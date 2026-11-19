"""Read-only connector integration using a fake runtime and temporary database."""
import sqlite3
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect, init_db, SCHEMA_VERSION
from app.external_collectors import normalize_shixiseng_job


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.db'))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def runtime(monkeypatch):
    tasks = {}
    def start(preferences):
        task_id = f'sxs-{len(tasks)+1}'
        tasks[task_id] = {'task_id': task_id, 'run_id': 'run-' + task_id,
                          'status': 'running', 'items': [], 'total': 0,
                          'platform_state': {'shixiseng': {'seen': 0}}, 'error': '', 'finished_at': None}
        return {'task': {'id': task_id, 'mode': 'collect', 'status': 'running'}}
    def page(task_id, limit=200, offset=0):
        value = tasks[task_id]
        return {**value, 'total': len(value['items']), 'items': value['items'][offset:offset+limit]}
    def stop(task_id):
        tasks[task_id]['status'] = 'stopped'
        return page(task_id)
    service = SimpleNamespace(capabilities=lambda: {'version': 1, 'matched_results': True},
                              readiness=lambda: {'ok': True, 'checks': [], 'messages': []},
                              start_search=start, result_page=page, stop=stop,
                              task_active=lambda task_id: any(v['status'] == 'running' for v in tasks.values()))
    monkeypatch.setitem(sys.modules, 'app.shixiseng_runtime', service)
    # A previous test may already have loaded the module on the package.
    import app as package
    monkeypatch.setattr(package, 'shixiseng_runtime', service, raising=False)
    return tasks, service


def batch(client, platform='shixiseng', **extra):
    response = client.post('/api/capture/batches', json={'platform': platform, 'keyword': 'AI产品', 'city': '北京', **extra})
    assert response.status_code == 201
    return response.json()['id']


def start(client, batch_id):
    response = client.post('/api/capture/connectors/shixiseng/start-collect', json={'batch_id': batch_id, 'confirmed': True})
    assert response.status_code == 200, response.text
    return response.json()['search_run']


def job(n, **extra):
    return {'title': '产品实习', 'company': '测试企业', 'city': '北京',
            'url': f'https://www.shixiseng.com/intern/inn_test{n}', 'jd': '负责产品需求和设计', **extra}


def test_preview_limits_and_platform_validation(client, runtime):
    bid = batch(client)
    url = '/api/capture/connectors/shixiseng/search-preview'
    result = client.post(url, json={'batch_id': bid, 'max_pages': 2})
    assert result.json()['preferences']['max_pages'] == {'shixiseng': 2}
    assert result.json()['max_jobs'] == result.json()['preferences']['max_jobs'] == 60
    assert not runtime[0]
    assert client.post(url, json={'batch_id': batch(client, 'boss')}).status_code == 409
    assert client.post(url, json={'batch_id': bid, 'internship_only': False}).status_code == 422
    assert client.post(url, json={'batch_id': bid, 'max_pages': 4}).status_code == 422
    bid = batch(client, keyword='a,b,c', city='北京,上海,杭州')
    assert client.post(url, json={'batch_id': bid, 'max_pages': 2}).status_code == 400


def test_partial_results_pagination_stop_and_review(client, runtime):
    bid = batch(client)
    run = start(client, bid)
    assert run['platform'] == 'shixiseng'
    runtime[0][run['task_id']]['items'] = [job(1), job(2, jd=''), job(3, jd='内容\ue123')]
    url = f"/api/capture/search-runs/{run['id']}/sync"
    result = client.post(url, json={'confirmed': True, 'limit': 2}).json()
    assert result['created'] == 2 and result['missing_jd'] == 1 and result['has_more']
    result = client.post(f"/api/capture/search-runs/{run['id']}/stop", json={'confirmed': True}).json()
    assert result['search_run']['status'] == 'stopped'
    result = client.post(url, json={'confirmed': True, 'limit': 2}).json()
    assert result['created'] == 1 and result['missing_jd'] == 1 and not result['has_more']
    items = client.get(f'/api/capture/batches/{bid}/items?run_id={run["id"]}').json()
    assert len(items) == 3 and all(v['status'] == 'pending' for v in items)
    assert sum(v['draft']['jd_available'] is False for v in items) == 2
    another = start(client, batch(client))
    assert client.get(f'/api/capture/batches/{bid}/items?run_id={another["id"]}').status_code == 404


def test_active_tasks_isolated_between_platforms(client, runtime):
    boss_bid = batch(client, 'boss')
    with connect() as conn:
        conn.execute("INSERT INTO capture_search_runs(batch_id,preferences_json,task_id,status,platform) VALUES(?, '{}','boss-owned','running','boss')", (boss_bid,))
    run = start(client, batch(client))
    assert run['platform'] == 'shixiseng'
    response = client.post('/api/capture/connectors/shixiseng/start-collect', json={'batch_id': batch(client), 'confirmed': True})
    assert response.status_code == 409
    with connect() as conn:
        assert conn.execute("SELECT status FROM capture_search_runs WHERE task_id='boss-owned'").fetchone()[0] == 'running'


def test_empty_results_and_readiness_failure(client, runtime):
    run = start(client, batch(client))
    runtime[0][run['task_id']].update(status='completed')
    result = client.post(f"/api/capture/search-runs/{run['id']}/sync", json={'confirmed': True}).json()
    assert result['scanned'] == 0 and result['search_run']['status'] == 'completed'
    runtime[1].readiness = lambda: {'ok': False, 'checks': [], 'messages': ['请完成登录']}
    response = client.post('/api/capture/connectors/shixiseng/start-collect', json={'batch_id': batch(client), 'confirmed': True})
    assert response.status_code == 409
    assert len(runtime[0]) == 1


def test_local_start_failure_does_not_leave_an_unknown_reservation(client, runtime):
    def failed_start(preferences):
        raise RuntimeError('Node.js 启动失败')
    runtime[1].start_search = failed_start
    response = client.post('/api/capture/connectors/shixiseng/start-collect',
                           json={'batch_id': batch(client), 'confirmed': True})
    assert response.status_code == 503
    with connect() as conn:
        assert conn.execute('SELECT status FROM capture_search_runs').fetchone()[0] == 'failed'


def test_url_and_garbled_jd_validation():
    for url in ('https://evil.test/intern/inn_abc', 'https://www.shixiseng.com.evil.test/intern/inn_abc', 'https://user@www.shixiseng.com/intern/inn_abc', 'https://www.shixiseng.com:123/intern/inn_abc', 'http://www.shixiseng.com/intern/inn_abc', 'https://www.shixiseng.com/jobs'):
        assert normalize_shixiseng_job(job(1, url=url)) is None
    assert normalize_shixiseng_job(job(1, jd='含有\ue012乱码'))['jd_available'] is False


def test_schema8_migration_keeps_boss_ownership(tmp_path, monkeypatch):
    path = tmp_path / 'old.db'
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(path))
    init_db()
    with sqlite3.connect(path) as conn:
        conn.execute('DROP INDEX ix_one_active_search')
        conn.execute('ALTER TABLE capture_search_runs DROP COLUMN platform')
        conn.execute("INSERT INTO capture_batches(label,platform,keyword,city) VALUES('old','boss','AI','北京')")
        conn.execute("INSERT INTO capture_search_runs(batch_id,preferences_json,task_id,status) VALUES(1,'{}','original','running')")
        conn.execute('PRAGMA user_version=8')
    init_db()
    with connect() as conn:
        row = conn.execute('SELECT platform,task_id,status FROM capture_search_runs').fetchone()
        assert tuple(row) == ('boss', 'original', 'running')
        assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_foreign_results_and_failed_partial_are_kept_separate(client, runtime):
    bid = batch(client)
    run = start(client, bid)
    value = runtime[0][run['task_id']]
    value.update(status='failed', error='页面要求验证')
    value['items'] = [job(1)]
    result = client.post(f"/api/capture/search-runs/{run['id']}/sync", json={'confirmed': True}).json()
    assert result['created'] == 1 and result['search_run']['status'] == 'failed'
    assert result['search_run']['error'] == '页面要求验证'
    # A different task cannot be substituted even if it has plausible jobs.
    value['task_id'] = 'foreign-task'
    response = client.post(f"/api/capture/search-runs/{run['id']}/sync", json={'confirmed': True})
    assert response.status_code == 409
    with connect() as conn:
        assert conn.execute('SELECT sync_offset FROM capture_search_runs WHERE id=?', (run['id'],)).fetchone()[0] == 1


def test_font_obfuscation_and_metadata_are_separate_from_jd():
    assert normalize_shixiseng_job(job(1, title='产品\ue010')) is None
    assert normalize_shixiseng_job(job(1, company='企业\ufffd')) is None
    raw = job(1, degree='本科', posted_at='2026-10-09', deadline='2026-12-01')
    value = normalize_shixiseng_job(raw)
    assert value['eligibility'] == '本科'
    assert value['posted_at'] == '2026-10-09'
    assert value['deadline'] == '2026-12-01'
    assert value['raw_text'] == raw['jd']


def test_platform_state_is_unwrapped_and_metadata_reaches_draft(client, runtime):
    bid = batch(client)
    run = start(client, bid)
    assert run['preferences']['max_jobs'] == 60
    value = runtime[0][run['task_id']]
    value['platform_state'] = {'shixiseng': {'status': 'running', 'seen': 60, 'reason_code': 'limit_reached', 'message': '本次最多60条'}}
    value['items'] = [job(1, degree='本科', deadline='2026-12-01')]
    result = client.post(f"/api/capture/search-runs/{run['id']}/sync", json={'confirmed': True}).json()
    assert result['search_run']['platform_state'] == value['platform_state']['shixiseng']
    draft = client.get(f'/api/capture/batches/{bid}/items').json()[0]['draft']
    assert draft['eligibility'] == '本科' and draft['deadline'] == '2026-12-01'


def test_configurable_detail_attempt_cap(client, runtime):
    bid = batch(client)
    for invalid in (0, 61, True, 1.5):
        assert client.post('/api/capture/connectors/shixiseng/search-preview', json={
            'batch_id': bid, 'max_jobs': invalid}).status_code == 422
    preview = client.post('/api/capture/connectors/shixiseng/search-preview', json={
        'batch_id': bid, 'max_jobs': 3}).json()
    assert preview['max_jobs'] == preview['preferences']['max_jobs'] == 3
    result = client.post('/api/capture/connectors/shixiseng/start-collect', json={
        'batch_id': bid, 'confirmed': True, 'max_jobs': 3})
    assert result.status_code == 200
    assert result.json()['search_run']['preferences']['max_jobs'] == 3


def test_direct_batch_read_beyond_recent_list(client):
    first = batch(client)
    for index in range(100):
        batch(client, keyword=f'query{index}')
    assert first not in [b['id'] for b in client.get('/api/capture/batches').json()]
    assert client.get(f'/api/capture/batches/{first}').json()['id'] == first
