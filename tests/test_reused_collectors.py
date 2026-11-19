"""v0.7 integration contract tests: reuse upstream software, not their auth/cookies."""
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app import external_collectors
from app.capture import ocr_adapter
from app.main import app


def setup(tmp_path, monkeypatch, platform='boss'):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.db'))
    client = TestClient(app)
    client.__enter__()
    resp = client.post('/api/capture/batches', json={'label':'真实岗位收集', 'platform': platform})
    assert resp.status_code == 201, resp.text
    return client, resp.json()['id']


def test_bosshunter_imports_full_jd_as_pending_without_sensitive_metadata(tmp_path, monkeypatch):
    client, bid = setup(tmp_path, monkeypatch)
    jd = '职位职责：' + ('开发 Agent 应用。' * 500)
    upstream = [
        {'source_platform': 'boss', 'url': '/job_detail/test1.html', 'title': 'Agent实习生',
         'company': '测试创业公司', 'city': '北京', 'jd': jd, 'hr_name':'PRIVATE'},
        {'source_platform': 'zhilian', 'url': 'https://other.example/job/1', 'title':'无关', 'company':'其他', 'jd':'无关'},
        {'source_platform': 'boss', 'url':'https://www.zhipin.com.evil.example/job_detail/foo',
         'title':'钓鱼', 'company':'坏公司', 'jd':'恶意'},
    ]
    called = []
    def fake_request(path, *, post=None):
        called.append((path, post))
        if path.startswith('/api/jobs?'):
            return upstream
        return {'task_id':'a'}
    monkeypatch.setattr(external_collectors, 'request_bosshunter', fake_request)
    req={'batch_id':bid, 'confirmed': True, 'limit': 50}
    a=client.post('/api/capture/connectors/bosshunter/import',json=req)
    assert a.status_code == 200,a.text
    assert a.json()['created']==1 and a.json()['rejected']==2
    items=client.get(f'/api/capture/batches/{bid}/items').json()
    assert len(items)==1 and items[0]['status']=='pending'
    assert items[0]['raw_text']==jd and items[0]['source_url']=='https://www.zhipin.com/job_detail/test1.html'
    assert 'PRIVATE' not in str(items)
    assert client.get('/api/dashboard').json()['jobs']==0
    assert client.post('/api/capture/connectors/bosshunter/import',json=req).json()['already_exists']==1
    # No automated sends, no auto-confirmation.
    assert all('/api/jobs?' in path for path, _ in called)
    client.__exit__(None,None,None)


def test_bosshunter_collect_requires_explicit_confirmation_and_only_collect(tmp_path, monkeypatch):
    client,bid=setup(tmp_path,monkeypatch)
    seen=[]
    def upstream(path, *, post=None):
        seen.append((path,post))
        return {'task_id':'demo'}
    monkeypatch.setattr(external_collectors,'request_bosshunter', upstream)
    assert client.post('/api/capture/connectors/bosshunter/start-collect',json={'confirmed':False}).status_code==422
    assert client.post('/api/capture/connectors/bosshunter/start-collect',json={'confirmed':True}).status_code==200
    assert seen==[('/api/agent/tasks',{'mode':'collect','confirm':True})]
    assert client.post('/api/capture/connectors/bosshunter/import',json={'batch_id':bid,'confirmed':False}).status_code==422
    client.__exit__(None,None,None)


def test_workbench_clipboard_multi_job_and_idempotency(tmp_path, monkeypatch):
    client,bid=setup(tmp_path,monkeypatch, 'shixiseng')
    content='''# 采集自 实习僧 （https://www.shixiseng.com/interns）
# 共 2 个岗位

公司：星辰AI
岗位：AI产品实习生
城市：上海
薪资：150元/天
https://www.shixiseng.com/intern/111
岗位原文：
负责 Agent 业务体验研究
熟悉 prompt engineering

---

公司：飞鱼科技
岗位：模型应用工程师实习生
城市：北京
https://www.shixiseng.com/intern/222
岗位原文：
负责研发
'''
    first=client.post(f'/api/capture/batches/{bid}/workbench-text',json={'text':content})
    assert first.status_code==200,first.text
    assert first.json()['created']==2
    second=client.post(f'/api/capture/batches/{bid}/workbench-text',json={'text':content})
    assert second.json()['already_exists']==2
    items=client.get(f'/api/capture/batches/{bid}/items').json()
    assert items[0]['draft']['company']=='飞鱼科技'
    assert items[1]['raw_text'].startswith('负责 Agent')
    assert all(item['status']=='pending' for item in items)
    client.__exit__(None,None,None)


def test_ocr_screenshot_result_is_suggestion_only(tmp_path,monkeypatch):
    client,bid=setup(tmp_path,monkeypatch)
    created=client.post(f'/api/capture/batches/{bid}/items',json={'raw_text':''}).json()
    image=BytesIO();Image.new('RGB',(160,70),'white').save(image,'PNG')
    assert client.post(f"/api/capture/items/{created['id']}/screenshot",content=image.getvalue()).status_code==200
    monkeypatch.setattr(ocr_adapter,'is_available',lambda:True)
    monkeypatch.setattr(ocr_adapter,'recognize_file',lambda p: {'text':'某公司\nAI 实习生','line_count':2,'confidence':0.95,'provider':'rapidocr'})
    response=client.post(f"/api/capture/items/{created['id']}/ocr")
    assert response.status_code==200
    assert response.json()['text']=='某公司\nAI 实习生'
    row=client.get(f'/api/capture/batches/{bid}/items').json()[0]
    assert row['raw_text']=='' and 'jd_text' not in row['draft']
    assert row['status']=='pending' and client.get('/api/dashboard').json()['jobs']==0
    client.__exit__(None,None,None)


def test_connector_guard_denies_non_loopback_and_unapproved_paths():
    assert external_collectors.normalize_bosshunter_job({
        'platform':'boss','title':'工作','company':'企业','url':'https://not-zhipin.example/job_detail/1', 'jd':'...'
    }) is None
    try:
        external_collectors.request_bosshunter('http://127.0.0.1:1/exfiltrate')
    except external_collectors.ConnectorError:
        pass
    else:
        raise AssertionError('Should reject arbitrary upstream addresses')


def test_real_loopback_http_transport_and_redirect_block(monkeypatch):
    import json, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class LocalAPI(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if 'offset=0' in self.path:
                self.send_response(302); self.send_header('Location','https://example.com/private'); self.end_headers()
                return
            body=json.dumps([{'title':'demo'}]).encode()
            self.send_response(200);self.end_headers();self.wfile.write(body)
    server=HTTPServer(('127.0.0.1',0),LocalAPI)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        monkeypatch.setattr(external_collectors,'BOSSHUNTER_ORIGIN',f'http://127.0.0.1:{server.server_port}')
        assert external_collectors.request_bosshunter('/api/agent/state')==[{'title':'demo'}]
        # Automatic following of upstream HTTP redirects must stay disabled.
        try:
            external_collectors.request_bosshunter('/api/jobs?limit=1&offset=0')
        except external_collectors.ConnectorError:
            pass
        else:
            # URL guard may reject the nonstandard redirect query before networking.
            raise AssertionError('Unauthorized upstream URL')
    finally:
        server.shutdown();thread.join(timeout=2);server.server_close()
