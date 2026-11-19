from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from test_career_analysis_roundtrip import add_confirmed, add_evidence, career, findings, setup_job


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'dossier.sqlite'))
    with TestClient(app) as instance:
        yield instance


def test_followup_dates_survive_legacy_writes_and_appear_on_today(client):
    job = setup_job(client)
    today = date.today().isoformat()
    payload = {'stage': 'resume_sent', 'applied_on': today, 'follow_up_on': today, 'next_action': '补发作品链接'}
    assert client.put(f'/api/jobs/{job}/application', json=payload).status_code == 200
    dashboard = client.get('/api/dashboard').json()
    assert dashboard['due_followup_count'] == 1
    assert dashboard['due_followups'][0]['job_id'] == job
    assert client.put(f'/api/jobs/{job}/application', json={
        'stage': 'contacted', 'next_action': '旧版修改行动',
    }).status_code == 200
    application = client.get(f'/api/jobs/{job}').json()['application']
    assert application['applied_on'] == application['follow_up_on'] == today
    assert client.put(f'/api/jobs/{job}/application', json={
        'stage': 'contacted', 'follow_up_on': '2026-02-30',
    }).status_code == 422
    expected = {key: application[key] for key in payload}
    assert client.patch(f'/api/jobs/{job}', json={
        'application': {**expected, 'next_action': '并发后的行动'},
        'expected_application': expected,
    }).status_code == 200
    stale = client.patch(f'/api/jobs/{job}', json={
        'application': {**expected, 'next_action': '不能覆盖'},
        'expected_application': expected,
    })
    assert stale.status_code == 409
    assert client.put(f'/api/jobs/{job}/application', json={
        'stage': 'rejected', 'follow_up_on': today,
    }).status_code == 200
    assert client.get('/api/dashboard').json()['due_followup_count'] == 0
    assert client.put(f'/api/jobs/{job}/application', json={
        'stage': 'resume_sent', 'follow_up_on': (date.today() + timedelta(days=1)).isoformat(),
    }).status_code == 200
    assert client.get('/api/dashboard').json()['due_followup_count'] == 0


def test_structured_findings_resolve_in_job_file_and_adopt_once(client):
    first = setup_job(client)
    second = setup_job(client, name='第二家公司')
    requirement = add_confirmed(client, first)
    evidence = add_evidence(client)
    private = add_evidence(client, description='UNREFERENCED PRIVATE MATERIAL')
    package = career(client, [first, second], [evidence])
    payload = findings(package, [first, second])
    payload['findings'][0]['assessments'] = [{
        'requirement_id': requirement, 'personal_evidence_ids': [evidence],
        'judgment': 'transferable', 'explanation': '已有课程作品，工作场景还需核对',
        'next_action': '补充真实项目中的验证方法',
    }]
    preview = client.post('/api/analysis/import', json=payload)
    assert preview.status_code == 200, preview.text
    assert client.get(f'/api/jobs/{first}').json()['dossier']['findings'] == []
    assert client.post('/api/analysis/import', json={**payload, 'dry_run': False}).status_code == 200
    assert client.post('/api/analysis/import', json={**payload, 'dry_run': False}).json()['new_count'] == 0
    file = client.get(f'/api/jobs/{first}').json()['dossier']
    [finding] = file['findings']
    assert finding['requirement_ids'] == [requirement]
    assert finding['personal_evidence_ids'] == [evidence]
    assert finding['assessments'][0]['judgment'] == 'transferable'
    assert not finding['source_changed']
    assert [item['id'] for item in file['personal_evidence']] == [evidence]
    assert 'UNREFERENCED PRIVATE MATERIAL' not in str(file)
    assert private != evidence
    route = f"/api/analysis/findings/{finding['id']}/adopt-learning-task"
    task = client.post(route).json()['id']
    assert client.post(route).json() == {'id': task, 'already_adopted': True}
    for job in (first, second):
        [linked] = client.get(f'/api/jobs/{job}').json()['dossier']['learning_tasks']
        assert linked['id'] == task
        assert '补充真实项目中的验证方法' in linked['notes']
    with connect() as conn:
        conn.execute('UPDATE profile_evidence SET description=? WHERE id=?', ('证据已经修改', evidence))
    assert client.get(f'/api/jobs/{first}').json()['dossier']['findings'][0]['source_changed']


def test_assessments_require_exported_citations_and_the_right_job(client):
    first = setup_job(client)
    second = setup_job(client, name='另一个岗位')
    requirement = add_confirmed(client, first)
    evidence = add_evidence(client)
    package = career(client, [first, second], [evidence])
    payload = findings(package, [first])
    item = {'requirement_id': requirement, 'personal_evidence_ids': [],
            'judgment': 'direct', 'explanation': '判断依据', 'next_action': '核实实际经历'}
    payload['findings'][0]['assessments'] = [item]
    assert client.post('/api/analysis/import', json=payload).status_code == 200
    item['judgment'] = 'not_documented'
    assert client.post('/api/analysis/import', json=payload).status_code == 200
    payload['findings'][0]['job_ids'] = [second]
    assert client.post('/api/analysis/import', json=payload).status_code == 422
    payload['findings'][0]['job_ids'] = [first]
    item['personal_evidence_ids'] = [evidence + 999]
    assert client.post('/api/analysis/import', json=payload).status_code == 422
