"""Atomic detail saves and durable capture review drafts."""
import json

from fastapi.testclient import TestClient
import pytest

from app.db import connect
from app.main import app


def client_for(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'review.sqlite'))
    return TestClient(app, raise_server_exceptions=False)


def new_job(client):
    response = client.post('/api/companies', json={'name': '测试企业'})
    assert response.status_code == 201, response.text
    response = client.post('/api/jobs', json={
        'company_id': response.json()['id'], 'title': '原始岗位', 'jd_text': '原始 JD',
    })
    assert response.status_code == 201, response.text
    return response.json()['id']


def application(**changes):
    return {'stage': 'contacted', 'resume_version': 'Agent v2', 'recruiter': '招聘同事',
            'last_contact': '2026-10-08', 'next_action': '准备面试', 'notes': '周五沟通', **changes}


def test_combined_job_and_application_save_and_noop(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        job_id = new_job(client)
        payload = {'title': '已核对岗位', 'priority': 'high', 'application': application()}
        response = client.patch(f'/api/jobs/{job_id}', json=payload)
        assert response.status_code == 200, response.text
        assert response.json() == {'ok': True, 'changed': True}
        saved = client.get(f'/api/jobs/{job_id}').json()
        assert saved['title'] == payload['title'] and saved['priority'] == 'high'
        assert {key: saved['application'][key] for key in application()} == application()
        updates = {entry['event']: json.loads(entry['payload_json']) for entry in saved['history']}
        assert updates['updated']['after'] == {'title': '已核对岗位', 'priority': 'high'}
        assert updates['application_updated'] == {**application(), 'applied_on': '', 'follow_up_on': '', 'material_version_id': None}
        assert client.patch(f'/api/jobs/{job_id}', json=payload).json()['changed'] is False
        assert client.put(f'/api/jobs/{job_id}/application', json=application()).status_code == 200
        assert client.get(f'/api/jobs/{job_id}').json() == saved
        # An application-only edit must work without a job SQL update.
        assert client.patch(f'/api/jobs/{job_id}', json={
            'application': application(stage='interview'),
        }).json()['changed'] is True
        assert client.get(f'/api/jobs/{job_id}').json()['application']['stage'] == 'interview'


@pytest.mark.parametrize('existing_application', [False, True])
def test_application_write_failure_rolls_back_job_and_history(tmp_path, monkeypatch, existing_application):
    with client_for(tmp_path, monkeypatch) as client:
        job_id = new_job(client)
        if existing_application:
            assert client.put(f'/api/jobs/{job_id}/application', json=application()).status_code == 200
        before = client.get(f'/api/jobs/{job_id}').json()
        operation = 'UPDATE' if existing_application else 'INSERT'
        with connect() as conn:
            conn.execute(f'''CREATE TRIGGER reject_application BEFORE {operation} ON applications
                BEGIN SELECT RAISE(ABORT, 'application write failed'); END''')
        response = client.patch(f'/api/jobs/{job_id}', json={
            'title': '不得保存', 'priority': 'high', 'application': application(stage='interview'),
        })
        assert response.status_code == 500
        assert client.get(f'/api/jobs/{job_id}').json() == before


@pytest.mark.parametrize('invalid_application', [{'stage': 'invalid'}, {'last_contact': None}])
def test_invalid_application_does_not_save_job_or_history(tmp_path, monkeypatch, invalid_application):
    with client_for(tmp_path, monkeypatch) as client:
        job_id = new_job(client)
        before = client.get(f'/api/jobs/{job_id}').json()
        response = client.patch(f'/api/jobs/{job_id}', json={
            'title': '不得保存', 'application': invalid_application,
        })
        assert response.status_code == 422
        assert client.get(f'/api/jobs/{job_id}').json() == before


def test_job_edits_and_default_application_do_not_create_tracking_row(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        job_id = new_job(client)
        assert client.patch(f'/api/jobs/{job_id}', json={'title': '原始岗位'}).json()['changed'] is False
        assert client.patch(f'/api/jobs/{job_id}', json={'title': '新岗位'}).json()['changed'] is True
        assert client.patch(f'/api/jobs/{job_id}', json={'application': {}}).json()['changed'] is False
        assert client.patch(f'/api/jobs/{job_id}', json={
            'priority': 'high', 'application': {'stage': 'not_applied'},
        }).json()['changed'] is True
        before = client.get(f'/api/jobs/{job_id}').json()
        assert client.put(f'/api/jobs/{job_id}/application', json={}).status_code == 200
        assert client.get(f'/api/jobs/{job_id}').json() == before
        assert before['application'] is None
        assert not any(entry['event'] == 'application_updated' for entry in before['history'])
        assert client.get('/api/applications').json() == []


def test_job_only_patch_preserves_contact_date_and_application_notes(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        job_id = new_job(client)
        assert client.put(f'/api/jobs/{job_id}/application', json=application()).status_code == 200
        before = client.get(f'/api/jobs/{job_id}').json()['application']
        response = client.patch(f'/api/jobs/{job_id}', json={'notes': '岗位备注', 'city': '北京'})
        assert response.status_code == 200, response.text
        saved = client.get(f'/api/jobs/{job_id}').json()
        assert saved['application'] == before
        assert saved['application']['last_contact'] == '2026-10-08'
        assert saved['application']['notes'] == '周五沟通'
        assert saved['notes'] == '岗位备注'


def test_complete_capture_review_draft_survives_reload_without_commit(tmp_path, monkeypatch):
    raw_text = '最初复制的岗位原文\n尚未审核'
    raw_url = 'https://example.com/original'
    draft = {'company': '审核企业', 'title': '审核岗位', 'city': '上海',
             'direction': 'research', 'salary': '300 元/天', 'eligibility': '硕士在读',
             'jd_text': '人工审核后的完整 JD', 'source_url': 'https://example.com/reviewed'}
    with client_for(tmp_path, monkeypatch) as client:
        response = client.post('/api/capture/batches', json={'label': '草稿审核'})
        assert response.status_code == 201, response.text
        batch_id = response.json()['id']
        response = client.post(f'/api/capture/batches/{batch_id}/items', json={
            'raw_text': raw_text, 'source_url': raw_url,
        })
        assert response.status_code == 201, response.text
        item_id = response.json()['id']
        response = client.patch(f'/api/capture/items/{item_id}', json=draft)
        assert response.status_code == 200, response.text
        assert response.json()['draft'] == draft
    with client_for(tmp_path, monkeypatch) as client:
        item = client.get(f'/api/capture/batches/{batch_id}/items').json()[0]
        assert item['draft'] == draft
        assert item['raw_text'] == raw_text and item['source_url'] == raw_url
        assert item['status'] == 'pending' and item['job_id'] is None
        assert item['confirmed_json'] == ''
        assert client.get('/api/dashboard').json()['jobs'] == 0
        assert client.get('/api/capture/batches').json()[0]['remaining'] == 1
        for invalid in ({'direction': ' '}, {'direction': 'x' * 81}, {'salary': 'x' * 201}, {'eligibility': 'x' * 1001}):
            assert client.patch(f'/api/capture/items/{item_id}', json=invalid).status_code == 422
        assert client.get(f'/api/capture/batches/{batch_id}/items').json()[0]['draft'] == draft
