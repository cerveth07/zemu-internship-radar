import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.capture import _enqueue_from_adapter
from app.db import connect
from app.external_collectors import normalize_bosshunter_job, normalize_shixiseng_job
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'profiles.sqlite'))
    with TestClient(app) as client:
        yield client


def test_company_evidence_and_boundaries_persist(client):
    company = client.post('/api/companies', json={
        'name': '样例审计公司', 'sector': '专业服务',
        'sector_source_url': 'https://example.org/about', 'sector_observed_on': '2026-11-01',
        'headcount_text': '150-500人', 'headcount_source_url': 'https://example.org/report',
    })
    assert company.status_code == 201, company.text
    cid = company.json()['id']
    [row] = client.get('/api/companies').json()
    assert (row['headcount_min'], row['headcount_max']) == (150, 500)
    assert row['sector_observed_on'] == '2026-11-01'
    assert row['headcount_observed_on']
    assert client.patch(f'/api/companies/{cid}', json={'headcount_min': 501}).status_code == 422
    assert client.patch(f'/api/companies/{cid}', json={'sector_source_url': 'javascript:alert(1)'}).status_code == 422
    assert client.patch(f'/api/companies/{cid}', json={'sector': '金融'}).status_code == 200
    [row] = client.get('/api/companies').json()
    assert row['sector_source_url'] == row['sector_observed_on'] == ''
    assert row['headcount_source_url'] == 'https://example.org/report'
    updated = client.patch(f'/api/companies/{cid}', json={
        'headcount_text': '200-800人', 'headcount_min': 150, 'headcount_max': 500,
        'headcount_source_url': 'https://example.org/new-report',
    })
    assert updated.status_code == 200, updated.text
    [row] = client.get('/api/companies').json()
    assert (row['headcount_min'], row['headcount_max'], row['headcount_text']) == (200, 800, '200-800人')
    assert client.patch(f'/api/companies/{cid}', json={
        'headcount_text': '200-800人', 'headcount_min': None, 'headcount_max': None,
    }).status_code == 200
    [row] = client.get('/api/companies').json()
    assert row['headcount_text'] == row['headcount_source_url'] == ''
    assert client.get('/api/company-size-preferences').status_code == 404


def test_old_company_rows_stay_unsourced(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.sqlite'
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(path))
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                sector TEXT, city TEXT, size_band TEXT,
                website TEXT, status TEXT, notes TEXT, created_at TEXT
            );
            INSERT INTO companies(id,name,sector,size_band) VALUES(1,'旧公司','制造业','大厂');
            PRAGMA user_version=21;
        """)
    with TestClient(app) as client:
        [row] = client.get('/api/companies').json()
        assert row['sector'] == '制造业' and 'size_band' not in row
        assert row['sector_source_url'] == row['headcount_source_url'] == ''
        assert row['headcount_min'] is None and row['headcount_max'] is None
        assert client.get('/api/company-size-preferences').status_code == 404


@pytest.mark.parametrize('normalizer,platform,url', [
    (normalize_shixiseng_job, 'shixiseng', 'https://www.shixiseng.com/intern/inn_fixture'),
    (normalize_bosshunter_job, 'boss', 'https://www.zhipin.com/job_detail/fixture.html'),
])
def test_capture_preserves_public_evidence_and_custom_direction(client, normalizer, platform, url):
    batch = client.post('/api/capture/batches', json={'label': '实习', 'platform': platform}).json()['id']
    raw = {
        'company': '甲公司', 'title': '财务实习', 'jd': '协助编制报表及财务审计', 'url': url,
        'company_sector': '金融', 'company_headcount': '2000人以上',
        'observed_at': '2026-11-01T08:00:00Z',
    }
    with connect() as conn:
        assert _enqueue_from_adapter(conn, batch, normalizer(raw))
        assert _enqueue_from_adapter(conn, batch, normalizer(dict(raw, title='第二岗', url=url + '?v=2')))
        first, second = [r[0] for r in conn.execute('SELECT id FROM capture_items ORDER BY id')]
    assert not client.get('/api/companies').json()
    payload = {'company': '甲公司', 'title': '财务实习', 'jd_text': raw['jd'],
               'direction': ' 审计与鉴证 ', 'source_url': url}
    result = client.post(f'/api/capture/items/{first}/commit', json=payload)
    assert result.json()['status'] == 'imported', result.text
    payload.update(company='乙公司', title='第二岗', source_url=url + '?v=2')
    assert client.patch(f'/api/capture/items/{second}', json={'company': '乙公司'}).status_code == 200
    result = client.post(f'/api/capture/items/{second}/commit', json=payload)
    assert result.json()['status'] == 'imported', result.text
    companies = {row['name']: row for row in client.get('/api/companies').json()}
    assert companies['甲公司']['sector_source_url'] == companies['甲公司']['headcount_source_url'] == url
    assert companies['甲公司']['headcount_min'] == 2000
    assert companies['甲公司']['headcount_max'] is None
    assert companies['甲公司']['headcount_observed_on'] == '2026-11-01'
    assert companies['乙公司']['sector_source_url'] == companies['乙公司']['headcount_source_url'] == ''
    assert len(client.get('/api/jobs', params={'direction': '审计与鉴证'}).json()) == 2
    assert client.get('/api/requirements/stats', params={'direction': '审计与鉴证'}).status_code == 200
