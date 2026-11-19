"""Reviewed snapshots, atomic intake and retry safety; no live recruiting data."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.sqlite'))
    with TestClient(app) as client:
        yield client


def batch(client):
    return client.post('/api/capture/batches', json={'platform': 'manual'}).json()['id']


def item(client, batch_id, label, **changes):
    result = client.post(f'/api/capture/batches/{batch_id}/items', json={
        'company': '测试企业', 'title': label, 'city': '北京',
        'raw_text': '  完整 JD：熟悉 Python，每周到岗三天。  ',
        'source_url': f'https://example.com/{label}'})
    assert result.status_code == 201, result.text
    ident = result.json()['id']
    if changes:
        assert client.patch(f'/api/capture/items/{ident}', json=changes).status_code == 200
    return ident


def preview(client, batch_id, ids):
    result = client.post('/api/capture/items/preview-bulk', json={'batch_id': batch_id, 'ids': ids})
    assert result.status_code == 200, result.text
    return result.json()['items']


def payload(batch_id, rows):
    return {'batch_id': batch_id, 'items': [{'id': row['id'], 'version': row['version']} for row in rows]}


def jobs(client):
    return client.get('/api/jobs').json()


def test_commit_dates_drafts_history_and_lost_response_retry(client):
    b = batch(client)
    a = item(client, b, '产品实习', deadline='2026-11-01', posted_at='2026-10-19',
             jd_text='  修订 JD：熟悉 Python。  ', duration='三个月', onsite_days='每周三天',
             salary='200 元/天', direction='pm', eligibility='在校生')
    z = item(client, b, '算法实习')
    rows = preview(client, b, [a, z])
    assert all(not row['issue'] for row in rows)
    assert jobs(client) == []  # Preview is strictly read-only.
    body = payload(b, rows)
    result = client.post('/api/capture/items/commit-bulk', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['imported'] == 2
    saved = next(j for j in jobs(client) if j['title'] == '产品实习')
    assert saved['deadline'] == '2026-11-01'
    assert saved['posted_at'] == '2026-10-19'
    assert saved['jd_text'] == '修订 JD：熟悉 Python。'
    assert saved['duration'] == '三个月'
    assert saved['eligibility'] == '在校生'
    again = client.post('/api/capture/items/commit-bulk', json=body)
    assert again.status_code == 200, again.text
    assert again.json()['imported'] == 0 and again.json()['already_processed'] == 2
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM job_history').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM job_sources').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM companies').fetchone()[0] == 1


def test_preview_blocks_missing_invalid_existing_and_within_batch_duplicates(client):
    b = batch(client)
    old = item(client, b, '已有岗位')
    client.post('/api/capture/items/commit-bulk', json=payload(b, preview(client, b, [old])))
    a = item(client, b, '相同标题', source_url='https://example.com/a')
    z = item(client, b, '不同标题', title='相同标题', source_url='https://example.com/z')
    dup = item(client, b, '链接重复', source_url='https://example.com/已有岗位')
    missing = item(client, b, '缺失', company='', jd_text='')
    malformed = item(client, b, '错误日期')
    with connect() as conn:
        conn.execute('UPDATE capture_items SET draft_json=? WHERE id=?',
                     (json.dumps({'company': '测试', 'title': '错误日期', 'deadline': '2026-99-99'}), malformed))
    rows = preview(client, b, [a, z, dup, missing, malformed])
    assert all(row['issue'] for row in rows)
    assert '相似岗位' in rows[0]['issue'] and '相似岗位' in rows[1]['issue']
    assert '来源已收录' in rows[2]['issue']
    assert '完整 JD' in rows[3]['issue']
    assert '截止日期' in rows[4]['issue']
    assert len(jobs(client)) == 1
    assert client.get('/api/reviews').json() == []


@pytest.mark.parametrize('change', ['draft', 'state', 'batch', 'new_duplicate'])
def test_changes_after_preview_roll_back_entire_selection(client, change):
    b = batch(client)
    a, z = item(client, b, '岗位 A'), item(client, b, '岗位 Z')
    body = payload(b, preview(client, b, [a, z]))
    before = 0
    if change == 'draft':
        client.patch(f'/api/capture/items/{z}', json={'salary': '已变更'})
    elif change == 'state':
        client.patch(f'/api/capture/items/{z}/disposition', json={'action': 'defer'})
    elif change == 'batch':
        client.patch(f'/api/capture/batches/{b}', json={'action': 'pause'})
    else:
        c = client.post('/api/companies', json={'name': '另一企业'}).json()['id']
        client.post('/api/jobs', json={'company_id': c, 'title': '另一岗位',
                    'source_url': 'https://example.com/岗位 Z'})
        before = 1
    result = client.post('/api/capture/items/commit-bulk', json=body)
    assert result.status_code == 409, result.text
    assert len(jobs(client)) == before
    records = client.get(f'/api/capture/batches/{b}/items').json()
    assert next(row for row in records if row['id'] == a)['status'] == 'pending'
    with connect() as conn:
        assert not conn.execute("SELECT 1 FROM companies WHERE name='测试企业'").fetchone()


@pytest.mark.parametrize('same', ['source', 'identity'])
def test_caller_cannot_bypass_duplicate_check_by_previewing_separately(client, same):
    b = batch(client)
    a = item(client, b, 'A')
    z = item(client, b, 'Z', **({'source_url': 'https://example.com/A'} if same == 'source' else {'title': 'A'}))
    rows = preview(client, b, [a]) + preview(client, b, [z])
    result = client.post('/api/capture/items/commit-bulk', json=payload(b, rows))
    assert result.status_code == 409, result.text
    assert jobs(client) == []
    assert client.get('/api/reviews').json() == []


def test_only_selected_rows_committed_and_nonpending_does_not_reopen(client):
    b = batch(client)
    a, z = item(client, b, 'A'), item(client, b, 'Z')
    rows = preview(client, b, [a, z])
    result = client.post('/api/capture/items/commit-bulk', json=payload(b, rows[:1]))
    assert result.status_code == 200
    assert len(jobs(client)) == 1
    assert preview(client, b, [a])[0]['issue']
    assert not preview(client, b, [z])[0]['issue']


def test_wrong_batch_duplicate_ids_limit_and_unknown_ids(client):
    b, other = batch(client), batch(client)
    a = item(client, b, 'A')
    rows = preview(client, b, [a])
    for ids in [[], [a, a], [0], list(range(1, 102))]:
        assert client.post('/api/capture/items/preview-bulk', json={'batch_id': b, 'ids': ids}).status_code == 422
    assert client.post('/api/capture/items/preview-bulk', json={'batch_id': other, 'ids': [a]}).status_code == 409
    assert client.post('/api/capture/items/preview-bulk', json={'batch_id': b, 'ids': [9999]}).status_code == 404
    assert client.post('/api/capture/items/commit-bulk', json=payload(other, rows)).status_code == 409
    assert client.post('/api/capture/items/commit-bulk', json=payload(b, rows * 2)).status_code == 422
    assert client.post('/api/capture/items/commit-bulk', json={'batch_id': b, 'items': []}).status_code == 422
    assert jobs(client) == []


def test_screenshot_change_invalidates_snapshot_even_without_text_change(client):
    b = batch(client)
    a = item(client, b, 'A')
    rows = preview(client, b, [a])
    with connect() as conn:
        conn.execute("UPDATE capture_items SET screenshot_file='changed.png' WHERE id=?", (a,))
    assert client.post('/api/capture/items/commit-bulk', json=payload(b, rows)).status_code == 409
    assert jobs(client) == []
