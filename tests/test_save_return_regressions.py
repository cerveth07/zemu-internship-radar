import base64
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db
from app.main import app
from test_career_workspace import analyzed


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'save-return.sqlite'))
    with TestClient(app) as instance:
        yield instance


def test_text_and_attachment_are_one_version_and_copy_keeps_both(client):
    content = b'%PDF-1.4\nresume\n%%EOF'
    data = {'name': '运营简历', 'text': '这版突出产品策划经历', 'filename': 'resume.pdf',
            'content_base64': base64.b64encode(content).decode()}
    created = client.post('/api/materials/versions/attachment', json=data)
    assert created.status_code == 201, created.text
    first = created.json()
    assert client.get(f"/api/materials/versions/{first['id']}").json()['text_snapshot'] == data['text']
    assert client.get(f"/api/materials/versions/{first['id']}/download").content == content
    newer = client.post('/api/materials/versions', json={'name': data['name'], 'material_id': first['material_id'],
        'copy_attachment_from': first['id'], 'text': '突出策划经历，并补充运营项目'}).json()
    assert newer['id'] != first['id']
    assert client.get(f"/api/materials/versions/{newer['id']}/download").content == content
    assert client.get(f"/api/materials/versions/{newer['id']}").json()['text_snapshot'].endswith('补充运营项目')
    assert client.get(f"/api/materials/versions/{first['id']}").json()['text_snapshot'] == data['text']
    assert client.post('/api/materials/versions', json={'name': '另一个材料', 'text': '说明',
        'copy_attachment_from': first['id']}).status_code == 422


def test_text_file_does_not_overwrite_the_users_written_text(client):
    file_text = '附件正文'
    request = {'name': '项目说明', 'filename': 'project.txt', 'text': '用户另外填写的版本说明',
               'content_base64': base64.b64encode(file_text.encode()).decode()}
    result = client.post('/api/materials/versions/attachment', json=request)
    assert result.status_code == 201, result.text
    ident = result.json()['id']
    assert client.get(f'/api/materials/versions/{ident}').json()['text_snapshot'] == request['text']
    assert client.get(f'/api/materials/versions/{ident}/download').content.decode() == file_text


def test_all_personal_entry_routes_allow_untagged_experiences(client):
    first = client.post('/api/evidence', json={'description': '我负责过一次完整的校园活动'})
    assert first.status_code == 201, first.text
    item = client.get('/api/evidence').json()[0]
    assert item['skills'] == [] and item['skill'] == ''
    imported = client.post('/api/materials/import-evidence', json={'name': '我的经历', 'text': '我整理过活动数据',
        'import_id': str(uuid.uuid4()), 'candidates': [{'source_quote': '我整理过活动数据', 'description': '我整理过活动数据'}]})
    assert imported.status_code == 200, imported.text
    task = client.post('/api/learning', json={'title': '完成复盘'}).json()['id']
    completion = client.post(f'/api/learning/{task}/complete', json={'completion_id': str(uuid.uuid4()),
        'expected_revision': 1, 'evidence': {'description': '完成了复盘说明，之后补能力标签'}})
    assert completion.status_code == 200, completion.text
    assert client.get('/api/evidence').json()[0]['skills'] == []
    assert client.post('/api/evidence', json={}).status_code == 422


def test_saved_findings_include_job_and_requirement_labels(client):
    job, req, _, finding = analyzed(client)
    assert finding['job_details'][0]['id'] == job
    assert finding['job_details'][0]['title'] and finding['job_details'][0]['company']
    assert finding['requirement_details'][0]['id'] == req
    assert finding['requirement_details'][0]['name'] == 'Python'
    assert finding['requirement_details'][0]['evidence'] == '熟悉 Python'


def test_example_exit_preserves_independent_user_content_and_promoted_experiences(client):
    fixture = client.post('/api/examples/install').json()
    seed = client.get('/api/analysis/findings').json()[0]
    task = client.post(f"/api/analysis/findings/{seed['id']}/adopt-learning-task").json()['id']
    example = client.get('/api/evidence').json()[0]
    edited = {key: example[key] for key in ('skill', 'skills', 'description', 'source_url', 'proficiency', 'evidence_type', 'review_status')}
    edited.update(expected_revision=example['revision'], description='我把这个条目改成了自己的项目经历')
    assert client.patch(f"/api/evidence/{example['id']}", json=edited).status_code == 200
    own = client.post('/api/evidence', json={'description': '我自己新增的经历'}).json()['id']
    company = client.post('/api/companies', json={'name': '【示例】也是用户自己输入的名字'}).json()['id']
    job = client.post('/api/jobs', json={'company_id': company, 'title': '自己的正式岗位', 'jd_text': '真实用户记录'}).json()['id']
    material = client.post('/api/materials/versions', json={'name': '个人简历', 'text': '我的简历'}).json()['id']
    assert client.put(f'/api/jobs/{job}/application', json={'stage': 'resume_sent', 'material_version_id': material}).status_code == 200
    # A user-created finding may still refer to a former example. Keep it as a
    # historical suggestion instead of breaking the entire findings endpoint.
    user_result = {'package_id': seed['package_id'], 'dry_run': False, 'findings': [{
        'kind': 'other', 'title': '我自己新建的分析', 'content': '我的后续想法', 'job_ids': fixture['job_ids'][:1]}]}
    assert client.post('/api/analysis/import', json=user_result).status_code == 200
    result = client.post('/api/examples/remove')
    assert result.status_code == 200, result.text
    assert [row['id'] for row in client.get('/api/jobs').json()] == [job]
    ids = {row['id'] for row in client.get('/api/evidence').json()}
    assert {example['id'], own} == ids
    assert client.get(f'/api/materials/versions/{material}').status_code == 200
    assert client.get(f'/api/jobs/{job}').json()['application']['material_version_id'] == material
    assert all(row['id'] != task for row in client.get('/api/learning').json())
    [user_finding] = client.get('/api/analysis/findings').json()
    assert user_finding['title'] == '我自己新建的分析' and user_finding['source_changed']
    assert client.get('/api/dashboard').json()['examples'] == {'active': False, 'jobs': 0}
    assert client.post('/api/examples/remove').status_code == 200
    with connect() as conn:
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_legacy_example_can_be_exited_and_own_completed_outcome_survives(client):
    client.post('/api/examples/install')
    finding = client.get('/api/analysis/findings').json()[0]
    task = client.post(f"/api/analysis/findings/{finding['id']}/adopt-learning-task").json()['id']
    result = client.post(f'/api/learning/{task}/complete', json={'completion_id': str(uuid.uuid4()),
        'expected_revision': 1, 'evidence': {'description': '我自己完成后的新成果'}}).json()
    with connect() as conn:
        conn.execute('DELETE FROM example_records')  # a pre-tracking v0.14 workspace
    init_db()
    assert client.get('/api/examples').json()['jobs'] == 3
    assert client.post('/api/examples/remove').status_code == 200
    assert client.get('/api/jobs').json() == []
    assert [row['id'] for row in client.get('/api/evidence').json()] == [result['evidence_id']]
    assert client.get('/api/learning').json()[0]['id'] == task
