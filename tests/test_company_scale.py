import json
import sqlite3
import zipfile

import pytest

from app.company_profile import headcount_matches
from app.db import connect, init_db, database_path
from test_company_profiles import client


@pytest.mark.parametrize('company,lower,upper,include_missing,expected', [
    ({'headcount_min': 100, 'headcount_max': 499}, 200, 500, False, True),
    ({'headcount_min': 100, 'headcount_max': 499}, 500, 1000, True, False),
    ({'headcount_min': 2000, 'headcount_max': None}, 1000, 3000, False, True),
    ({'headcount_min': 2000, 'headcount_max': None}, None, 1999, True, False),
    ({'headcount_min': None, 'headcount_max': 20}, 20, 100, False, True),
    ({'headcount_min': 0, 'headcount_max': 20}, 0, 0, False, True),
    ({}, 100, 500, True, True), ({}, 100, 500, False, False),
    ({}, None, None, False, False), ({}, None, None, True, True),
])
def test_numeric_range_overlap_and_missing_values(company, lower, upper, include_missing, expected):
    assert headcount_matches(company, lower, upper, include_missing) == expected


def test_manual_scale_is_saved_without_a_source_or_extra_status(client):
    company = client.post('/api/companies', json={'name': '手工录入企业', 'headcount_text': '100–499人'}).json()['id']
    job = client.post('/api/jobs', json={'company_id': company, 'title': '财务实习'}).json()['id']
    row = client.get('/api/companies').json()[0]
    assert (row['headcount_min'], row['headcount_max']) == (100, 499)
    assert row['headcount_source_url'] == ''
    assert not {'size_band', 'tier_reason'} & row.keys()
    listed = client.get('/api/jobs').json()[0]
    assert listed['company_headcount_text'] == '100–499人'
    assert 'company_tier' not in listed and 'company_tier_reason' not in listed
    package = client.post('/api/analysis/packages', json={'job_ids': [job]}).json()['package']['jobs'][0]
    assert 'size' not in package['company_profile']
    assert package['company_profile']['headcount_text'] == '100–499人'
    assert client.get('/api/company-size-preferences').status_code == 404


@pytest.mark.parametrize('send_existing_bounds', [False, True])
def test_edited_scale_text_never_reuses_unrelated_old_bounds(client, send_existing_bounds):
    company = client.post('/api/companies', json={'name': '修改规模企业', 'headcount_text': '100–499人'}).json()['id']
    client.post('/api/jobs', json={'company_id': company, 'title': '财务实习'})
    payload = {'headcount_text': '约 1000 人'}
    if send_existing_bounds:
        payload.update(headcount_min=100, headcount_max=499)
    response = client.patch(f'/api/companies/{company}', json=payload)
    assert response.status_code == 422
    assert '请填写本次人数上下限' in response.json()['detail']
    [row] = client.get('/api/companies').json()
    assert (row['headcount_text'], row['headcount_min'], row['headcount_max']) == ('100–499人', 100, 499)

    # Both a minimal PATCH and the full prefilled form must derive fresh bounds.
    payload['headcount_text'] = '1000人'
    assert client.patch(f'/api/companies/{company}', json=payload).status_code == 200
    [row] = client.get('/api/companies').json()
    assert (row['headcount_text'], row['headcount_min'], row['headcount_max']) == ('1000人', 1000, 1000)
    assert client.get('/api/requirements/stats', params={'headcount_min': 800, 'headcount_max': 1200}).json()['selected_jobs'] == 1
    assert client.get('/api/requirements/stats', params={'headcount_min': 100, 'headcount_max': 499}).json()['selected_jobs'] == 0

    # An unrecognized description is allowed when the user supplies new bounds.
    assert client.patch(f'/api/companies/{company}', json={
        'headcount_text': '约 1000 人', 'headcount_min': 800, 'headcount_max': 1200,
    }).status_code == 200
    [row] = client.get('/api/companies').json()
    assert (row['headcount_text'], row['headcount_min'], row['headcount_max']) == ('约 1000 人', 800, 1200)


def test_equivalent_scale_texts_share_one_numeric_statistics_group(client):
    texts = ['100-499人', '100–499人', '100 至 499 人']
    for index, text in enumerate(texts):
        company = client.post('/api/companies', json={'name': f'同区间企业{index}', 'headcount_text': text}).json()['id']
        client.post('/api/jobs', json={'company_id': company, 'title': '财务实习'})
    groups = client.get('/api/requirements/stats').json()['comparisons']['company_scale']
    assert len(groups) == 1
    assert groups[0]['group'] == '100–499人'
    assert groups[0]['selected_jobs'] == 3
    assert sorted(row['headcount_text'] for row in client.get('/api/companies').json()) == sorted(texts)


def test_migration_retires_categories_and_keeps_a_full_backup(client, tmp_path):
    category = client.post('/api/companies', json={'name': '旧分类企业'}).json()['id']
    numeric = client.post('/api/companies', json={'name': '旧人数企业'}).json()['id']
    actual = client.post('/api/companies', json={'name': '已有规模企业', 'headcount_text': '20-99人',
        'headcount_source_url': 'https://example.org/about'}).json()['id']
    for ident in [category, numeric, actual]:
        client.post('/api/jobs', json={'company_id': ident, 'title': '原岗位'})
    with connect() as conn:
        conn.executescript("ALTER TABLE companies ADD COLUMN size_band TEXT; ALTER TABLE companies ADD COLUMN tier_reason TEXT; CREATE TABLE company_size_preferences(id INTEGER PRIMARY KEY,small_max INTEGER,medium_max INTEGER);")
        conn.execute("UPDATE companies SET size_band='大厂',tier_reason='历史人工分类' WHERE id=?", (category,))
        conn.execute("UPDATE companies SET size_band='500-999人' WHERE id=?", (numeric,))
        conn.execute("UPDATE companies SET size_band='大厂' WHERE id=?", (actual,))
        conn.execute('INSERT INTO company_size_preferences VALUES(1,99,999)')
        conn.execute('PRAGMA user_version=25')
    init_db()
    rows = {row['id']: row for row in client.get('/api/companies').json()}
    assert rows[category]['headcount_min'] is None and rows[category]['headcount_max'] is None
    assert (rows[numeric]['headcount_min'], rows[numeric]['headcount_max']) == (500, 999)
    assert rows[actual]['headcount_text'] == '20-99人' and rows[actual]['headcount_source_url']
    assert len(client.get('/api/jobs').json()) == 3
    with connect() as conn:
        assert not {'size_band', 'tier_reason'} & {row['name'] for row in conn.execute('PRAGMA table_info(companies)')}
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='company_size_preferences'").fetchone()
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    [backup] = list(database_path().parent.joinpath('backups').glob('*.zip'))
    with zipfile.ZipFile(backup) as archive:
        old = tmp_path / 'before.sqlite'
        old.write_bytes(archive.read('radar.sqlite'))
    with sqlite3.connect(old) as conn:
        assert conn.execute('SELECT size_band FROM companies WHERE id=?', (category,)).fetchone()[0] == '大厂'
        assert conn.execute('SELECT count(*) FROM company_size_preferences').fetchone()[0] == 1
