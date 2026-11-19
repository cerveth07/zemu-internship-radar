import os
from fastapi.testclient import TestClient


def test_release_version_is_consistent(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'version.sqlite'))
    from app import VERSION
    from app.main import app
    from desktop import APP_TITLE

    with TestClient(app) as client:
        assert client.get('/api/health').json()['version'] == VERSION
        assert client.get('/api/support/diagnostics').json()['app_version'] == VERSION
        info = client.get('/openapi.json').json()['info']
        assert info['version'] == VERSION
        assert info['title'] == '择木 · 实习工作台'
        home = client.get('/').text
        assert '<title>择木 · 实习工作台</title>' in home
        assert '<strong>择木</strong><small>实习工作台</small>' in home
        assert f'<span class="version">v{VERSION}</span>' in home
    assert APP_TITLE == f'择木 · 实习工作台 v{VERSION}'


def test_end_to_end(tmp_path):
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(tmp_path / 'test.sqlite')
    from app.main import app
    with TestClient(app) as client:
        assert client.get('/api/health').json()['collectors_enabled'] is False
        assert client.get('/api/dashboard').json()['jobs'] == 0
        company = client.post('/api/companies',json={'name':'测试公司','sector':'Agent'});
        assert company.status_code == 201, company.text
        cid = company.json()['id']
        assert client.post('/api/companies',json={'name':'测试公司'}).status_code == 409
        job = client.post('/api/jobs',json={
            'company_id':cid,'title':'AI Agent 实习生','direction':'agent',
            'jd_text':'负责 Agent 应用开发，要求 Python',
            'source_platform':'manual','skills':[{'name':'Python','necessity':'must','evidence':'要求 Python'}]
        }); assert job.status_code == 201, job.text
        jid = job.json()['id']
        detail = client.get(f'/api/jobs/{jid}').json()
        assert detail['jd_text'].startswith('负责 Agent')
        assert detail['sources'][0]['platform'] == 'manual'
        assert detail['skills'][0]['name'] == 'Python'
        assert client.get('/api/skills').json()[0]['must_count'] == 1
        assert client.patch(f'/api/jobs/{jid}',json={'status':'verified','priority':'high'}).status_code == 200
        assert client.post(f'/api/jobs/{jid}/sources',json={'platform':'company_site','url':'https://example.com/job/1'}).status_code == 201
        assert client.put(f'/api/jobs/{jid}/application',json={'stage':'contacted','resume_version':'AI PM v1'}).status_code == 200
        assert client.post('/api/learning',json={'title':'学习 Python','source_job_id':jid}).status_code == 201
        assert client.get('/api/dashboard').json()['verified'] == 1
        assert client.get('/api/dashboard').json()['applications'] == 1
        assert len(client.get(f'/api/jobs/{jid}').json()['history']) == 4
        assert len(client.get('/api/jobs?q=Agent').json()) == 1
        exported = client.get('/api/export').json()
        assert len(exported['job_sources']) == 2
        assert len(exported['learning_tasks']) == 1
        assert client.get('/').status_code == 200
        assert client.get('/static/app.js').status_code == 200
