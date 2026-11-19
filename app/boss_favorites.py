"""Imported job targets and explicit BOSS favorite actions. No messaging routes."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import threading
import unicodedata
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from . import boss_runtime, local_collector
from .db import connect, database_path
from .shixiseng_runtime import resource_root, node_path, node_argument

router = APIRouter(prefix='/api/boss-favorites', tags=['BOSS favorites'])
_processes = {}
_lock = threading.Lock()
ACTIVE = local_collector.ACTIVE
TERMINAL = local_collector.TERMINAL
SnapshotMissingError = local_collector.SnapshotMissingError
ALIASES = {
    '岗位': 'title', '职位': 'title', '岗位名称': 'title', '职位名称': 'title', 'title': 'title', 'job_title': 'title',
    '企业': 'company', '公司': 'company', '公司名称': 'company', '企业名称': 'company', 'company': 'company',
    '城市': 'city', 'city': 'city', '地点': 'city',
    '链接': 'url', '岗位链接': 'url', '职位链接': 'url', '来源链接': 'url', 'url': 'url', 'source_url': 'url',
}


def key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


def canonical_url(value):
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.netloc not in ('www.zhipin.com', 'zhipin.com')
                or not re.fullmatch(r'/job_detail/[A-Za-z0-9_~\-]{1,128}\.html', url.path)):
            return ''
        return 'https://www.zhipin.com' + url.path
    except ValueError:
        return ''


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ImportInput(StrictModel):
    text: str = Field(min_length=1, max_length=262144)
    format: Literal['text', 'csv', 'txt'] = 'text'
    city: str = Field(default='北京', max_length=60)


class CreateInput(ImportInput):
    name: str = Field(default='我的岗位清单', min_length=1, max_length=120)
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    browser_mode: Literal['managed', 'chrome_use'] = 'managed'
    internship_only: bool = True


class Selection(StrictModel):
    ids: list[int] = Field(max_length=300)


class StartInput(StrictModel):
    action: Literal['match', 'favorite']
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    target_ids: list[int] = Field(default_factory=list, max_length=100)
    similar: bool = False


def parse_import(data: ImportInput):
    if len(data.text.encode('utf-8')) > 262144 or '\x00' in data.text:
        raise HTTPException(400, '文件最多 256 KiB，不能包含空字符；请分批导入 UTF-8 文本')
    cities = boss_runtime.supported_cities()
    if data.city not in cities:
        raise HTTPException(400, '请选择 BOSS 支持的默认城市')
    value = data.text.lstrip('\ufeff').strip('\r\n')
    delimiter = '\t' if '\t' in value else ','
    try:
        if data.format == 'txt' and '\t' not in value:
            rows = [[line.strip()] for line in value.splitlines() if line.strip()]
        else:
            rows = [row for row in csv.reader(io.StringIO(value), delimiter=delimiter, strict=True) if any(cell.strip() for cell in row)]
    except (csv.Error, UnicodeError):
        raise HTTPException(400, 'CSV 格式无法读取，请检查引号，或另存为 CSV UTF-8 后重试')
    if not rows or len(rows) > 101:
        raise HTTPException(400, '每份清单请填写 1–100 个岗位；较大的文件请分批导入')
    mapped = [ALIASES.get(cell.strip().casefold(), '') for cell in rows[0]]
    has_header = bool({'title', 'url'} & set(mapped))
    if has_header and len([x for x in mapped if x]) != len(set(x for x in mapped if x)):
        raise HTTPException(400, '表头中存在重复的岗位、企业、城市或链接列，请保留各一列')
    if not has_header and any(len(row) != 1 for row in rows):
        raise HTTPException(400, '多列内容请添加表头：企业、岗位、城市、链接；也可以只粘贴一列岗位名称')
    fields = mapped if has_header else ['title']
    width = len(rows[0]) if has_header else 1
    if len(rows) - int(has_header) > 100:
        raise HTTPException(400, '每份清单最多 100 个岗位，请分批导入')
    items, duplicates, errors, seen = [], [], [], set()
    for number, row in enumerate(rows[1:] if has_header else rows, 2 if has_header else 1):
        if len(row) != width:
            errors.append({'line': number, 'message': '列数与表头不一致，请补空列或检查逗号和引号'})
            continue
        record = {name: row[i].strip() for i, name in enumerate(fields) if name}
        if not has_header and record.get('title', '').startswith('https://'):
            record['url'], record['title'] = record['title'], ''
        record = {name: record.get(name, '') for name in ('title', 'company', 'city', 'url')}
        record['city'] = record['city'] or data.city
        problem = ''
        if not record['title'] and not record['url']:
            problem = '至少填写岗位名称或 BOSS 岗位链接'
        if any(len(record[name]) > limit or re.search(r'[\x00-\x1f\x7f]', record[name])
               for name, limit in (('title', 180), ('company', 140), ('city', 60), ('url', 2000))):
            problem = '岗位名称最多 180 字、企业 140 字；字段不能包含换行或控制字符'
        if record['city'] not in cities:
            problem = '城市无法识别，请使用一个 BOSS 支持的城市名称'
        if record['url']:
            original = record['url']
            record['url'] = canonical_url(original)
            if not record['url']:
                problem = '链接必须是 https://www.zhipin.com/job_detail/…html 形式的 BOSS 岗位链接'
        if problem:
            errors.append({'line': number, 'message': problem})
            continue
        identity = record['url'] or tuple(key(record[name]) for name in ('company', 'title', 'city'))
        if identity in seen:
            duplicates.append({'line': number, 'title': record['title'], 'message': '与清单前面的条目重复，将合并'})
            continue
        seen.add(identity)
        items.append(record)
    digest = hashlib.sha256(json.dumps([data.text, data.format, data.city], ensure_ascii=False).encode()).hexdigest()
    return {'items': items, 'duplicates': duplicates, 'errors': errors, 'preview_hash': digest,
            'ignored_columns': [rows[0][i] for i, name in enumerate(mapped) if has_header and not name]}


def batch_row(conn, batch_id):
    row = conn.execute('SELECT * FROM boss_favorite_batches WHERE id=?', (batch_id,)).fetchone()
    if not row:
        raise HTTPException(404, '岗位收藏清单不存在')
    return dict(row)


def public_run(row):
    if not row:
        return None
    value = dict(row)
    output = json.loads(value.pop('output_json'))
    value.pop('preferences_json')
    value.pop('request_json')
    value.pop('output_hash')
    value['progress'] = output.get('platform_state', {}).get('boss', {})
    return value


def ensure_idle(conn):
    if conn.execute("SELECT 1 FROM boss_favorite_runs WHERE status IN ('starting','pending','running','stopping','unknown')").fetchone():
        raise HTTPException(409, '已有 BOSS 岗位查找或收藏任务正在运行，请在岗位收藏中查看或停止')


@router.get('/options')
def options():
    return {'cities': list(boss_runtime.supported_cities()), 'max_targets': 100, 'max_lists': 12, 'max_details': 60}


@router.get('/template')
def template():
    return Response('\ufeff企业,岗位,城市,链接\r\n,产品运营实习,北京,\r\n', media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="boss-job-list-template.csv"'})


@router.post('/preview')
def preview(data: ImportInput):
    return parse_import(data)


@router.post('/batches', status_code=201)
def create(data: CreateInput):
    parsed = parse_import(data)
    if data.preview_hash != parsed['preview_hash']:
        raise HTTPException(409, '输入内容已修改，请重新预览当前清单')
    if parsed['errors'] or not parsed['items']:
        raise HTTPException(400, '请先修正预览中的格式问题；本次没有保存任何条目')
    if not data.name.strip():
        raise HTTPException(400, '请填写清单名称')
    with connect() as conn:
        batch_id = conn.execute('INSERT INTO boss_favorite_batches(name,city,browser_mode,internship_only) VALUES(?,?,?,?)',
                                (data.name.strip(), data.city, data.browser_mode, int(data.internship_only))).lastrowid
        conn.executemany('INSERT INTO boss_favorite_targets(batch_id,position,title,company,city,source_url) VALUES(?,?,?,?,?,?)',
                         [(batch_id, i, row['title'], row['company'], row['city'], row['url']) for i, row in enumerate(parsed['items'], 1)])
    return get_batch(batch_id)


@router.get('/batches')
def batches():
    with connect() as conn:
        return [dict(row) for row in conn.execute('''SELECT b.*,COUNT(t.id) total FROM boss_favorite_batches b
          LEFT JOIN boss_favorite_targets t ON t.batch_id=b.id GROUP BY b.id ORDER BY b.id DESC LIMIT 200''')]


@router.get('/batches/{batch_id}')
def get_batch(batch_id: int):
    with connect() as conn:
        batch = batch_row(conn, batch_id)
        targets = []
        for row in conn.execute('SELECT * FROM boss_favorite_targets WHERE batch_id=? ORDER BY position', (batch_id,)):
            item = dict(row)
            item['candidates'] = [{k: v for k, v in dict(candidate).items() if k != 'jd'} for candidate in conn.execute(
                'SELECT * FROM boss_favorite_candidates WHERE target_id=? ORDER BY id', (item['id'],))]
            targets.append(item)
        run = public_run(conn.execute('SELECT * FROM boss_favorite_runs WHERE batch_id=? ORDER BY id DESC LIMIT 1', (batch_id,)).fetchone())
    return {'batch': batch, 'targets': targets, 'run': run}


@router.put('/batches/{batch_id}/selection')
def select(batch_id: int, data: Selection):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        batch_row(conn, batch_id)
        ensure_idle(conn)
        rows = conn.execute('''SELECT c.id,c.availability FROM boss_favorite_candidates c
          JOIN boss_favorite_targets t ON t.id=c.target_id WHERE t.batch_id=?''', (batch_id,)).fetchall()
        allowed = {row['id'] for row in rows if row['availability'] == 'active'}
        if not set(data.ids) <= allowed:
            raise HTTPException(409, '部分候选已关闭或尚未读取，请重新查看当前结果')
        conn.executemany('UPDATE boss_favorite_candidates SET selected=? WHERE id=?', [(int(row['id'] in data.ids), row['id']) for row in rows])
    return get_batch(batch_id)


def run_directory(task_id):
    if not re.fullmatch(r'[a-f0-9]{32}', task_id):
        raise HTTPException(400, '收藏任务标识无效')
    return database_path().parent / 'boss-favorite-runs' / task_id


def runner_command(*args):
    node = node_path()
    script = resource_root() / 'tools' / 'boss_favorites_runner.mjs'
    if not script.is_file():
        raise RuntimeError('桌面包缺少 BOSS 收藏组件，请更新完整应用')
    return [node, node_argument(script, node), *args]


def launch(task_id, preferences):
    directory = run_directory(task_id)
    directory.mkdir(parents=True, exist_ok=False)
    _processes[task_id] = local_collector.launch_runner(task_id, directory, preferences, platform='boss', label='BOSS 岗位收藏',
        node_path=node_path, node_argument=node_argument, command=runner_command,
        browser_profile=database_path().parent / 'BossRadarChrome' if preferences['browser_mode'] == 'managed' else None)


@router.post('/batches/{batch_id}/runs')
def start(batch_id: int, data: StartInput):
    from . import search_runs
    encoded_request = data.model_dump_json()
    with _lock:
        with connect() as conn:
            previous = conn.execute('SELECT * FROM boss_favorite_runs WHERE task_id=?', (data.request_id,)).fetchone()
            if previous:
                if previous['batch_id'] != batch_id or previous['request_json'] != encoded_request:
                    raise HTTPException(409, '启动请求对应的清单或操作已变化，请重新发起')
                return {'run': public_run(previous)}
        search_runs.ensure_idle('boss')
        with connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            ensure_idle(conn)
            if conn.execute("SELECT 1 FROM capture_search_runs WHERE platform='boss' AND status IN ('starting','pending','running','stopping','unknown')").fetchone():
                raise HTTPException(409, '已有 BOSS 检索正在运行，请先等待或停止')
            batch = batch_row(conn, batch_id)
            if data.action == 'match':
                targets = [dict(row) for row in conn.execute('SELECT * FROM boss_favorite_targets WHERE batch_id=? ORDER BY position', (batch_id,))
                           if row['id'] in data.target_ids or not data.target_ids and row['state'] == 'pending']
                if data.target_ids and set(data.target_ids) - {row['id'] for row in targets}:
                    raise HTTPException(404, '指定条目不属于当前清单')
                chosen, lists = [], 0
                for row in targets:
                    needs_list = data.similar or not row['source_url']
                    if len(chosen) >= 60 or needs_list and lists >= 12:
                        break
                    if data.similar:
                        row['source_url'] = ''
                    chosen.append(row)
                    lists += int(needs_list)
            else:
                selected = [dict(row) for row in conn.execute('''SELECT c.*,t.id target_id FROM boss_favorite_candidates c
                  JOIN boss_favorite_targets t ON t.id=c.target_id WHERE t.batch_id=? AND c.selected=1
                  AND c.availability IN ('active','unread','failed') ORDER BY c.id LIMIT 300''', (batch_id,))]
                unique = {}
                for candidate in selected:
                    unique.setdefault(candidate['url'], candidate)
                chosen = list(unique.values())[:60]
            if not chosen:
                raise HTTPException(400, '没有待查找的条目' if data.action == 'match' else '请先选择要收藏的在招岗位')
            preferences = {'action': data.action, 'targets': chosen, 'browser_mode': batch['browser_mode'],
                           'internship_only': bool(batch['internship_only']), 'collection_mode': data.action}
            conn.execute('INSERT INTO boss_favorite_runs(task_id,batch_id,action,request_json,preferences_json) VALUES(?,?,?,?,?)',
                         (data.request_id, batch_id, data.action, encoded_request, json.dumps(preferences, ensure_ascii=False)))
        try:
            runner_command('--self-test')  # verifies packaged paths without opening a browser
            launch(data.request_id, preferences)
        except (OSError, RuntimeError, ValueError) as exc:
            with connect() as conn:
                conn.execute("UPDATE boss_favorite_runs SET status='failed',error=?,finished_at=datetime('now') WHERE task_id=?",
                             (str(exc)[:1000], data.request_id))
            raise HTTPException(503, '无法启动岗位收藏；清单已保存，请检查采集环境后重试') from exc
    return {'run': get_batch(batch_id)['run']}


def validate_output(value, task_id):
    if (not isinstance(value, dict) or value.get('task_id') != task_id or value.get('status') not in ACTIVE | TERMINAL
            or not isinstance(value.get('items'), list) or value['items']
            or not isinstance(value.get('results', []), list) or len(value.get('results', [])) > 60
            or not isinstance(value.get('platform_state'), dict) or type(value.get('heartbeat')) not in (int, float)
            or not math.isfinite(value['heartbeat']) or value['heartbeat'] <= 0):
        raise RuntimeError('收藏任务快照不完整，未修改已保存的结果')


validate_snapshot = validate_output


def read_snapshot(task_id):
    directory = run_directory(task_id)
    value = local_collector.read_snapshot_file(directory / 'result.json', task_id, validate_output, _processes.get(task_id), 'BOSS 收藏')
    return local_collector.refresh_snapshot(value, directory, _processes.get(task_id))


def apply_output(conn, row, value):
    prefs = json.loads(row['preferences_json'])
    targets = {target['id']: target for target in prefs['targets']}
    checked = []
    result_ids = set()
    for result in value.get('results', []):
        if not isinstance(result, dict) or result.get('id') not in targets or result['id'] in result_ids:
            raise RuntimeError('收藏结果与本次清单不符')
        result_ids.add(result['id'])
        if row['action'] == 'match':
            if result.get('state') not in {'matched', 'needs_choice', 'not_found', 'closed', 'failed'}:
                raise RuntimeError('岗位查找状态不兼容')
            candidates = result.get('candidates', [])
            if not isinstance(candidates, list) or len(candidates) > 3:
                raise RuntimeError('候选数量不兼容')
        else:
            candidates = [result]
            if result.get('url') != targets[result['id']]['url']:
                raise RuntimeError('收藏结果的岗位链接发生变化')
        for candidate in candidates:
            if (not isinstance(candidate, dict) or canonical_url(candidate.get('url', '')) != candidate.get('url')
                    or candidate.get('availability') not in {'active', 'closed', 'unread', 'failed'}
                    or candidate.get('favorite_status', 'not_checked') not in {'not_checked', 'already_saved', 'saved', 'failed', 'unknown'}
                    or any(not isinstance(candidate.get(k, ''), str) or len(candidate.get(k, '')) > limit
                           for k, limit in (('title', 2000), ('company', 2000), ('city', 2000), ('salary', 2000), ('jd', 100000), ('checked_at', 60), ('error', 2000), ('match_reason', 2000)))):
                raise RuntimeError('收藏候选格式不兼容')
        checked.append((result, candidates))
    # Validate the whole snapshot before writing any candidate or selection.
    latest = conn.execute('SELECT MAX(id) FROM boss_favorite_runs WHERE batch_id=?', (row['batch_id'],)).fetchone()[0]
    if latest != row['id']:
        return
    for result, candidates in checked:
        if row['action'] == 'match':
            conn.execute('UPDATE boss_favorite_targets SET state=?,error=? WHERE id=?', (result['state'], str(result.get('error', ''))[:2000], result['id']))
            conn.execute("UPDATE boss_favorite_candidates SET selected=0,availability='unread' WHERE target_id=?", (result['id'],))
            for c in candidates:
                conn.execute('''INSERT INTO boss_favorite_candidates(target_id,url,title,company,city,salary,jd,checked_at,availability,selected,match_reason,favorite_status,error)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(target_id,url) DO UPDATE SET
                  title=excluded.title,company=excluded.company,city=excluded.city,salary=excluded.salary,jd=excluded.jd,
                  checked_at=excluded.checked_at,availability=excluded.availability,selected=excluded.selected,
                  match_reason=excluded.match_reason,favorite_status=excluded.favorite_status,error=excluded.error''',
                  (result['id'], c['url'], c.get('title', ''), c.get('company', ''), c.get('city', ''), c.get('salary', ''), c.get('jd', ''),
                   c.get('checked_at', ''), c['availability'], int(c.get('selected') is True and c['availability'] == 'active'), c.get('match_reason', ''), c.get('favorite_status', 'not_checked'), c.get('error', '')))
        else:
            c = candidates[0]
            conn.execute('''UPDATE boss_favorite_candidates SET title=?,company=?,city=?,salary=?,jd=?,checked_at=?,availability=?,
              favorite_status=?,favorite_at=CASE WHEN ? IN ('saved','already_saved','unknown') THEN ? ELSE favorite_at END,
              error=?,selected=CASE WHEN ? IN ('saved','already_saved') OR ?='closed' THEN 0 ELSE selected END
              WHERE url=? AND target_id IN (SELECT id FROM boss_favorite_targets WHERE batch_id=?)''',
              (c.get('title', ''), c.get('company', ''), c.get('city', ''), c.get('salary', ''), c.get('jd', ''), c.get('checked_at', ''), c['availability'],
               c.get('favorite_status', 'not_checked'), c.get('favorite_status', ''), c.get('checked_at', ''), c.get('error', ''), c.get('favorite_status', ''), c['availability'], c['url'], row['batch_id']))
            if c['availability'] == 'closed':
                conn.execute('''UPDATE boss_favorite_targets SET state=CASE WHEN EXISTS(
                  SELECT 1 FROM boss_favorite_candidates x WHERE x.target_id=boss_favorite_targets.id AND x.availability='active')
                  THEN 'needs_choice' ELSE 'closed' END WHERE batch_id=? AND id IN (
                  SELECT target_id FROM boss_favorite_candidates WHERE url=?)''', (row['batch_id'], c['url']))


@router.post('/runs/{task_id}/refresh')
def refresh(task_id: str):
    with connect() as conn:
        row = conn.execute('SELECT * FROM boss_favorite_runs WHERE task_id=?', (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, '岗位收藏任务不存在')
        try:
            directory = run_directory(task_id)
            value = local_collector.read_snapshot_file(directory / 'result.json', task_id, validate_output, _processes.get(task_id), 'BOSS 收藏')
            value = local_collector.refresh_snapshot(value, directory, _processes.get(task_id))
            encoded = json.dumps(value, ensure_ascii=False)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            if digest != row['output_hash']:
                apply_output(conn, row, value)
                conn.execute('UPDATE boss_favorite_runs SET output_json=?,output_hash=?,status=?,error=?,finished_at=? WHERE task_id=?',
                             (encoded, digest, value['status'], str(value.get('error', ''))[:2000], value.get('finished_at'), task_id))
        except (OSError, ValueError, RuntimeError) as exc:
            if not _processes.get(task_id) or _processes[task_id].poll() is not None:
                conn.execute("UPDATE boss_favorite_runs SET status='failed',error=?,finished_at=datetime('now') WHERE task_id=?", (str(exc)[:2000], task_id))
            else:
                raise HTTPException(503, '结果暂不可读，已保存的清单和结果保留；请刷新重试') from exc
    return get_batch(row['batch_id'])


@router.post('/runs/{task_id}/{action}')
def control(task_id: str, action: Literal['stop', 'resume']):
    with connect() as conn:
        row = conn.execute('SELECT * FROM boss_favorite_runs WHERE task_id=?', (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, '岗位收藏任务不存在')
        if row['status'] not in ACTIVE:
            raise HTTPException(409, '任务已经结束，请刷新后重试剩余条目')
        directory = run_directory(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        if action == 'resume':
            phase = json.loads(row['output_json']).get('platform_state', {}).get('boss', {}).get('phase')
            if phase not in ('login_required', 'verification_required'):
                raise HTTPException(409, '当前任务没有等待登录或验证')
        (directory / ('stop' if action == 'stop' else 'resume')).write_text(action, encoding='ascii')
        if action == 'stop':
            conn.execute("UPDATE boss_favorite_runs SET status='stopping' WHERE task_id=?", (task_id,))
    return get_batch(row['batch_id'])


@router.post('/from-job/{job_id}')
def from_job(job_id: int):
    import uuid
    with connect() as conn:
        job = conn.execute('SELECT j.*,c.name company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?', (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, '岗位不存在')
        source = next((canonical_url(row['url']) for row in conn.execute('SELECT url FROM job_sources WHERE job_id=? ORDER BY id', (job_id,)) if canonical_url(row['url'])), '')
        if not source:
            raise HTTPException(400, '此岗位没有 BOSS 岗位链接，请在来源中补充链接')
        ensure_idle(conn)
        city = job['city'] if job['city'] in boss_runtime.supported_cities() else '全国'
        batch_id = conn.execute('INSERT INTO boss_favorite_batches(name,city) VALUES(?,?)', ('收藏 · ' + job['title'][:100], city)).lastrowid
        target = conn.execute('INSERT INTO boss_favorite_targets(batch_id,position,title,company,city,source_url) VALUES(?,1,?,?,?,?)',
                              (batch_id, job['title'], job['company'], city, source)).lastrowid
        conn.execute('INSERT INTO boss_favorite_candidates(target_id,url,title,company,city,selected) VALUES(?,?,?,?,?,1)',
                     (target, source, job['title'], job['company'], city))
    start(batch_id, StartInput(action='favorite', request_id=uuid.uuid4().hex))
    return get_batch(batch_id)
