"""Durable ownership of explicit, read-only BOSS searches and their results."""
import hashlib
import json
import re
import sqlite3
import threading
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from .db import connect
from . import external_collectors as upstream
from .local_collector import local_platform

router = APIRouter(prefix='/api/capture', tags=['search'])
ACTIVE = {'starting', 'pending', 'running', 'stopping', 'unknown'}
TERMINAL = {'completed', 'completed_with_shortage', 'completed_with_errors', 'stopped', 'failed'}
_starting_runs: set[int] = set()
_start_guard = threading.Lock()


def request(path, post=None):
    try:
        result = upstream.request_bosshunter(path, post=post)
    except upstream.ConnectorError as exc:
        status = 409 if exc.http_status and 400 <= exc.http_status < 500 else 503
        raise HTTPException(status, upstream.safe_message(exc)) from exc
    if not isinstance(result, dict) or result.get('error') and 'items' not in result:
        raise HTTPException(409, upstream.upstream_error(result))
    return result


def public_run(row):
    if row is None:
        return None
    value = dict(row)
    value['preferences'] = json.loads(value.pop('preferences_json'))
    value['platform_state'] = json.loads(value.pop('platform_state_json'))
    value['internship_only'] = bool(value['internship_only'])
    return value


def latest_run(batch_id):
    with connect() as conn:
        return public_run(conn.execute('SELECT * FROM capture_search_runs WHERE batch_id=? ORDER BY id DESC LIMIT 1', (batch_id,)).fetchone())


def stored_run(search_id):
    with connect() as conn:
        row = conn.execute('SELECT * FROM capture_search_runs WHERE id=?', (search_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '搜索任务不存在')
    return public_run(row)


def safe_platform_state(state, platform='boss'):
    if not isinstance(state, dict):
        return {}
    boss = state.get(platform, state)
    if not isinstance(boss, dict):
        return {}
    result = {}
    for key in ('status', 'phase', 'keyword', 'city', 'reason_code', 'message', 'cleanup_message'):
        if isinstance(boss.get(key), str):
            result[key] = upstream.safe_message(boss[key])
    for key in ('seen', 'new', 'duplicate', 'filtered', 'closed', 'detail_attempts', 'list_attempts', 'parse_failed', 'save_failed', 'page', 'max_pages', 'tabs_opened', 'tabs_closed', 'company_attempts', 'company_with_headcount', 'company_lookup_failed'):
        if type(boss.get(key)) is int and boss[key] >= 0:
            result[key] = boss[key]
    if isinstance(boss.get('closed_jobs'), list):
        # Only supported source URLs may become actionable links in the UI.
        from . import boss_runtime, shixiseng_runtime
        valid_url = boss_runtime._canonical_url if platform == 'boss' else shixiseng_runtime._canonical_url
        result['closed_jobs'] = [{key: upstream.safe_message(job.get(key, '')) for key in ('url', 'title', 'company', 'checked_at')}
                                 for job in boss['closed_jobs'][:400]
                                 if isinstance(job, dict) and valid_url(job.get('url'))]
    if boss.get('browser_mode') in ('chrome_use', 'managed'):
        result['browser_mode'] = boss['browser_mode']
    if boss.get('cleanup_status') in ('pending', 'complete', 'incomplete'):
        result['cleanup_status'] = boss['cleanup_status']
    return result


def result_page(run, limit=1, offset=0):
    task_id = run.get('task_id')
    if not task_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', task_id):
        raise HTTPException(409, '任务尚未确认启动，请检查本地服务；不要重复发起搜索')
    collector = local_platform(run)
    if collector:
        data = runtime_call('result_page', task_id, collector=collector, limit=limit, offset=offset)
    else:
        data = request(f'/api/radar/tasks/{task_id}/results?limit={limit}&offset={offset}')
    if data.get('task_id') != task_id or not isinstance(data.get('items'), list):
        raise HTTPException(409, '上游结果与搜索任务不符，未导入任何资料')
    if run.get('run_id') and data.get('run_id') != run['run_id']:
        raise HTTPException(409, '采集运行记录已变更，已停止同步以保护结果归属')
    if data.get('status') not in ACTIVE | TERMINAL or type(data.get('total')) is not int or data['total'] < 0:
        raise HTTPException(409, '上游任务状态格式不兼容')
    return data


def update_run(search_id, data):
    run = stored_run(search_id)
    platform = run.get('platform', 'boss')
    state = safe_platform_state(data.get('platform_state'), platform)
    with connect() as conn:
        if platform == 'shixiseng':
            from . import discovery
            try:
                discovery.ingest_snapshot(conn, run, data)
            except ValueError as exc:
                raise HTTPException(409, f'采集参考资料格式不兼容：{exc}') from exc
            if isinstance(data.get('receipts'), list):
                state['ledger_reported'] = True
                state['costs'] = discovery.public_run_info(conn, search_id)['costs']
        conn.execute('''UPDATE capture_search_runs SET run_id=COALESCE(?,run_id),status=?,total=?,
          error=?,platform_state_json=?,finished_at=? WHERE id=?''', (
            data.get('run_id'), data['status'], data['total'], upstream.safe_message(data.get('error')),
            json.dumps(state, ensure_ascii=False),
            data.get('finished_at'), search_id))
    return stored_run(search_id)


@router.get('/search-runs/{search_id}')
def refresh_run(search_id: int):
    run = stored_run(search_id)
    collector = local_platform(run)
    if collector and run['status'] in ('starting', 'unknown'):
        with _start_guard:
            if search_id in _starting_runs:
                return run
        if not run.get('task_id'):
            # Older local starts reserved a database row before assigning their
            # task. Preserve a live launch, but let an abandoned row recover.
            if _start_expired(search_id) and not runtime_call('task_active', None, collector=collector):
                with connect() as conn:
                    conn.execute("""UPDATE capture_search_runs SET status='failed',error=?,finished_at=datetime('now')
                        WHERE id=? AND task_id IS NULL AND status IN ('starting','unknown')""",
                        ('本机检索启动已中断，未发现仍在运行的采集进程；可以重新检索。', search_id))
                return stored_run(search_id)
            return run
    if run.get('task_id'):
        try:
            return update_run(search_id, result_page(run))
        except HTTPException as exc:
            if not collector or exc.status_code != 410:
                raise
            if collector == 'boss' and run['status'] == 'starting' and not _start_expired(search_id):
                # A second process can observe the committed reservation before
                # its runner writes the initial snapshot.
                return run
            with connect() as conn:
                conn.execute("""UPDATE capture_search_runs SET status='failed',error=?,
                    finished_at=COALESCE(finished_at,datetime('now')) WHERE id=?""",
                    (str(exc.detail), search_id))
            return stored_run(search_id)
    return run


def _start_expired(search_id):
    with connect() as conn:
        return bool(conn.execute("SELECT (julianday('now')-julianday(created_at))*86400 > 90 FROM capture_search_runs WHERE id=?",
                                 (search_id,)).fetchone()[0])


def ensure_idle(platform):
    """Check the shared platform slot; the unique index remains the race guard."""
    with connect() as conn:
        active = conn.execute("SELECT id FROM capture_search_runs WHERE platform=? AND status IN ('starting','pending','running','stopping','unknown') LIMIT 1",
                              (platform,)).fetchone()
    if active and refresh_run(active['id'])['status'] in ACTIVE:
        raise HTTPException(409, '已有搜索尚未结束，请先刷新任务状态或停止该搜索')


def capabilities(*, require_fresh=False):
    value = request('/api/radar/capabilities')
    if value.get('version') != 1 or value.get('matched_results') is not True:
        raise HTTPException(409, '请使用项目中的 BOSS 链路启动脚本，开启本次结果记录功能')
    if require_fresh and value.get('fresh_details') is not True:
        raise HTTPException(409, '此 BossHunter 服务仍会复用旧详情；请更新并重启 BOSS 链路，或改用独立采集窗口重新检索')
    return {'version': 1, 'matched_results': True, 'fresh_details': value.get('fresh_details') is True}


def start_search(batch_id, preferences, internship_only, platform='boss', connector=None, *, new_batch=None):
    from .capture import _batch, _create_batch
    preferences = dict(preferences)
    if new_batch is not None and (batch_id is not None or new_batch.platform != platform):
        raise HTTPException(422, '新采集批次的归属无效')
    # Select the owner before reserving a run. Persist the choice so historical
    # tasks never change transport when a new default is introduced.
    if connector not in (None, 'native', 'bosshunter') or (connector and platform != 'boss'):
        raise HTTPException(422, '采集连接器选择无效')
    if connector == 'native':
        preferences.update(connector='native', internship_only=internship_only, force_refresh=True, detail_cache=[])
        from . import boss_runtime
        try:
            boss_runtime.validate_preferences(preferences)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise HTTPException(503, upstream.safe_message(exc)) from exc
    elif platform == 'shixiseng':
        from . import discovery
        preferences = discovery.build_preferences(preferences, batch_id)
    elif platform == 'boss':
        # A payload marker alone cannot redirect an existing BossHunter API.
        preferences.pop('connector', None)
    # Refresh an exact owned task before reserving a new one, even when a user
    # returns in a new tab after collection completed while Radar was closed.
    ensure_idle(platform)
    native_task_id = uuid4().hex if connector == 'native' else None
    # Reserve ownership before any upstream mutation. The database constraint
    # also covers a second browser tab or a second Radar process.
    try:
        with connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if platform == 'boss':
                from .boss_favorites import ensure_idle as ensure_favorites_idle
                ensure_favorites_idle(conn)
            if new_batch is not None:
                batch_id = _create_batch(conn, new_batch)['id']
            batch = _batch(conn, batch_id)
            if batch['status'] != 'open' or batch['platform'] != platform:
                raise HTTPException(409, '请先打开该采集批次')
            if platform == 'shixiseng':
                from . import execution_budget
                preferences = execution_budget.constrain(conn, batch_id, preferences)
            search_id = conn.execute('''INSERT INTO capture_search_runs
                (batch_id,preferences_json,internship_only,platform,task_id) VALUES(?,?,?,?,?)''',
                (batch_id, json.dumps(preferences, ensure_ascii=False), int(internship_only), platform, native_task_id)).lastrowid
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, '已有搜索尚未结束，请先刷新任务状态或停止该搜索') from exc
    task_requested = False
    with _start_guard:
        _starting_runs.add(search_id)
    try:
        if platform == 'shixiseng':
            runtime_call('capabilities')
            ready = runtime_call('readiness')
            if ready.get('ok') is not True:
                raise HTTPException(409, '；'.join(ready.get('messages', [])) or '实习僧连接器尚未就绪')
            task_requested = True
            from . import discovery
            response = runtime_call('start_search', discovery.build_preferences(preferences, batch_id))
        elif connector == 'native':
            runtime_call('capabilities', collector='boss')
            ready = runtime_call('readiness', collector='boss', browser_mode=preferences.get('browser_mode', 'managed'))
            if ready.get('ok') is not True:
                raise HTTPException(409, '；'.join(ready.get('messages', [])) or 'BOSS 采集组件尚未就绪')
            task_requested = True
            response = runtime_call('start_search', preferences, collector='boss', task_id=native_task_id)
        else:
            capabilities(require_fresh=True)
            task_requested = True
            response = request('/api/radar/search/start', {'preferences': preferences,
                               'internship_only': internship_only, 'confirmed': True})
        task = upstream.public_task(response.get('task'))
        expected_mode = preferences.get('collection_mode', 'collect') if platform == 'shixiseng' or connector == 'native' else 'collect'
        if not task or task.get('mode') != expected_mode or task.get('status') not in ACTIVE | TERMINAL or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', task.get('id') or ''):
            raise HTTPException(503, '上游未返回可确认的只读任务，请检查面板')
        if native_task_id and task['id'] != native_task_id:
            raise HTTPException(503, '本机采集任务与已保存的归属不符，已停止启动')
        with connect() as conn:
            conn.execute('UPDATE capture_search_runs SET task_id=?,status=? WHERE id=?', (task['id'], task['status'], search_id))
    except HTTPException as exc:
        # An HTTP timeout can occur after the collector started. Preserve an
        # unknown state rather than silently starting a second task on retry.
        ambiguous = platform == 'boss' and connector != 'native' and task_requested and exc.status_code == 503
        with connect() as conn:
            conn.execute('UPDATE capture_search_runs SET status=?,error=?,finished_at=CASE WHEN ? THEN NULL ELSE datetime(\'now\') END WHERE id=?',
                         ('unknown' if ambiguous else 'failed', str(exc.detail), int(ambiguous), search_id))
            if not task_requested:
                conn.execute('UPDATE capture_search_runs SET platform_state_json=? WHERE id=?',
                             (json.dumps({'no_access': True, 'ledger_reported': True}), search_id))
        raise
    finally:
        with _start_guard:
            _starting_runs.discard(search_id)
    return {'started': True, 'batch_id': batch_id, 'preferences': preferences,
            'settings_persist': platform == 'boss' and connector != 'native',
            'upstream': {'task': task}, 'search_run': stored_run(search_id)}


class SyncResults(BaseModel):
    confirmed: Literal[True]
    limit: int = Field(default=200, ge=1, le=500)


@router.post('/search-runs/{search_id}/sync')
def sync_results(search_id: int, body: SyncResults):
    from .capture import _batch, _enqueue_from_adapter
    run = stored_run(search_id)
    with connect() as conn:
        if _batch(conn, run['batch_id'])['status'] != 'open':
            raise HTTPException(409, '批次已暂停，请重新打开后同步本次结果')
    offset = run['sync_offset']
    data = result_page(run, body.limit, offset)
    if len(data['items']) > body.limit or offset + len(data['items']) > data['total']:
        raise HTTPException(409, '上游结果分页不一致，未更新同步进度')
    normalizer = upstream.normalize_shixiseng_job if run.get('platform') == 'shixiseng' else upstream.normalize_bosshunter_job
    candidates = [normalizer(value) for value in data['items']]
    created = duplicate = incomplete = rejected = 0
    with connect() as conn:
        if _batch(conn, run['batch_id'])['status'] != 'open':
            raise HTTPException(409, '批次已暂停')
        current = conn.execute('SELECT sync_offset FROM capture_search_runs WHERE id=?', (search_id,)).fetchone()
        if current['sync_offset'] != offset:
            raise HTTPException(409, '本次结果已被其他窗口同步，请刷新')
        if run.get('platform') == 'shixiseng':
            from . import discovery
            try:
                discovery.ingest_snapshot(conn, run, data)
            except ValueError as exc:
                raise HTTPException(409, f'采集参考资料格式不兼容：{exc}') from exc
        for candidate in candidates:
            if candidate is None:
                rejected += 1
                continue
            added = _enqueue_from_adapter(conn, run['batch_id'], candidate)
            created += int(added)
            duplicate += int(not added)
            incomplete += int(not candidate['jd_available'])
            fingerprint = hashlib.sha256(json.dumps([candidate['raw_text'], candidate['source_url']], ensure_ascii=False).encode()).hexdigest()
            item_id = conn.execute('SELECT id FROM capture_items WHERE batch_id=? AND fingerprint=?', (run['batch_id'], fingerprint)).fetchone()['id']
            conn.execute('''INSERT OR IGNORE INTO capture_search_results
                (search_run_id,item_id,observed_at,reused_snapshot) VALUES(?,?,?,?)''',
                (search_id, item_id, candidate['observed_at'], int(candidate['reused_snapshot'])))
        next_offset = offset + len(data['items'])
        count = conn.execute('SELECT COUNT(*) FROM capture_search_results WHERE search_run_id=?', (search_id,)).fetchone()[0]
        conn.execute('UPDATE capture_search_runs SET sync_offset=?,synced_count=? WHERE id=?', (next_offset, count, search_id))
    latest = update_run(search_id, data)
    return {'created': created, 'already_exists': duplicate, 'missing_jd': incomplete,
            'rejected': rejected, 'scanned': len(data['items']), 'next_offset': next_offset,
            'has_more': next_offset < data['total'], 'search_run': latest}


class ConfirmStop(BaseModel):
    confirmed: Literal[True]


@router.post('/search-runs/{search_id}/resolve-start')
def resolve_uncertain_start(search_id: int, body: ConfirmStop):
    run = stored_run(search_id)
    if run['task_id'] or run['status'] not in ('starting', 'unknown'):
        raise HTTPException(409, '只有未确认启动的搜索可以解除等待')
    if run['status'] == 'starting':
        with connect() as conn:
            stale = conn.execute("SELECT (julianday('now')-julianday(created_at))*86400 > 90 FROM capture_search_runs WHERE id=?", (search_id,)).fetchone()[0]
        if not stale:
            raise HTTPException(409, '启动请求仍在处理中，请等待响应；超过 90 秒仍无任务时可重新检查')
    collector = local_platform(run)
    state = ({'task': runtime_call('task_active', None, collector=collector)} if collector else request('/api/agent/state'))
    if state.get('task'):
        raise HTTPException(409, '采集仍有任务运行，请先停止搜索' if collector else '上游仍有任务运行，请先在 BossHunter 面板检查并结束该任务')
    with connect() as conn:
        conn.execute("UPDATE capture_search_runs SET status='failed',error=?,finished_at=datetime('now') WHERE id=? AND task_id IS NULL AND status IN ('starting','unknown')",
                     ('启动未确认；已确认连接器无运行任务，可重新检索。已同步资料保留在本机。', search_id))
    return {'search_run': stored_run(search_id)}


@router.post('/search-runs/{search_id}/stop')
def stop_search(search_id: int, body: ConfirmStop):
    run = stored_run(search_id)
    if local_platform(run) and not run.get('task_id'):
        run = refresh_run(search_id)
    if run['status'] not in ACTIVE:
        return {'search_run': run, 'already_stopped': True}
    if not run['task_id']:
        raise HTTPException(409, '本机检索仍在启动，请稍后刷新状态' if local_platform(run)
                            else '任务未确认启动，需在 BossHunter 面板检查后处理')
    collector = local_platform(run)
    if collector:
        try:
            data = runtime_call('stop', run['task_id'], collector=collector)
        except HTTPException as exc:
            if exc.status_code != 410:
                raise
            return {'search_run': refresh_run(search_id)}
        # Validate the exact task and run before updating durable ownership.
        if data.get('task_id') != run['task_id'] or (run.get('run_id') and data.get('run_id') != run['run_id']):
            raise HTTPException(409, '停止响应不属于本次搜索')
        return {'search_run': update_run(search_id, data)}
    state = request('/api/agent/state')
    active = state.get('task')
    if not active:
        return {'search_run': refresh_run(search_id)}
    if active.get('id') != run['task_id'] or active.get('mode') != 'collect':
        raise HTTPException(409, '当前上游任务属于其他操作，未停止该任务')
    request(f"/api/workbench/task/{run['task_id']}/stop", {})
    with connect() as conn:
        conn.execute("UPDATE capture_search_runs SET status='stopping' WHERE id=?", (search_id,))
    return {'search_run': stored_run(search_id)}


@router.post('/search-runs/{search_id}/resume')
def resume_search(search_id: int, body: ConfirmStop):
    run = stored_run(search_id)
    if local_platform(run) != 'boss' or not run.get('task_id'):
        raise HTTPException(409, '只有应用内的 BOSS 检索可以在登录或验证后继续')
    # The process checks its current visible page. This signal never solves a
    # challenge, opens a different task or discards already collected results.
    try:
        from . import boss_runtime
        data = boss_runtime.resume(run['task_id'])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except boss_runtime.SnapshotMissingError:
        return {'search_run': refresh_run(search_id)}
    except (RuntimeError, OSError) as exc:
        raise HTTPException(503, upstream.safe_message(exc)) from exc
    if data.get('task_id') != run['task_id'] or (run.get('run_id') and data.get('run_id') != run['run_id']):
        raise HTTPException(409, '继续响应不属于本次搜索')
    return {'search_run': update_run(search_id, data)}


def readiness():
    data = request('/api/workbench/preflight?mode=collect')
    checks = []
    for check in data.get('checks', []):
        if isinstance(check, dict):
            checks.append({key: upstream.safe_message(check.get(key)) for key in ('id', 'status', 'message')})
    return {'ok': data.get('ok') is True, 'checks': checks,
            'messages': [upstream.safe_message(v) for v in data.get('messages', []) if isinstance(v, str)]}


def runtime_call(method, *args, collector='shixiseng', **kwargs):
    if collector == 'boss':
        from . import boss_runtime as runtime
    elif collector == 'shixiseng':
        from . import shixiseng_runtime as runtime
    else:
        raise HTTPException(422, '本地采集连接器无效')
    try:
        return getattr(runtime, method)(*args, **kwargs)
    except HTTPException:
        raise
    except getattr(runtime, 'SnapshotMissingError', ()) as exc:
        raise HTTPException(410, str(exc)) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(503, upstream.safe_message(exc)) from exc
