"""Fixed local BossHunter search/collect contract; no live upstream requests."""
import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import external_collectors


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.sqlite'))
    with TestClient(app) as client:
        yield client


def batch(client, **extra):
    response = client.post('/api/capture/batches', json={
        'label': 'AI 产品实习', 'platform': 'boss',
        'keyword': 'AI 产品 实习，Agent 产品实习;AI 产品 实习', 'city': '北京；上海', **extra})
    assert response.status_code == 201
    return response.json()['id']


def test_preview_apply_collect_chain(client, monkeypatch):
    calls = []
    def upstream(path, *, post=None):
        calls.append((path, post))
        if path == '/api/radar/capabilities':
            return {'version':1,'matched_results':True,'fresh_details':True}
        if path.startswith('/api/radar/search-options'):
            return {'internship_only':True,'changes':[]}
        if path.endswith('/preview'):
            return {'preferences': post['preferences'], 'changes': [{'path':'search.keywords','before':[], 'after':post['preferences']['keywords']}]}
        if path.endswith('/apply'):
            return {'success': True}
        return {'task': {'id': 'task-1', 'mode': 'collect', 'status': 'running', 'logs': ['private'], 'resume_path': 'private'}}
    monkeypatch.setattr(external_collectors, 'request_bosshunter', upstream)
    bid = batch(client)
    preview = client.post('/api/capture/connectors/bosshunter/search-preview', json={'batch_id':bid, 'max_pages':2})
    assert preview.status_code == 200, preview.text
    expected = {'keywords':['AI 产品 实习','Agent 产品实习'], 'cities':['北京','上海'],
                'platform_order':['boss'], 'allow_internship':True, 'recruitment_type':'both', 'max_pages':{'boss':2}}
    assert preview.json()['preferences'] == expected
    start = client.post('/api/capture/connectors/bosshunter/start-collect', json={'confirmed':True,'batch_id':bid,'max_pages':2})
    assert start.status_code == 200, start.text
    assert calls == [('/api/radar/capabilities',None),
                     ('/api/radar/search-options/preview',{'internship_only':True}),
                     ('/api/agent/config/preview', {'preferences':expected}),
                     ('/api/radar/capabilities',None),
                     ('/api/radar/search/start', {'preferences':expected,'internship_only':True,'confirmed':True})]
    assert start.json()['upstream']['task']['id'] == 'task-1'
    assert 'private' not in start.text


@pytest.mark.parametrize('extra,action,status', [({'platform':'manual'},None,409), ({},'pause',409), ({},'complete',409), ({'city':''},None,400), ({'keyword':''},None,400)])
def test_batch_guard_before_upstream(client, monkeypatch, extra, action, status):
    def unexpected(*args, **kwargs):
        raise AssertionError('Must validate before calling upstream')
    monkeypatch.setattr(external_collectors, 'request_bosshunter', unexpected)
    bid = batch(client, **extra)
    if action:
        assert client.patch(f'/api/capture/batches/{bid}', json={'action':action}).status_code == 200
    for endpoint in ('search-preview','start-collect'):
        response = client.post('/api/capture/connectors/bosshunter/'+endpoint, json={'batch_id':bid,'confirmed':True})
        assert response.status_code == status


def test_input_and_confirmation_guard(client, monkeypatch):
    monkeypatch.setattr(external_collectors, 'request_bosshunter', lambda *a, **k: pytest.fail('Unexpected upstream'))
    bid = batch(client)
    for maximum in (0,4,True,1.5):
        assert client.post('/api/capture/connectors/bosshunter/search-preview', json={'batch_id':bid,'max_pages':maximum}).status_code == 422
    for confirmed in (False,None):
        assert client.post('/api/capture/connectors/bosshunter/start-collect', json={'batch_id':bid,'confirmed':confirmed}).status_code == 422
    assert client.post('/api/capture/connectors/bosshunter/search-preview', json={'batch_id':999}).status_code == 404


def test_legacy_collect_and_preflight_error(client, monkeypatch):
    calls=[]
    def upstream(path, *, post=None):
        calls.append((path,post))
        return {'error':'请先处理启动前检查', 'checks':[{'status':'error','message':'没有连接 Google Chrome','detail':'请开启远程调试'}]}
    monkeypatch.setattr(external_collectors, 'request_bosshunter', upstream)
    response=client.post('/api/capture/connectors/bosshunter/start-collect', json={'confirmed':True})
    assert response.status_code == 409
    assert '远程调试' in response.json()['detail']
    assert calls == [('/api/agent/tasks',{'mode':'collect','confirm':True})]


def test_apply_failure_does_not_start(client, monkeypatch):
    calls=[]
    def upstream(path, *, post=None):
        calls.append(path)
        raise external_collectors.ConnectorError('BossHunter：配置正在使用，不能修改')
    monkeypatch.setattr(external_collectors, 'request_bosshunter', upstream)
    response=client.post('/api/capture/connectors/bosshunter/start-collect', json={'confirmed':True,'batch_id':batch(client)})
    assert response.status_code == 503
    assert '不能修改' in response.json()['detail']
    assert calls == ['/api/radar/capabilities']


def test_safe_status(client, monkeypatch):
    monkeypatch.setattr(external_collectors, 'request_bosshunter', lambda *a, **k: {
        'preferences':{'keywords':['AI 实习'],'cities':['北京'],'allow_internship':True,'api_key':'private','resume_path':'private'},
        'ai':{'api_key':'private'}, 'task':{'id':'t1','mode':'collect','status':'running','logs':['private'],'progress':{'cookie':'private'}},
        'last_task':{'id':'t0','mode':'collect','status':'failed','error':'api_key=sk-private'}})
    response=client.get('/api/capture/connectors/bosshunter/status')
    assert response.json()['connected'] is True
    assert response.json()['task']['status'] == 'running'
    assert 'private' not in response.text
    assert response.json()['preferences']['keywords'] == ['AI 实习']


def test_http_error_details_and_fixed_allowlist(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError('http://127.0.0.1:8686/api/agent/tasks',400,'Bad request',{}, BytesIO(json.dumps({
                'error':'请先处理启动前检查','messages':['浏览器连接失败，开启远程调试','api_key=sk-private'],
                'api_key':'private'}).encode()))
    monkeypatch.setattr(external_collectors,'build_opener',lambda *a:Opener())
    with pytest.raises(external_collectors.ConnectorError) as exc:
        external_collectors.request_bosshunter('/api/agent/tasks',post={'mode':'collect','confirm':True})
    assert '远程调试' in str(exc.value) and 'private' not in str(exc.value)
    with pytest.raises(external_collectors.ConnectorError,match='未授权'):
        external_collectors.request_bosshunter('/api/agent/config/evil',post={})
