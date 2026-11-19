"""The homepage search owns one durable run and automatically receives its results."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import sqlite3
import threading
import time
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import boss_runtime, capture, job_search, search_runs, shixiseng_runtime
from app.db import connect
from app.main import app


@pytest.fixture
def searches(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.sqlite'))
    monkeypatch.setattr(boss_runtime, 'supported_cities', lambda: {'北京': '101010100', '上海': '101020100'})
    monkeypatch.setattr(boss_runtime, 'readiness', lambda browser_mode='managed': {'ok': True, 'messages': []})
    monkeypatch.setattr(shixiseng_runtime, 'readiness', lambda: {'ok': True, 'messages': []})
    started, snapshots = [], {}

    def start(preferences, task_id=None):
        task_id = task_id or uuid.uuid4().hex
        platform = preferences['platform_order'][0]
        started.append(preferences)
        snapshots[task_id] = {'task_id': task_id, 'run_id': task_id, 'status': 'running',
                              'items': [], 'total': 0, 'error': '', 'finished_at': None,
                              'heartbeat': time.time(), 'candidates': [], 'receipts': [],
                              'platform_state': {platform: {'phase': 'login_required', 'message': '请在浏览器登录'}}}
        return {'task': {'id': task_id, 'mode': 'collect', 'status': 'running'}}

    def results(task_id, limit=200, offset=0):
        snapshot = snapshots[task_id]
        return {**snapshot, 'total': len(snapshot['items']), 'items': snapshot['items'][offset:offset+limit]}

    def stop(task_id):
        snapshots[task_id]['status'] = 'stopped'
        return results(task_id)

    def resume(task_id):
        snapshots[task_id]['platform_state']['boss'] = {'phase': 'search', 'message': '继续检索'}
        return results(task_id)

    def external_forbidden(*args, **kwargs):
        raise AssertionError('Homepage native searches must never require BossHunter')

    for runtime in (boss_runtime, shixiseng_runtime):
        monkeypatch.setattr(runtime, 'start_search', start)
        monkeypatch.setattr(runtime, 'result_page', results)
        monkeypatch.setattr(runtime, 'stop', stop)
    monkeypatch.setattr(boss_runtime, 'resume', resume)
    monkeypatch.setattr(search_runs.upstream, 'request_bosshunter', external_forbidden)
    with TestClient(app) as client:
        yield client, started, snapshots


def payload(**changes):
    return {'platform': 'boss', 'keywords': ['AI 产品', 'Agent 产品'], 'city': '北京',
            'max_pages': 1, 'max_jobs': 30, 'confirmed': True, 'request_id': uuid.uuid4().hex, **changes}


def begin(client, **changes):
    response = client.post('/api/job-search/start', json=payload(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def captured_job():
    return {'url': 'https://www.zhipin.com/job_detail/jobfixture123.html', 'title': 'AI 产品实习生',
            'company': '样例企业', 'city': '北京', 'jd': '负责产品调研，需要熟悉 Python。',
            'jd_available': True, 'observed_at': '2026-11-05T06:00:00Z', 'reused_snapshot': False}


def test_preview_preserves_phrases_deduplicates_and_creates_nothing(searches):
    client, started, _ = searches
    response = client.post('/api/job-search/preview', json={
        'keywords': ['  AI 产品  ', '', 'ai 产品', 'Agent 工具调用'], 'city': ' 北京 ', 'max_pages': 2})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['preferences']['keywords'] == ['AI 产品', 'Agent 工具调用']
    assert data['summary']['list_pages'] == 4 and data['ready']['ok']
    assert client.get('/api/capture/batches').json() == [] and started == []


@pytest.mark.parametrize('changes', [
    {'keywords': ['']}, {'keywords': ['词' * 81]}, {'keywords': [f'岗位{i}' for i in range(10)]},
    {'keywords': [f'岗位{i}' for i in range(5)], 'max_pages': 3},
    {'max_pages': 1.5}, {'max_jobs': 61}, {'city': '北京，上海'}, {'city': '不存在的城市'},
    {'keywords': ['AI\n产品']}, {'platform': 'arbitrary'}, {'browser_mode': 'arbitrary'},
])
def test_invalid_scope_never_starts_or_creates_a_batch(searches, changes):
    client, started, _ = searches
    response = client.post('/api/job-search/start', json=payload(**changes))
    assert response.status_code == 422, response.text
    assert started == [] and client.get('/api/capture/batches').json() == []


def test_retry_after_lost_response_reuses_same_run_and_checks_scope(searches):
    client, started, _ = searches
    request = payload()
    first = client.post('/api/job-search/start', json=request).json()
    second = client.post('/api/job-search/start', json=request).json()
    assert first['search_run']['id'] == second['search_run']['id']
    assert len(started) == len(client.get('/api/capture/batches').json()) == 1
    assert first['search_run']['preferences']['connector'] == 'native'
    assert first['request_id'] == request['request_id']
    changed = client.post('/api/job-search/start', json={**request, 'city': '上海'})
    assert changed.status_code == 409 and len(started) == 1


def test_current_get_restores_progress_without_touching_collector(searches, monkeypatch):
    client, _, _ = searches
    result = begin(client)
    def forbidden(*args, **kwargs):
        raise AssertionError('GET current must not refresh or ingest')
    monkeypatch.setattr(search_runs, 'refresh_run', forbidden)
    monkeypatch.setattr(search_runs, 'sync_results', forbidden)
    restored = client.get('/api/job-search/current').json()
    assert restored['batch_id'] == result['batch_id']
    assert restored['request_id'] == result['request_id']


def test_refresh_automatically_receives_results_without_confirming_jobs(searches):
    client, _, snapshots = searches
    result = begin(client)
    run = result['search_run']
    snapshot = snapshots[run['task_id']]
    snapshot.update(status='completed', items=[captured_job()])
    url = f"/api/job-search/{run['id']}/refresh"
    received = client.post(url, json={'confirmed': True})
    assert received.status_code == 200, received.text
    data = received.json()
    assert data['search_run']['synced_count'] == 1 and data['search_run']['status'] == 'completed'
    assert data['items'][0]['title'] == 'AI 产品实习生' and data['items'][0]['review_status'] == 'pending'
    assert client.get('/api/jobs').json() == []
    again = client.post(url, json={'confirmed': True}).json()
    assert len(again['items']) == 1 and again['batch']['total'] == 1


def test_login_resume_and_stop_keep_available_results(searches):
    client, _, snapshots = searches
    run = begin(client)['search_run']
    identifier = run['id']
    waiting = client.post(f'/api/job-search/{identifier}/refresh', json={'confirmed': True}).json()
    assert waiting['search_run']['platform_state']['phase'] == 'login_required'
    resumed = client.post(f'/api/job-search/{identifier}/resume', json={'confirmed': True})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['search_run']['platform_state']['phase'] == 'search'
    snapshots[run['task_id']]['items'] = [captured_job()]
    stopped = client.post(f'/api/job-search/{identifier}/stop', json={'confirmed': True}).json()
    assert stopped['search_run']['status'] == 'stopped' and len(stopped['items']) == 1


def test_fresh_policy_and_closed_jobs_survive_refresh_and_reopen(searches):
    client, started, snapshots = searches
    run = begin(client)['search_run']
    assert started[0]['force_refresh'] is True and started[0]['detail_cache'] == []
    closed = {'url': captured_job()['url'], 'title': '原岗位已关闭', 'company': '样例企业', 'checked_at': '2026-11-16T02:00:00Z'}
    snapshots[run['task_id']].update(status='completed', platform_state={'boss': {
        'phase': '检索结束', 'closed': 1, 'detail_attempts': 1, 'closed_jobs': [closed, {**closed, 'url': 'https://evil.invalid/job'}]}})
    received = client.post(f"/api/job-search/{run['id']}/refresh", json={'confirmed': True})
    assert received.status_code == 200, received.text
    state = received.json()['search_run']['platform_state']
    assert state['closed_jobs'] == [closed] and state['closed'] == 1
    assert state['detail_attempts'] == 1 and received.json()['items'] == []
    restored = client.get('/api/job-search/current').json()
    assert restored['search_run']['platform_state']['closed_jobs'] == [closed]
    assert client.get('/api/jobs').json() == []


def test_refresh_failure_retains_saved_results(searches, monkeypatch):
    client, _, snapshots = searches
    run = begin(client)['search_run']
    snapshots[run['task_id']]['items'] = [captured_job()]
    url = f"/api/job-search/{run['id']}/refresh"
    assert len(client.post(url, json={'confirmed': True}).json()['items']) == 1
    def missing(*args, **kwargs):
        raise RuntimeError('浏览器暂时失去连接')
    monkeypatch.setattr(boss_runtime, 'result_page', missing)
    result = client.post(url, json={'confirmed': True}).json()
    assert len(result['items']) == 1 and '失去连接' in result['error']


def test_unready_environment_preserves_empty_database_and_does_not_launch(searches, monkeypatch):
    client, started, _ = searches
    monkeypatch.setattr(boss_runtime, 'readiness', lambda browser_mode='managed': {'ok': False, 'messages': ['请安装 Chrome 或 Edge']})
    response = client.post('/api/job-search/start', json=payload())
    assert response.status_code == 503 and 'Chrome' in response.text
    assert started == [] and client.get('/api/capture/batches').json() == []


def test_all_keywords_are_saved_without_truncation(searches):
    client, _, _ = searches
    terms = ['关键词' + str(i) + '长' * 70 for i in range(9)]
    result = begin(client, keywords=terms)
    assert result['batch']['keyword'].splitlines() == terms
    assert result['search_run']['preferences']['keywords'] == terms


def test_home_cannot_control_a_search_from_another_entrypoint(searches):
    client, _, _ = searches
    run = begin(client)['search_run']
    with connect() as conn:
        prefs = run['preferences'];prefs.pop('entrypoint')
        conn.execute('UPDATE capture_search_runs SET preferences_json=? WHERE id=?', (json.dumps(prefs), run['id']))
    assert client.get('/api/job-search/current').json()['search_run'] is None
    response = client.post(f"/api/job-search/{run['id']}/stop", json={'confirmed': True})
    assert response.status_code == 404


def test_shixiseng_home_search_uses_existing_runtime(searches):
    client, started, _ = searches
    result = begin(client, platform='shixiseng')
    assert result['search_run']['platform'] == 'shixiseng'
    assert started[0]['platform_order'] == ['shixiseng']
    assert result['search_run']['preferences']['entrypoint'] == 'home'


def test_new_search_receives_completed_previous_results_before_replacing_it(searches):
    client, started, snapshots = searches
    first = begin(client)['search_run']
    snapshots[first['task_id']].update(status='completed', items=[captured_job()])
    second = begin(client, keywords=['模型评测'])['search_run']
    previous = search_runs.stored_run(first['id'])
    assert previous['status'] == 'completed' and previous['sync_offset'] == previous['synced_count'] == 1
    assert len(client.get(f"/api/capture/batches/{first['batch_id']}/items").json()) == 1
    assert client.get('/api/job-search/current').json()['search_run']['id'] == second['id']
    assert len(started) == 2


def test_failed_previous_result_sync_blocks_replacement_and_remains_reachable(searches, monkeypatch):
    client, started, snapshots = searches
    first = begin(client)['search_run']
    snapshots[first['task_id']].update(status='completed', items=[captured_job()])
    original = search_runs.sync_results
    def unavailable(*args, **kwargs):
        raise HTTPException(409, '批次已暂停，请先恢复')
    monkeypatch.setattr(search_runs, 'sync_results', unavailable)
    response = client.post('/api/job-search/start', json=payload(keywords=['模型评测']))
    assert response.status_code == 409
    current = client.get('/api/job-search/current').json()
    assert current['search_run']['id'] == first['id'] and current['search_run']['total'] == 1
    assert len(started) == len(client.get('/api/capture/batches').json()) == 1
    monkeypatch.setattr(search_runs, 'sync_results', original)
    recovered = client.post(f"/api/job-search/{first['id']}/refresh", json={'confirmed': True}).json()
    assert len(recovered['items']) == 1


def test_another_entrypoint_active_search_does_not_create_empty_retry_batches(searches):
    client, started, _ = searches
    first = begin(client)['search_run']
    with connect() as conn:
        preferences = first['preferences'];preferences.pop('entrypoint')
        conn.execute('UPDATE capture_search_runs SET preferences_json=? WHERE id=?',
                     (json.dumps(preferences), first['id']))
    request = payload()
    for _ in range(2):
        response = client.post('/api/job-search/start', json=request)
        assert response.status_code == 409
    assert len(started) == len(client.get('/api/capture/batches').json()) == 1


def test_stale_unassigned_native_start_recovers_without_external_panel(searches, monkeypatch):
    client, _, _ = searches
    first = begin(client)['search_run']
    with connect() as conn:
        conn.execute("UPDATE capture_search_runs SET task_id=NULL,status='starting',created_at=datetime('now','-10 minutes') WHERE id=?", (first['id'],))
    monkeypatch.setattr(boss_runtime, 'task_active', lambda task_id: False)
    refreshed = client.post(f"/api/job-search/{first['id']}/refresh", json={'confirmed': True}).json()
    assert refreshed['search_run']['status'] == 'failed'
    assert 'BossHunter' not in refreshed['search_run']['error']
    assert begin(client)['search_run']['id'] != first['id']


def test_recent_unassigned_start_is_not_released_by_another_window(searches):
    client, started, _ = searches
    first = begin(client)['search_run']
    with connect() as conn:
        conn.execute("UPDATE capture_search_runs SET task_id=NULL,status='starting' WHERE id=?", (first['id'],))
    refreshed = client.post(f"/api/job-search/{first['id']}/refresh", json={'confirmed': True}).json()
    assert refreshed['search_run']['status'] == 'starting'
    assert client.post('/api/job-search/start', json=payload()).status_code == 409
    assert len(started) == len(client.get('/api/capture/batches').json()) == 1


def test_platform_constraint_rolls_back_new_batch_without_deleting_existing_items(searches, monkeypatch):
    client, started, _ = searches
    first = begin(client)['search_run']
    saved = client.post(f"/api/capture/batches/{first['batch_id']}/items", json={'raw_text': '已有人工资料'})
    assert saved.status_code == 201, saved.text
    # Both contenders may pass the preflight idle check before either reserves
    # its slot. The database transaction must still reject the second batch.
    monkeypatch.setattr(search_runs, 'ensure_idle', lambda platform: None)
    preferences = job_search.SearchInput(keywords=['模型评测']).preferences()
    with pytest.raises(HTTPException) as error:
        search_runs.start_search(None, preferences, True, platform='boss', connector='native',
                                 new_batch=capture.BatchIn(platform='boss', keyword='模型评测', city='北京'))
    assert error.value.status_code == 409
    assert len(started) == len(client.get('/api/capture/batches').json()) == 1
    assert len(client.get(f"/api/capture/batches/{first['batch_id']}/items").json()) == 1


def test_unassigned_old_start_waits_for_a_live_local_collector(searches, monkeypatch):
    client, started, _ = searches
    first = begin(client)['search_run']
    with connect() as conn:
        conn.execute("UPDATE capture_search_runs SET task_id=NULL,status='starting',created_at=datetime('now','-10 minutes') WHERE id=?", (first['id'],))
    monkeypatch.setattr(boss_runtime, 'task_active', lambda task_id: True)
    refreshed = client.post(f"/api/job-search/{first['id']}/refresh", json={'confirmed': True}).json()
    assert refreshed['search_run']['status'] == 'starting'
    assert client.post('/api/job-search/start', json=payload()).status_code == 409
    assert len(started) == 1


def test_live_launch_owns_its_reserved_id_even_after_the_recovery_timeout(searches, monkeypatch):
    client, _, _ = searches
    entered, release = threading.Event(), threading.Event()
    original = boss_runtime.start_search
    reserved = {}
    def slow_start(preferences, task_id=None):
        with connect() as conn:
            row = conn.execute('SELECT * FROM capture_search_runs ORDER BY id DESC LIMIT 1').fetchone()
            assert task_id and row['task_id'] == task_id
            reserved.update(id=row['id'], task_id=task_id)
            conn.execute("UPDATE capture_search_runs SET created_at=datetime('now','-10 minutes') WHERE id=?", (row['id'],))
        entered.set()
        assert release.wait(5), 'test must release the controlled launch'
        return original(preferences, task_id=task_id)
    monkeypatch.setattr(boss_runtime, 'start_search', slow_start)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/api/job-search/start', json=payload())
        try:
            assert entered.wait(5), 'the runner should reach its launch boundary'
            refreshed = client.post(f"/api/job-search/{reserved['id']}/refresh", json={'confirmed': True}).json()
            assert refreshed['search_run']['status'] == 'starting'
            with pytest.raises(HTTPException) as conflict:
                search_runs.ensure_idle('boss')
            assert conflict.value.status_code == 409
        finally:
            release.set()
        result = pending.result(timeout=5)
    assert result.status_code == 200, result.text
    assert result.json()['search_run']['task_id'] == reserved['task_id']


def test_reserved_native_task_recovers_if_post_launch_database_write_fails(searches, monkeypatch):
    client, started, snapshots = searches
    original = search_runs.connect
    @contextmanager
    def fail_after_launch():
        with original() as conn:
            class Connection:
                def execute(self, statement, *args):
                    if statement.startswith('UPDATE capture_search_runs SET task_id='):
                        raise sqlite3.OperationalError('simulated interrupted status commit')
                    return conn.execute(statement, *args)
                def __getattr__(self, name):
                    return getattr(conn, name)
            yield Connection()
    monkeypatch.setattr(search_runs, 'connect', fail_after_launch)
    with pytest.raises(sqlite3.OperationalError, match='interrupted status commit'):
        client.post('/api/job-search/start', json=payload())
    monkeypatch.setattr(search_runs, 'connect', original)
    run = client.get('/api/job-search/current').json()['search_run']
    assert run['status'] == 'starting' and run['task_id'] in snapshots
    assert len(started) == 1
    snapshots[run['task_id']].update(status='completed', items=[captured_job()])
    recovered = client.post(f"/api/job-search/{run['id']}/refresh", json={'confirmed': True}).json()
    assert recovered['search_run']['status'] == 'completed' and len(recovered['items']) == 1


def test_current_exposes_older_unsynced_results_after_a_newer_run_finished(searches):
    client, _, snapshots = searches
    first = begin(client)['search_run']
    snapshots[first['task_id']]['status'] = 'completed'
    second = begin(client)['search_run']
    snapshots[second['task_id']]['status'] = 'completed'
    client.post(f"/api/job-search/{second['id']}/refresh", json={'confirmed': True})
    # Reproduce persisted state from before automatic handoff synchronization.
    snapshots[first['task_id']]['items'] = [captured_job()]
    search_runs.refresh_run(first['id'])
    current = client.get('/api/job-search/current').json()
    assert current['search_run']['id'] == first['id'] and current['search_run']['total'] == 1
    third = begin(client)['search_run']
    assert search_runs.stored_run(first['id'])['synced_count'] == 1
    assert client.get('/api/job-search/current').json()['search_run']['id'] == third['id']


@pytest.mark.parametrize('mode', ['chrome_use', 'managed'])
def test_home_browser_mode_is_persisted_and_used_by_every_preflight(searches, monkeypatch, mode):
    client, started, _ = searches
    checked = []
    def ready(browser_mode='managed'):
        checked.append(browser_mode)
        return {'ok': True, 'messages': []}
    monkeypatch.setattr(boss_runtime, 'readiness', ready)
    request = payload(browser_mode=mode)
    preview = client.post('/api/job-search/preview', json={key: value for key, value in request.items()
                                                       if key not in ('confirmed', 'request_id')})
    assert preview.status_code == 200, preview.text
    response = client.post('/api/job-search/start', json=request)
    assert response.status_code == 200, response.text
    assert checked == [mode, mode, mode]
    assert preview.json()['preferences']['browser_mode'] == mode
    assert response.json()['search_run']['preferences']['browser_mode'] == mode
    assert started[0]['browser_mode'] == mode
    changed = client.post('/api/job-search/start', json={**request, 'browser_mode': 'managed' if mode == 'chrome_use' else 'chrome_use'})
    assert changed.status_code == 409 and len(started) == 1


def test_home_defaults_to_managed_without_changing_shixiseng_preferences(searches):
    client, _, snapshots = searches
    first = begin(client)['search_run']
    assert first['preferences']['browser_mode'] == 'managed'
    snapshots[first['task_id']]['status'] = 'completed'
    second = begin(client, platform='shixiseng')['search_run']
    assert 'browser_mode' not in second['preferences']


def test_missing_chrome_use_connection_never_falls_back_to_managed(searches, monkeypatch):
    client, started, _ = searches
    checked = []
    def ready(browser_mode='managed'):
        checked.append(browser_mode)
        return {'ok': browser_mode == 'managed', 'messages': ['请连接 Chrome 扩展']}
    monkeypatch.setattr(boss_runtime, 'readiness', ready)
    response = client.post('/api/job-search/start', json=payload(browser_mode='chrome_use'))
    assert response.status_code == 503 and 'Chrome' in response.text
    assert checked == ['chrome_use']
    assert started == [] and client.get('/api/capture/batches').json() == []


def test_chrome_use_disconnect_during_start_preserves_failed_owner_without_fallback(searches, monkeypatch):
    client, started, _ = searches
    checked = []
    def ready(browser_mode='managed'):
        checked.append(browser_mode)
        return {'ok': len(checked) == 1, 'messages': ['Chrome 连接已断开，请重新连接']}
    monkeypatch.setattr(boss_runtime, 'readiness', ready)
    response = client.post('/api/job-search/start', json=payload(browser_mode='chrome_use'))
    assert response.status_code == 409
    current = client.get('/api/job-search/current').json()['search_run']
    assert current['status'] == 'failed' and current['preferences']['browser_mode'] == 'chrome_use'
    assert checked == ['chrome_use', 'chrome_use'] and started == []


def test_multiple_cities_search_every_keyword_in_every_city(searches):
    client, started, _ = searches
    keywords = ['财务分析实习', '机械设计实习']
    response = client.post('/api/job-search/preview', json={
        'keywords': keywords, 'cities': [' 北京 ', '上海', '北京', ''], 'max_pages': 2})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['preferences']['cities'] == ['北京', '上海']
    assert data['preferences']['max_list_attempts'] == 8
    assert data['summary']['list_pages'] == 8 and data['summary']['cities'] == ['北京', '上海']
    result = begin(client, keywords=keywords, cities=['北京', '上海'], max_pages=1)
    assert started[-1]['cities'] == ['北京', '上海']
    assert started[-1]['keywords'] == keywords
    batch = next(b for b in client.get('/api/capture/batches').json() if b['id'] == result['batch_id'])
    assert batch['city'] == '北京,上海' and '北京、上海' in batch['label']


@pytest.mark.parametrize('changes', [
    {'keywords': [f'岗位{i}' for i in range(5)], 'cities': ['北京', '上海']},
    {'keywords': ['AI 产品', 'Agent 产品', '模型评测'], 'cities': ['北京', '上海'], 'max_pages': 3},
    {'cities': ['北京', '不存在的城市']}, {'cities': ['北京，上海']}, {'cities': ['北\t京']},
])
def test_multiple_city_scope_is_checked_before_anything_starts(searches, changes):
    client, started, _ = searches
    response = client.post('/api/job-search/start', json=payload(**changes))
    assert response.status_code == 422, response.text
    assert started == [] and client.get('/api/capture/batches').json() == []


def test_city_suggestions_are_read_only_and_exclude_nationwide(searches, monkeypatch):
    client, started, _ = searches
    monkeypatch.setattr(boss_runtime, 'supported_cities', lambda: {'全国': '100010000', '北京': '101010100', '喀什地区': '101131200'})
    data = client.get('/api/job-search/cities').json()
    assert '北京' in data['common'] and data['boss'] == ['北京', '喀什地区']
    assert started == [] and client.get('/api/capture/batches').json() == []
