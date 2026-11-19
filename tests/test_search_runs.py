"""Exact search ownership, restart-safe sync and manual review; no real BOSS access."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import external_collectors
from app.db import connect


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.sqlite'))
    with TestClient(app) as value:
        yield value


@pytest.fixture
def upstream(monkeypatch):
    state = {'tasks': {}, 'items': {}, 'active': None, 'calls': [], 'offline': False, 'fresh_details': True}
    def call(path, *, post=None):
        state['calls'].append((path, post))
        if state['offline']:
            raise external_collectors.ConnectorError('服务断开')
        if path == '/api/radar/capabilities':
            return {'version': 1, 'matched_results': True, 'fresh_details': state['fresh_details']}
        if path.startswith('/api/radar/search-options'):
            return {'internship_only': post['internship_only'] if post else True}
        if path == '/api/agent/config/apply':
            return {'success': True}
        if path == '/api/agent/config/preview':
            return {'preferences': post['preferences']}
        if path == '/api/radar/search/start':
            task_id = f"task-{len(state['tasks'])+1}"
            state['tasks'][task_id] = 'running'
            state['items'][task_id] = []
            state['active'] = {'id': task_id, 'mode': 'collect', 'status': 'running'}
            return {'task': state['active']}
        if '/results?' in path:
            task_id = path.split('/')[4]
            params = parse_qs(urlsplit(path).query)
            start, limit = int(params['offset'][0]), int(params['limit'][0])
            items = state['items'][task_id]
            return {'task_id': task_id, 'run_id': 'run-'+task_id, 'status': state['tasks'][task_id],
                    'items': items[start:start+limit], 'total': len(items), 'platform_state': {'boss': {'seen': len(items)}},
                    'error': None, 'finished_at': None}
        if path == '/api/agent/state':
            return {'task': state['active'], 'last_task': None, 'preferences': {}}
        if path.endswith('/stop'):
            state['active'] = None
            return {'status': 'stopping'}
        if path == '/api/workbench/preflight?mode=collect':
            return {'ok': True, 'checks': [{'id':'boss_tab','status':'pass','message':'BOSS 页面已连接'}]}
        raise AssertionError(path)
    monkeypatch.setattr(external_collectors, 'request_bosshunter', call)
    return state


def new_search(client, city='北京', **extra):
    batch = client.post('/api/capture/batches', json={'platform':'boss','keyword':'AI 产品 实习','city':city}).json()
    response = client.post('/api/capture/connectors/bosshunter/start-collect', json={'batch_id':batch['id'],'confirmed':True, **extra})
    assert response.status_code == 200, response.text
    return batch['id'], response.json()['search_run']


def candidate(n=1, **extra):
    return {'id':f'boss-{n}','source_platform':'boss','title':f'AI 产品实习 {n}','company':'测试企业','city':'北京',
            'url':f'https://www.zhipin.com/job_detail/{n}.html','jd':'完整的测试 JD','salary':'200元/天',
            'observed_at':'2026-10-08T03:00:00+00:00','reused_snapshot':False, **extra}


def sync(client, run, **extra):
    return client.post(f"/api/capture/search-runs/{run['id']}/sync", json={'confirmed':True, **extra})


def test_exact_results_and_cross_batch_isolation(client, upstream):
    bid, run = new_search(client)
    upstream['items'][run['task_id']] = [candidate(1), candidate(2, reused_snapshot=True)]
    upstream['tasks'][run['task_id']] = 'completed'
    assert sync(client, run).json()['created'] == 2
    assert sync(client, run).json()['created'] == 0
    items = client.get(f"/api/capture/batches/{bid}/items?run_id={run['id']}").json()
    assert len(items) == 2 and items[0]['reused_snapshot'] is True
    assert items[0]['draft']['salary'] == '200元/天'
    second_bid, second = new_search(client, '上海')
    upstream['items'][second['task_id']] = [candidate(3, city='上海')]
    assert sync(client, second).json()['created'] == 1
    assert len(client.get(f"/api/capture/batches/{second_bid}/items?run_id={second['id']}").json()) == 1
    assert client.get(f"/api/capture/batches/{second_bid}/items?run_id={run['id']}").status_code == 404
    assert not any('/api/jobs?' in path for path, _ in upstream['calls'])


def test_page_cursor_persistent_and_partial_results(client, upstream):
    bid, run = new_search(client)
    upstream['items'][run['task_id']] = [candidate(n) for n in range(1,4)]
    response = sync(client, run, limit=2).json()
    assert response['has_more'] and response['next_offset'] == 2
    upstream['tasks'][run['task_id']] = 'stopped'
    response = sync(client, run, limit=2).json()
    assert not response['has_more'] and response['created'] == 1
    assert client.get('/api/capture/connectors/bosshunter/status', params={'batch_id':bid}).json()['search_run']['status'] == 'stopped'
    assert client.get(f"/api/capture/search-runs/{run['id']}").json()['synced_count'] == 3


def test_same_batch_second_run_links_existing_candidate(client, upstream):
    bid, run = new_search(client)
    upstream['items'][run['task_id']] = [candidate(1)]
    upstream['tasks'][run['task_id']] = 'completed'
    sync(client, run)
    response = client.post('/api/capture/connectors/bosshunter/start-collect', json={'batch_id':bid,'confirmed':True})
    second = response.json()['search_run']
    upstream['items'][second['task_id']] = [candidate(1, reused_snapshot=True)]
    result = sync(client, second).json()
    assert result['created'] == 0 and result['already_exists'] == 1
    assert len(client.get(f"/api/capture/batches/{bid}/items?run_id={second['id']}").json()) == 1


def test_same_jd_fresh_read_uses_this_runs_time_without_rewriting_history(client, upstream):
    bid, first = new_search(client)
    old_time = candidate()['observed_at']
    upstream['items'][first['task_id']] = [candidate()]
    upstream['tasks'][first['task_id']] = 'completed'
    assert sync(client, first).json()['created'] == 1
    second = client.post('/api/capture/connectors/bosshunter/start-collect', json={'batch_id': bid, 'confirmed': True}).json()['search_run']
    now = '2026-11-16T03:00:00+00:00'
    upstream['items'][second['task_id']] = [candidate(observed_at=now)]
    assert sync(client, second).json()['created'] == 0
    fresh = client.get(f"/api/capture/batches/{bid}/items?run_id={second['id']}").json()[0]
    assert fresh['observed_at'] == fresh['draft']['last_detail_at'] == now
    assert fresh['reused_snapshot'] is fresh['draft']['reused_snapshot'] is False
    old = client.get(f"/api/capture/batches/{bid}/items?run_id={first['id']}").json()[0]
    assert old['draft']['last_detail_at'] == old['observed_at'] == old_time


def test_active_task_blocks_other_window_without_mutation(client, upstream):
    new_search(client)
    before = len(upstream['calls'])
    bid = client.post('/api/capture/batches', json={'platform':'boss','keyword':'AI 实习','city':'上海'}).json()['id']
    response = client.post('/api/capture/connectors/bosshunter/start-collect', json={'batch_id':bid,'confirmed':True})
    assert response.status_code == 409
    assert all(post is None for _, post in upstream['calls'][before:])


def test_wrong_task_result_never_enters_inbox(client, upstream, monkeypatch):
    bid, run = new_search(client)
    original = external_collectors.request_bosshunter
    def wrong(path, **kwargs):
        result = original(path, **kwargs)
        if '/results?' in path:
            result['task_id'] = 'foreign-task'
        return result
    monkeypatch.setattr(external_collectors, 'request_bosshunter', wrong)
    assert sync(client, run).status_code == 409
    assert client.get(f'/api/capture/batches/{bid}/items').json() == []


def test_offline_preserves_run_and_recovers(client, upstream):
    bid, run = new_search(client)
    upstream['offline'] = True
    status = client.get('/api/capture/connectors/bosshunter/status', params={'batch_id':bid}).json()
    assert not status['connected'] and status['search_run']['task_id'] == run['task_id']
    assert sync(client, run).status_code == 503
    upstream['offline'] = False
    assert client.get(f"/api/capture/search-runs/{run['id']}").json()['status'] == 'running'


def test_stop_only_own_collect_task(client, upstream):
    _, run = new_search(client)
    upstream['active'] = {'id':'foreign-task','mode':'collect','status':'running'}
    assert client.post(f"/api/capture/search-runs/{run['id']}/stop", json={'confirmed':True}).status_code == 409
    upstream['active']['id'] = run['task_id']
    result = client.post(f"/api/capture/search-runs/{run['id']}/stop", json={'confirmed':True})
    assert result.status_code == 200 and result.json()['search_run']['status'] == 'stopping'


def test_manual_review_fields_and_disposition(client, upstream):
    bid, run = new_search(client, internship_only=False)
    assert run['internship_only'] is False
    upstream['items'][run['task_id']] = [candidate(duration='3个月', onsite_days='4天/周')]
    sync(client, run)
    item = client.get(f'/api/capture/batches/{bid}/items').json()[0]
    for action, status in [('defer','deferred'),('dismiss','dismissed'),('reopen','pending')]:
        assert client.patch(f"/api/capture/items/{item['id']}/disposition", json={'action':action}).json()['status'] == status
    payload = {k:item['draft'][k] for k in ('company','title','city','salary','duration','onsite_days')}
    payload.update(jd_text=item['raw_text'], source_url=item['source_url'])
    assert client.patch(f"/api/capture/items/{item['id']}",json=payload).status_code == 200
    result = client.post(f"/api/capture/items/{item['id']}/commit",json=payload).json()
    assert result['status'] == 'imported'
    with connect() as conn:
        job = conn.execute('SELECT * FROM jobs WHERE id=?',(result['job_id'],)).fetchone()
        assert job['duration'] == '3个月' and job['onsite_days'] == '4天/周'
    known = client.get(f'/api/capture/batches/{bid}/items').json()[0]
    assert known['source_state'] == 'already_saved' and known['known_job_id'] == result['job_id']
    assert client.patch(f"/api/capture/items/{item['id']}/disposition",json={'action':'reopen'}).status_code == 409


def test_missing_jd_held_for_review_and_confirm_required(client, upstream):
    bid, run = new_search(client)
    upstream['items'][run['task_id']] = [candidate(jd='')]
    assert sync(client, run).json()['missing_jd'] == 1
    assert client.post(f"/api/capture/search-runs/{run['id']}/sync",json={}).status_code == 422
    assert client.post(f"/api/capture/search-runs/{run['id']}/stop",json={'confirmed':False}).status_code == 422
    assert len(client.get(f'/api/capture/batches/{bid}/items').json()) == 1


def test_legacy_bridge_without_fresh_details_cannot_start_a_new_search(client, upstream):
    upstream['fresh_details'] = False
    bid = client.post('/api/capture/batches', json={'platform': 'boss', 'keyword': '产品实习', 'city': '北京'}).json()['id']
    response = client.post('/api/capture/connectors/bosshunter/start-collect', json={'confirmed': True, 'batch_id': bid})
    assert response.status_code == 409 and '复用旧详情' in response.text
    assert not any(path == '/api/radar/search/start' for path, _ in upstream['calls'])


def test_definite_start_refusal_does_not_lock_and_timeout_recoverable(client, upstream, monkeypatch):
    original = external_collectors.request_bosshunter
    def refused(path, **kwargs):
        if path == '/api/radar/search/start':
            raise external_collectors.ConnectorError('请先登录', http_status=400)
        return original(path, **kwargs)
    monkeypatch.setattr(external_collectors, 'request_bosshunter', refused)
    bid = client.post('/api/capture/batches', json={'platform':'boss','keyword':'AI 实习','city':'北京'}).json()['id']
    start = lambda: client.post('/api/capture/connectors/bosshunter/start-collect', json={'confirmed':True,'batch_id':bid})
    assert start().status_code == 409
    assert start().status_code == 409
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM capture_search_runs WHERE status='failed'").fetchone()[0] == 2
    def timeout(path, **kwargs):
        if path == '/api/radar/search/start':
            raise external_collectors.ConnectorError('响应超时')
        return original(path, **kwargs)
    monkeypatch.setattr(external_collectors, 'request_bosshunter', timeout)
    assert start().status_code == 503
    run = client.get('/api/capture/connectors/bosshunter/status',params={'batch_id':bid}).json()['search_run']
    assert run['status'] == 'unknown' and run['task_id'] is None
    upstream['active'] = {'id':'unconfirmed','mode':'collect','status':'running'}
    endpoint=f"/api/capture/search-runs/{run['id']}/resolve-start"
    assert client.post(endpoint,json={'confirmed':True}).status_code == 409
    upstream['active'] = None
    assert client.post(endpoint,json={'confirmed':True}).json()['search_run']['status'] == 'failed'
    monkeypatch.setattr(external_collectors, 'request_bosshunter', original)
    assert start().status_code == 200
