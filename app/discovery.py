"""Exact source identities, query evidence and measured collection costs."""
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException

from .db import connect
from .company_profile import valid_company_source

router = APIRouter(prefix='/api/capture', tags=['discovery'])
PARSER_VERSION = 2
STATES = {'pending', 'complete', 'cached', 'incomplete', 'failed', 'filtered', 'closed'}


def stamp(value=None):
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    if not isinstance(value, str):
        raise ValueError('发现时间格式无效')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec='milliseconds')
    except ValueError as exc:
        raise ValueError('发现时间格式无效') from exc


def identity(url, platform='shixiseng'):
    if not isinstance(url, str) or len(url) > 2000:
        raise ValueError('岗位链接无效')
    parsed = urlsplit(url)
    if platform != 'shixiseng' or parsed.scheme != 'https' or parsed.hostname != 'www.shixiseng.com' or parsed.netloc not in {'www.shixiseng.com', 'www.shixiseng.com:443'}:
        raise ValueError('岗位链接来源不受支持')
    match = re.fullmatch(r'/intern/(inn_[A-Za-z0-9_-]+)', parsed.path)
    if not match:
        raise ValueError('岗位身份无效')
    return match[1], f'https://www.shixiseng.com{parsed.path}'


def text(value, limit=1000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('候选信息格式无效')
    return value


def hit(value):
    if not isinstance(value, dict):
        raise ValueError('命中记录无效')
    page = value.get('page', 1)
    if type(page) is not int or not 1 <= page <= 1000:
        raise ValueError('命中页码无效')
    return (text(value.get('keyword', ''), 160), text(value.get('city', ''), 100), page)


def ingest_snapshot(conn, run, data):
    """Validate the entire untrusted snapshot before writing; caller owns transaction."""
    if run.get('platform', 'boss') != 'shixiseng':
        return
    candidates, items, receipts = (data.get(k, []) for k in ('candidates', 'items', 'receipts'))
    if any(not isinstance(v, list) for v in (candidates, items, receipts)) or len(candidates) > 400 or len(items) > 400 or len(receipts) > 1000:
        raise ValueError('发现快照超过允许范围')
    snapshot_time = data.get('updated_at') or data.get('heartbeat')
    now = stamp(snapshot_time) if isinstance(snapshot_time, str) else None
    checked_candidates = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError('候选记录无效')
        sid, url = identity(candidate.get('url'))
        fields = [text(candidate.get(k, ''), 2000) for k in ('title', 'company', 'city')]
        state = candidate.get('detail_state', 'pending')
        if state not in STATES or not isinstance(candidate.get('hits', []), list) or len(candidate.get('hits', [])) > 100:
            raise ValueError('候选状态或命中无效')
        seen = stamp(candidate['observed_at']) if candidate.get('observed_at') else now
        checked_at = stamp(candidate['last_detail_at']) if candidate.get('last_detail_at') else None
        checked_candidates.append((sid, url, fields, state, [hit(h) for h in candidate.get('hits', [])], seen, checked_at))
    checked_items = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('详情记录无效')
        sid, url = identity(item.get('url'))
        jd = text(item.get('jd', ''), 100000)
        observed = stamp(item.get('last_detail_at') or item.get('observed_at'))
        reused = item.get('reused_snapshot', False)
        if type(reused) is not bool:
            raise ValueError('缓存标记无效')
        complete = bool(jd.strip() and item.get('jd_available') is True and not re.search('[\ue000-\uf8ff\ufffd]', jd))
        fields = [text(item.get(k, ''), 2000) for k in ('title', 'company', 'city')]
        seen = stamp(item['observed_at']) if item.get('observed_at') else observed
        checked_items.append((sid, url, fields, item, observed, seen, reused, complete))
    checked_receipts = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError('访问回执无效')
        rid = text(receipt.get('id', ''), 160)
        kind, status = receipt.get('kind'), receipt.get('status')
        if not rid or kind not in {'list', 'detail', 'cache', 'company'} or status not in {'started', 'completed', 'failed', 'unknown'}:
            raise ValueError('访问回执类型无效')
        if not isinstance(receipt.get('started_at'), str):
            raise ValueError('访问回执缺少开始时间')
        stamp(receipt['started_at'])
        if receipt.get('finished_at'):
            stamp(receipt['finished_at'])
        if kind in {'detail', 'cache'}:
            identity(receipt.get('url'))
        elif kind == 'company' and not valid_company_source(receipt.get('url'), 'shixiseng', company_only=True):
            raise ValueError('公司页来源无效')
        if 'keyword' in receipt:
            text(receipt['keyword'], 160)
        if 'city' in receipt:
            text(receipt['city'], 100)
        encoded = json.dumps(receipt, ensure_ascii=False)
        if len(encoded) > 10000:
            raise ValueError('访问回执过长')
        old = conn.execute('SELECT kind,payload_json FROM discovery_receipts WHERE run_id=? AND receipt_id=?', (run['id'], rid)).fetchone()
        if old:
            previous = json.loads(old['payload_json'])
            if old['kind'] != kind or any(previous.get(k) != receipt.get(k) for k in ('url', 'keyword', 'city', 'page', 'started_at')):
                raise ValueError('访问回执身份发生变化')
        checked_receipts.append((rid, kind, status, encoded))

    def source(sid, url, fields, seen):
        previous = conn.execute("SELECT last_seen_at FROM discovery_sources WHERE platform='shixiseng' AND source_id=?", (sid,)).fetchone()
        seen = seen or (previous['last_seen_at'] if previous else stamp())
        if previous and seen < previous['last_seen_at']:
            fields = ['', '', '']
        conn.execute('''INSERT INTO discovery_sources(platform,source_id,url,title,company,city,first_seen_at,last_seen_at)
          VALUES('shixiseng',?,?,?,?,?,?,?) ON CONFLICT(platform,source_id) DO UPDATE SET
          last_seen_at=MAX(last_seen_at,excluded.last_seen_at),
          title=CASE WHEN excluded.title!='' THEN excluded.title ELSE title END,
          company=CASE WHEN excluded.company!='' THEN excluded.company ELSE company END,
          city=CASE WHEN excluded.city!='' THEN excluded.city ELSE city END''', (sid, url, *fields, seen, seen))
        return conn.execute("SELECT id FROM discovery_sources WHERE platform='shixiseng' AND source_id=?", (sid,)).fetchone()['id']

    for sid, url, fields, state, hits, seen, checked_at in checked_candidates:
        source_id = source(sid, url, fields, seen)
        if state == 'closed' and checked_at:
            conn.execute('''UPDATE discovery_sources SET last_detail_at=?,detail_state='closed' WHERE id=?
              AND (last_detail_at IS NULL OR last_detail_at<=?)''', (checked_at, source_id, checked_at))
        for keyword, city, page in hits or [('', '', 1)]:
            conn.execute('''INSERT INTO discovery_hits(run_id,source_id,keyword,city,page,detail_state) VALUES(?,?,?,?,?,?)
              ON CONFLICT(run_id,source_id,keyword,city,page) DO UPDATE SET detail_state=CASE
              WHEN discovery_hits.detail_state IN ('complete','cached') AND excluded.detail_state='pending'
              THEN discovery_hits.detail_state ELSE excluded.detail_state END''', (run['id'], source_id, keyword, city, page, state))
    for sid, url, fields, item, observed, seen, reused, complete in checked_items:
        previous = conn.execute("SELECT last_detail_at,last_seen_at FROM discovery_sources WHERE platform='shixiseng' AND source_id=?", (sid,)).fetchone()
        safe_fields = fields if previous is None or not reused and (not previous['last_detail_at'] or observed >= previous['last_detail_at']) else ['', '', '']
        source_id = source(sid, url, safe_fields, previous['last_seen_at'] if reused and previous else seen)
        if not reused:
            fingerprint = hashlib.sha256(json.dumps({k: item.get(k, '') for k in ('url', 'title', 'company', 'city', 'jd', 'jd_available', 'salary', 'duration', 'onsite_days', 'eligibility', 'posted_at', 'deadline', 'employment_type')}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            conn.execute('''INSERT INTO discovery_details VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,fingerprint,parser_version)
              DO UPDATE SET observed_at=MAX(observed_at,excluded.observed_at),item_json=CASE WHEN excluded.observed_at>=observed_at THEN excluded.item_json ELSE item_json END''', (source_id, fingerprint, json.dumps(item, ensure_ascii=False), observed, PARSER_VERSION, int(complete)))
            conn.execute('''UPDATE discovery_sources SET last_detail_at=?,detail_state=? WHERE id=?
              AND (last_detail_at IS NULL OR last_detail_at<=?)''', (observed, 'complete' if complete else 'incomplete', source_id, observed))
        conn.execute('UPDATE discovery_hits SET detail_state=? WHERE run_id=? AND source_id=?', ('cached' if reused and complete else 'complete' if complete else 'incomplete', run['id'], source_id))
    for rid, kind, status, encoded in checked_receipts:
        conn.execute('''INSERT INTO discovery_receipts VALUES(?,?,?,?,?) ON CONFLICT(run_id,receipt_id)
          DO UPDATE SET status=excluded.status,payload_json=excluded.payload_json
          WHERE discovery_receipts.status IN ('started','unknown') ''', (run['id'], rid, kind, status, encoded))


def build_preferences(preferences, batch_id=None, conn=None):
    # Keep saved JD versions for history, never as a substitute for a new visit.
    # Ignore stale flags/cache supplied by older pages or resumed preferences.
    return {**preferences, 'force_refresh': True, 'detail_cache': []}


def receipt_costs(conn, run_id):
    costs = {'list_attempts': 0, 'detail_attempts': 0, 'cache_hits': 0, 'unknown_attempts': 0}
    for row in conn.execute('SELECT kind,status FROM discovery_receipts WHERE run_id=?', (run_id,)):
        if row['kind'] == 'cache':
            costs['cache_hits'] += row['status'] == 'completed'
        else:
            key = row['kind'] + '_attempts'
            costs[key] = costs.get(key, 0) + 1
        costs['unknown_attempts'] += row['status'] in {'started', 'unknown'}
    return costs


def public_run_info(conn, run_id):
    rows = conn.execute('''SELECT s.*,h.detail_state hit_state,h.keyword,h.city query_city,h.page FROM discovery_hits h
      JOIN discovery_sources s ON s.id=h.source_id WHERE h.run_id=? ORDER BY s.id,h.keyword,h.city,h.page''', (run_id,)).fetchall()
    values = {}
    for row in rows:
        entry = values.setdefault(row['id'], {k: row[k] for k in ('id', 'url', 'title', 'company', 'city', 'last_detail_at')})
        entry.setdefault('observed_at', row['last_seen_at'])
        entry.setdefault('detail_state', row['hit_state'])
        entry.setdefault('hits', []).append({'keyword': row['keyword'], 'city': row['query_city'], 'page': row['page']})
    costs = receipt_costs(conn, run_id)
    candidates = list(values.values())
    run = conn.execute('SELECT platform_state_json FROM capture_search_runs WHERE id=?', (run_id,)).fetchone()
    reported = bool(run and json.loads(run['platform_state_json']).get('ledger_reported') is True)
    return {'costs': costs, 'costs_reported': reported, 'candidates': candidates, 'pending_count': sum(c['detail_state'] in {'pending', 'failed', 'incomplete'} for c in candidates)}


def pending_candidates(conn, batch_id):
    rows = conn.execute('''SELECT DISTINCT h.run_id FROM discovery_hits h JOIN capture_search_runs r ON r.id=h.run_id
      WHERE r.batch_id=? ORDER BY h.run_id''', (batch_id,)).fetchall()
    candidates = {}
    states = {}
    for row in rows:
        for candidate in public_run_info(conn, row['run_id'])['candidates']:
            old = candidates.setdefault(candidate['url'], {**candidate, 'hits': []})
            old['last_detail_at'] = candidate['last_detail_at']
            for h in candidate['hits']:
                if h not in old['hits']:
                    old['hits'].append(h)
                states.setdefault(candidate['url'], {})[(h['keyword'], h['city'], h['page'])] = candidate['detail_state']
    pending = []
    for url, candidate in candidates.items():
        unresolved = [s for s in states[url].values() if s in {'pending', 'failed', 'incomplete'}]
        if unresolved:
            candidate['detail_state'] = unresolved[-1]
            pending.append(candidate)
    return pending[:400]


@router.get('/search-runs/{search_id}/discovery')
def run_discovery(search_id: int):
    with connect() as conn:
        if not conn.execute('SELECT id FROM capture_search_runs WHERE id=?', (search_id,)).fetchone():
            raise HTTPException(404, '搜索任务不存在')
        return public_run_info(conn, search_id)


@router.get('/batches/{batch_id}/pending-candidates')
def batch_pending(batch_id: int):
    with connect() as conn:
        if not conn.execute('SELECT id FROM capture_batches WHERE id=?', (batch_id,)).fetchone():
            raise HTTPException(404, '采集批次不存在')
        return {'candidates': pending_candidates(conn, batch_id)}
