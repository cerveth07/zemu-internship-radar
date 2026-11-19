import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from test_career_analysis_roundtrip import setup_job, add_confirmed, add_evidence, career, findings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'career.sqlite'))
    with TestClient(app) as instance:
        yield instance


def analyzed(client):
    job = setup_job(client)
    req = add_confirmed(client, job)
    evidence = add_evidence(client)
    package = career(client, [job], [evidence])
    payload = findings(package, [job])
    payload['findings'][0]['kind'] = 'job_assessment'
    payload['findings'][0]['assessments'] = [{'requirement_id': req, 'personal_evidence_ids': [evidence],
        'judgment': 'transferable', 'explanation': '有项目经历，业务场景待核实', 'next_action': '补充项目复盘作品'}]
    assert client.post('/api/analysis/import', json={**payload, 'dry_run': False}).status_code == 200
    finding = client.get('/api/analysis/findings').json()[0]
    return job, req, evidence, finding


def test_evidence_edit_preserves_id_and_requests_recheck(client):
    job, req, evidence, finding = analyzed(client)
    current = client.get('/api/evidence').json()[0]
    update = {k: current[k] for k in ('skill', 'skills', 'proficiency', 'evidence_type', 'description', 'source_url', 'review_status')}
    update.update(description='补充已完成的作品和真实结果', skills=['Python', '数据分析'], expected_revision=current['revision'])
    result = client.patch(f'/api/evidence/{evidence}', json=update)
    assert result.status_code == 200, result.text
    assert result.json()['id'] == evidence
    assert client.patch(f'/api/evidence/{evidence}', json=update).status_code == 409
    revised = client.get('/api/analysis/findings').json()[0]
    assert revised['review_status'] == 'needs_review'
    assert revised['personal_evidence_ids'] == [evidence]
    assert len(client.get(f'/api/evidence/{evidence}/history').json()) == 1
    assert client.get(f'/api/jobs/{job}').json()['dossier']['personal_evidence'][0]['id'] == evidence


def test_user_judgment_revisions_keep_original_and_new_changes_invalidate(client):
    job, req, evidence, finding = analyzed(client)
    amended = [{**finding['assessments'][0], 'judgment': 'direct', 'explanation': '已核对作品中的实际实现'}]
    route = f"/api/analysis/findings/{finding['id']}/review"
    assert client.post(route, json={'expected_revision': finding['revision'], 'state': 'current', 'assessments': amended}).status_code == 200
    saved = client.get('/api/analysis/findings').json()[0]
    assert saved['assessments'][0]['judgment'] == 'direct'
    assert saved['original_assessments'][0]['judgment'] == 'transferable'
    assert saved['review_status'] == 'current'
    assert client.post(route, json={'expected_revision': finding['revision'], 'state': 'rejected'}).status_code == 409
    assert client.post(route, json={'expected_revision': saved['revision'], 'state': 'rejected', 'note': '不再采用'}).status_code == 200
    assert client.get('/api/analysis/findings').json()[0]['review_status'] == 'rejected'
    assert len(client.get(route.replace('/review', '/history')).json()) == 2


def test_individual_action_to_task_and_outcome_are_explicit_and_retry_safe(client):
    job, req, evidence, finding = analyzed(client)
    route = f"/api/analysis/findings/{finding['id']}/assessments/{req}/task"
    task = client.post(route).json()['id']
    assert client.post(route).json() == {'id': task, 'already_adopted': True}
    row = client.get('/api/learning').json()[0]
    assert row['linked_jobs'][0]['id'] == job
    assert row['linked_requirements'][0]['id'] == req
    complete = {'completion_id': str(uuid.uuid4()), 'expected_revision': row['revision'], 'evidence': {
        'skill': 'Python', 'skills': ['Python', '数据分析'], 'description': '实际完成的复盘', 'review_status': 'needs_check'}}
    done = client.post(f'/api/learning/{task}/complete', json=complete)
    assert done.status_code == 200, done.text
    assert client.post(f'/api/learning/{task}/complete', json=complete).json() == done.json()
    assert len(client.get('/api/evidence').json()) == 2
    outcome = client.get('/api/evidence').json()[0]
    assert outcome['review_status'] == 'needs_check'
    assert client.get('/api/analysis/findings').json()[0]['assessments'][0]['judgment'] == 'transferable'
    assert client.get('/api/learning').json()[0]['outcome_evidence_ids'] == [outcome['id']]


def test_material_import_has_verbatim_candidates_and_immutable_versions(client):
    text = '校园活动项目\n整理报名数据，完成活动复盘。\n\n课程研究\n整理问卷并给出结论。'
    preview = client.post('/api/materials/candidates/preview', json={'text': text}).json()
    assert len(preview['candidates']) == 2
    assert client.get('/api/materials').json() == []
    item = preview['candidates'][0]
    item.update(skill='数据分析', skills=['数据分析', '活动运营'])
    data = {'name': '个人经历初稿', 'text': text, 'import_id': str(uuid.uuid4()), 'candidates': [item]}
    saved = client.post('/api/materials/import-evidence', json=data)
    assert saved.status_code == 200, saved.text
    assert client.post('/api/materials/import-evidence', json=data).json() == saved.json()
    first = saved.json()['id']
    next_version = client.post('/api/materials/versions', json={'name': '个人经历初稿', 'material_id': saved.json()['material_id'], 'text': '更新后的实际说明'}).json()['id']
    assert first != next_version
    assert client.get(f'/api/materials/versions/{first}').json()['text_snapshot'] == text
    job = setup_job(client)
    assert client.put(f'/api/jobs/{job}/application', json={'stage': 'resume_sent', 'material_version_id': first}).status_code == 200
    assert client.put(f'/api/jobs/{job}/application', json={'stage': 'contacted'}).status_code == 200
    assert client.get(f'/api/jobs/{job}').json()['application']['material_version_id'] == first
    assert client.get(f'/api/materials/versions/{first}/download').content.decode() == text
    assert client.get('/api/export').status_code == 200


def test_file_snapshots_are_stored_and_downloaded_without_path_traversal(client):
    payload = b'%PDF-1.4\nexample snapshot\n%%EOF'
    saved = client.post('/api/materials/versions/file?name=resume&filename=resume.pdf', content=payload)
    assert saved.status_code == 201, saved.text
    ident = saved.json()['id']
    assert client.get(f'/api/materials/versions/{ident}/download').content == payload
    assert client.post('/api/materials/versions/file?name=x&filename=evil.exe', content=b'MZ').status_code == 422
    assert client.post('/api/materials/versions/file?name=x&filename=fake.pdf', content=b'MZ').status_code == 422


def test_empty_workspace_example_is_explicit_and_cannot_overwrite_jobs(client):
    created = client.post('/api/examples/install')
    assert created.status_code == 200, created.text
    jobs = client.get('/api/jobs').json()
    assert len(jobs) == 3 and all('【示例】' in j['title'] for j in jobs)
    finding = client.get('/api/analysis/findings').json()[0]
    assert finding['review_status'] == 'current'
    adopted = client.post(f"/api/analysis/findings/{finding['id']}/adopt-learning-task")
    assert adopted.status_code == 200, adopted.text
    task = client.get('/api/learning').json()[0]
    assert len(task['linked_jobs']) == len(task['linked_requirements']) == 3
    assert client.post('/api/examples/install').status_code == 409


def test_manual_review_can_replace_a_deleted_evidence_citation(client):
    _, _, original, finding = analyzed(client)
    assert client.delete(f'/api/evidence/{original}').status_code == 200
    replacement = add_evidence(client, description='重新整理的真实作品')
    assessment = {**finding['assessments'][0], 'personal_evidence_ids': [replacement]}
    reviewed = client.post(f"/api/analysis/findings/{finding['id']}/review", json={
        'expected_revision': finding['revision'], 'state': 'current', 'assessments': [assessment]})
    assert reviewed.status_code == 200, reviewed.text
    saved = client.get('/api/analysis/findings').json()[0]
    assert saved['review_status'] == 'current'
    assert saved['original_assessments'][0]['personal_evidence_ids'] == [original]
    assert saved['assessments'][0]['personal_evidence_ids'] == [replacement]


def test_self_described_ability_saves_without_link_or_proof_status(client):
    saved = client.post('/api/evidence', json={'skill': 'Excel', 'description': '我会使用 Excel 处理日常数据'})
    assert saved.status_code == 201, saved.text
    [item] = client.get('/api/evidence').json()
    assert item['description'] == '我会使用 Excel 处理日常数据'
    assert item['source_url'] == '' and item['evidence_type'] == 'self_report'
    assert item['review_status'] == 'documented'


def test_updated_personal_material_does_not_block_using_a_suggestion(client):
    _, req, ident, finding = analyzed(client)
    item = client.get('/api/evidence').json()[0]
    data = {key: item[key] for key in ('skill', 'skills', 'description', 'proficiency', 'evidence_type', 'source_url', 'review_status')}
    data.update(description='我补充了新的能力描述', expected_revision=item['revision'])
    assert client.patch(f'/api/evidence/{ident}', json=data).status_code == 200
    assert client.get('/api/analysis/findings').json()[0]['review_status'] == 'needs_review'
    result = client.post(f"/api/analysis/findings/{finding['id']}/assessments/{req}/task")
    assert result.status_code == 200, result.text
