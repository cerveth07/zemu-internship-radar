"""The local agent API and MCP server reuse the app's own exports, prompts and validators."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent_api
from app.main import app
from tools import radar_mcp

ROOT = Path(__file__).resolve().parents[1]
JD = '负责 Agent 应用开发。熟练使用 Python 进行数据处理。熟悉 LangChain 或 LlamaIndex 者优先。'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.sqlite'))
    with TestClient(app) as test_client:
        token = agent_api.agent_token()
        test_client.headers.update({'Authorization': 'Bearer ' + token})
        company = test_client.post('/api/companies', json={'name': '示例企业', 'city': '北京'}).json()['id']
        ids = []
        for title in ('Agent 应用开发实习生', 'AI 产品实习生'):
            ids.append(test_client.post('/api/jobs', json={
                'company_id': company, 'title': title, 'direction': 'agent', 'city': '北京', 'jd_text': JD,
                'status': 'candidate', 'priority': 'high', 'source_platform': 'manual',
                'source_url': 'https://example.com/' + str(len(ids)), 'skills': []}).json()['id'])
        test_client.job_ids = ids
        yield test_client


def test_every_agent_route_requires_the_local_token(client):
    bare = TestClient(app)
    for path in ('/api/agent/v1', '/api/agent/v1/jobs', '/api/agent/v1/prompts'):
        assert bare.get(path).status_code == 401
        assert bare.get(path, headers={'Authorization': 'Bearer ' + 'x' * 43}).status_code == 401
    assert bare.post('/api/agent/v1/results', json={}).status_code == 401
    access = bare.get('/api/agent-access').json()
    assert access['token_file'].endswith('agent-token') and agent_api.agent_token() not in json.dumps(access)
    assert client.get('/api/agent/v1', headers={'Authorization': '', 'X-Radar-Token': agent_api.agent_token()}).status_code == 200


def test_jobs_are_listed_without_jd_unless_requested(client):
    listed = client.get('/api/agent/v1/jobs').json()
    assert listed['total'] == 2 and 'jd_text' not in listed['items'][0]
    full = client.get('/api/agent/v1/jobs', params={'include_jd': 'true', 'limit': 1}).json()
    assert full['items'][0]['jd_text'] == JD and len(full['items']) == 1
    detail = client.get(f'/api/agent/v1/jobs/{client.job_ids[0]}').json()
    assert detail['jd_text'] == JD and 'history' not in detail and detail['sources']


def test_extraction_round_trip_previews_first_and_keeps_human_review(client):
    job = client.job_ids[0]
    exported = client.post('/api/agent/v1/packages/jd-extraction', json={'job_ids': [job]}).json()
    package = exported['packages'][0]
    assert package['task_instructions'] and package['jobs'][0]['jd_text'] == JD and exported['prompt'] == 'jd-extraction'
    result = {'package_id': package['package_id'], 'requirements': [
        {'job_id': job, 'requirement_type': 'technical_skill', 'name': 'Python', 'strength': 'must',
         'evidence': '熟练使用 Python 进行数据处理', 'source_field': 'jd_text'},
        {'job_id': job, 'requirement_type': 'technical_skill', 'name': '大模型应用框架', 'strength': 'bonus',
         'evidence': '熟悉 LangChain 或 LlamaIndex 者优先', 'source_field': 'jd_text',
         'option_logic': 'any_of', 'alternatives': ['LangChain', 'LlamaIndex']}]}
    preview = client.post('/api/agent/v1/results', json=result).json()
    assert preview['kind'] == 'requirements' and preview['committed'] is False and preview['result']['dry_run'] is True
    assert client.get('/api/requirements', params={'status': 'pending'}).json()['total'] == 0
    saved = client.post('/api/agent/v1/results', params={'commit': 'true'}, json=result,
                        headers={'X-Radar-Preview': preview['preview_token']}).json()
    assert saved['committed'] is True
    pending = client.get('/api/requirements', params={'status': 'pending'}).json()
    assert pending['total'] == 2, 'imported requirements stay pending for human review'
    altered = {**result, 'requirements': [{**result['requirements'][0], 'evidence': '精通 Python'}]}
    rejected = client.post('/api/agent/v1/results', json=altered)
    assert rejected.status_code == 422 and '逐字原文' in rejected.text, 'non-verbatim evidence is refused'
    assert client.get('/api/requirements', params={'status': 'pending'}).json()['total'] == 2
    raw_package = client.post('/api/agent/v1/results', json=package)
    assert raw_package.status_code == 422 and '原始岗位数据包' in raw_package.text


@pytest.mark.parametrize('evidence_ids', [[-1], [1, 1]])
def test_career_package_reports_invalid_evidence_ids(client, evidence_ids):
    response = client.post('/api/agent/v1/packages/career-analysis',
                           json={'job_ids': client.job_ids, 'evidence_ids': evidence_ids})
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['evidence_ids']


def test_career_package_returns_its_prompt_and_priorities_import(client):
    exported = client.post('/api/agent/v1/packages/career-analysis',
                           json={'job_ids': client.job_ids, 'goal': 'direction', 'mode': 'full'}).json()
    assert exported['prompt'] == 'career-direction' and '方向比较' in exported['prompt_text']
    assert exported['packages'][0]['package_id']
    extraction = client.post('/api/agent/v1/packages/jd-extraction', json={'job_ids': client.job_ids}).json()['packages'][0]
    result = {'package_id': extraction['package_id'], 'priorities': [{'job_id': client.job_ids[1], 'priority': 'low', 'reason': '方向不符'}]}
    endpoint = '/api/agent/v1/results'
    assert client.post(endpoint, params={'commit': 'true'}, json=result).status_code == 409
    preview = client.post(endpoint, json=result).json()
    assert preview['kind'] == 'priorities'
    headers = {'X-Radar-Preview': preview['preview_token']}
    changed = {**result, 'priorities': [{**result['priorities'][0], 'priority': 'skip'}]}
    assert client.post(endpoint, params={'commit': 'true'}, json=changed, headers=headers).status_code == 409
    assert client.get(f'/api/jobs/{client.job_ids[1]}').json()['priority'] == 'high'
    for _ in range(2):  # A lost response can be retried with the same preview.
        assert client.post(endpoint, params={'commit': 'true'}, json=result, headers=headers).status_code == 200
    assert client.get(f'/api/jobs/{client.job_ids[1]}').json()['priority'] == 'low'


def test_prompts_match_the_app_and_unknown_names_are_rejected(client):
    names = [p['name'] for p in client.get('/api/agent/v1/prompts').json()]
    assert names == list(agent_api.PROMPTS)
    for name in names:
        assert len(client.get(f'/api/agent/v1/prompts/{name}').json()['text']) > 200
    assert client.get('/api/agent/v1/prompts/unknown').status_code == 404


def test_endpoint_file_is_written_and_removed(client):
    path = agent_api.write_endpoint('http://127.0.0.1:12345')
    info = json.loads(path.read_text(encoding='utf-8'))
    assert info['api'] == 'http://127.0.0.1:12345/api/agent/v1' and Path(info['token_file']).is_file()
    assert 'token' not in info
    agent_api.remove_endpoint()
    assert not path.exists()


def test_schemas_prompts_and_examples_stay_in_sync():
    done = subprocess.run([sys.executable, 'tools/export_agent_schemas.py', '--check'], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    if shutil.which('node'):
        done = subprocess.run(['node', 'tools/export_agent_prompts.cjs', '--check'], cwd=ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
    from app.analysis import AnalysisImport
    from app.main import PriorityResultImport
    from app.requirements import RequirementImport
    examples = ROOT / 'docs' / 'agent' / 'examples'
    load = lambda name: json.loads((examples / name).read_text(encoding='utf-8'))
    RequirementImport.model_validate(load('requirements-result.json'))
    AnalysisImport.model_validate(load('findings-result.json'))
    priorities = load('priorities-result.json')
    PriorityResultImport.model_validate({'package_id': priorities['package_id'], 'rows': priorities['priorities']})
    with pytest.raises(ValueError):
        RequirementImport.model_validate(load('invalid-requirements-result.json'))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_mcp_discovery_skips_stale_desktop_endpoint_and_uses_source_token(tmp_path, monkeypatch):
    desktop = tmp_path / 'InternshipRadarPrivateRC' / 'data'
    source = tmp_path / 'source'
    desktop.mkdir(parents=True)
    source.mkdir()
    (desktop / 'agent-token').write_text('desktop-token', encoding='utf-8')
    (desktop / 'agent-endpoint.json').write_text('{"url":"http://127.0.0.1:32100","pid":12}', encoding='utf-8')
    (source / 'agent-token').write_text('source-token', encoding='utf-8')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(radar_mcp, 'REPO_DATA', source)
    for key in ('RADAR_PRIVATE_RC_URL', 'RADAR_PRIVATE_RC_AGENT_TOKEN', 'RADAR_PRIVATE_RC_DATA_DIR'):
        monkeypatch.delenv(key, raising=False)

    def matches(base, folder, pid=None):
        return (base, folder, pid) == ('http://127.0.0.1:8785', source, None)

    monkeypatch.setattr(radar_mcp, 'matches_data_dir', matches)
    assert radar_mcp.connection() == ('http://127.0.0.1:8785', 'source-token')


def test_mcp_rejects_non_loopback_and_does_not_follow_redirects(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(302 if len(received) == 1 else 200)
            self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/unexpected')
            self.end_headers()

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            monkeypatch.setenv('RADAR_PRIVATE_RC_AGENT_TOKEN', 'test-token')
            monkeypatch.setenv('RADAR_PRIVATE_RC_URL', 'http://example.invalid')
            with pytest.raises(ValueError, match='本机'):
                radar_mcp.connection()
            monkeypatch.setenv('RADAR_PRIVATE_RC_URL', f'http://127.0.0.1:{server.server_port}')
            with pytest.raises(RuntimeError, match='重定向'):
                radar_mcp.call_api('GET', '')
            assert received == ['/api/agent/v1']
        finally:
            server.shutdown()
            worker.join()


def test_mcp_server_talks_to_the_running_app(tmp_path, monkeypatch):
    import uvicorn
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'mcp.sqlite'))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='on'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        env = {**os.environ, 'RADAR_PRIVATE_RC_URL': f'http://127.0.0.1:{port}', 'RADAR_PRIVATE_RC_AGENT_TOKEN': agent_api.agent_token()}
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'list_jobs', 'arguments': {'limit': 5}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'preview_result', 'arguments': {'result': {'unexpected': True}}}},
            {'jsonrpc': '2.0', 'id': 5, 'method': 'unknown/method'},
        ]
        stdin = '\n'.join(json.dumps(m) for m in messages) + '\n'
        done = subprocess.run([sys.executable, str(ROOT / 'tools' / 'radar_mcp.py')], input=stdin.encode(),
                              capture_output=True, env=env, timeout=60)
        replies = {r['id']: r for r in map(json.loads, done.stdout.decode().splitlines())}
        assert set(replies) == {1, 2, 3, 4, 5}, done.stderr.decode()
        assert replies[1]['result']['serverInfo']['name'] == 'ai-internship-radar'
        assert {t['name'] for t in replies[2]['result']['tools']} >= {'list_jobs', 'preview_result', 'import_result'}
        listed = replies[3]['result']
        assert listed['isError'] is False and json.loads(listed['content'][0]['text'])['total'] == 0
        assert replies[4]['result']['isError'] is True and '422' in replies[4]['result']['content'][0]['text']
        assert replies[5]['error']['code'] == -32601
        from app.mcp_runtime import check, config
        from app.db import database_path
        assert Path(config()['mcpServers']['ai-internship-radar']['command']).is_file()
        assert check(f'http://127.0.0.1:{port}', database_path().parent)['ok'] is True
    finally:
        server.should_exit = True
        thread.join(timeout=10)
