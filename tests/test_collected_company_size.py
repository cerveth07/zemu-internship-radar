import json

import pytest

from app.company_profile import parse_headcount, collected_evidence, valid_company_source
from app.capture import _enqueue_from_adapter
from app.db import connect
from app.external_collectors import normalize_bosshunter_job, normalize_shixiseng_job
from test_company_profiles import client
from test_discovery import store, snapshot


@pytest.mark.parametrize('value,bounds', [
    ('0-20人', (0, 20)), ('公司规模： 20–99 人', (20, 99)),
    ('少于15人', (None, 14)), ('1万人以上', (10000, None)),
    ('1-2万人', (10000, 20000)), ('10,000+人', (10000, None)),
    ('５００－２０００人', (500, 2000)), ('2000人以上', (2000, None)),
])
def test_public_scale_keeps_original_range(value, bounds):
    assert parse_headcount(value) == (*bounds, value)


@pytest.mark.parametrize('value', ['200元/天', '招聘20人', '1000个在招职位', '注册资本1000万',
                                  '世界500强', '\ue123-\ue124人', '未知', '0人', '1.2人', '100-1万人'])
def test_unrelated_counts_and_unreadable_labels_are_not_company_scale(value):
    assert parse_headcount(value) is None


@pytest.mark.parametrize('platform,normalizer,url,profile', [
    ('boss', normalize_bosshunter_job, 'https://www.zhipin.com/job_detail/demo.html',
     'https://www.zhipin.com/gongsi/acme.html'),
    ('shixiseng', normalize_shixiseng_job, 'https://www.shixiseng.com/intern/inn_demo',
     'https://www.shixiseng.com/com/com_acme'),
])
def test_company_page_citation_survives_capture_and_commit(client, platform, normalizer, url, profile):
    batch = client.post('/api/capture/batches', json={'label': '规模', 'platform': platform}).json()['id']
    raw = {'url': url, 'company': '对应企业', 'title': '财务实习', 'jd': '整理财务报表',
           'company_headcount': '0-20人', 'company_headcount_source_url': profile + '?tracking=removed',
           'company_headcount_observed_at': '2026-11-11T05:00:00Z', 'company_sector': '制造业',
           'observed_at': '2026-11-10T01:00:00Z'}
    with connect() as conn:
        assert _enqueue_from_adapter(conn, batch, normalizer(raw))
        item = conn.execute('SELECT * FROM capture_items').fetchone()
        draft = json.loads(item['draft_json'])
        assert draft['company_evidence']['headcount_source_url'] == profile
    response = client.post(f"/api/capture/items/{item['id']}/commit", json={
        'company': raw['company'], 'title': raw['title'], 'jd_text': raw['jd'], 'source_url': url})
    assert response.status_code == 200, response.text
    [company] = client.get('/api/companies').json()
    assert (company['headcount_min'], company['headcount_max']) == (0, 20)
    assert company['headcount_source_url'] == profile
    assert company['sector_source_url'] == url
    assert company['headcount_observed_on'] == '2026-11-11'
    # Existing sourced data stays under user control on a later collection.
    assert client.patch('/api/companies/' + str(company['id']), json={
        'headcount_text': '100-499人', 'headcount_source_url': 'https://example.org/annual-report'}).status_code == 200
    second = dict(raw, url=url.replace('demo', 'demo2'), title='另一财务实习')
    with connect() as conn:
        assert _enqueue_from_adapter(conn, batch, normalizer(second))
        second_id = conn.execute('SELECT max(id) FROM capture_items').fetchone()[0]
    assert client.post(f'/api/capture/items/{second_id}/commit', json={
        'company': second['company'], 'title': second['title'], 'jd_text': second['jd'], 'source_url': second['url']}).status_code == 200
    assert client.get('/api/companies').json()[0]['headcount_text'] == '100-499人'


def test_company_source_is_specific_to_the_platform_and_not_a_job_counter():
    job = 'https://www.zhipin.com/job_detail/example.html'
    fields = {'company': '甲', 'company_headcount': '100-499人',
              'company_headcount_source_url': 'https://evil.test/gongsi/a.html'}
    assert collected_evidence(fields, job) == {}
    assert valid_company_source('https://www.zhipin.com/gongsi/job/acme.html', 'boss', company_only=True) == ''
    assert valid_company_source('https://www.shixiseng.com/com/com_acme', 'boss') == ''
    assert valid_company_source('https://user@www.zhipin.com/gongsi/acme.html', 'boss') == ''
    assert valid_company_source('https://www.zhipin.com:8443/gongsi/acme.html', 'boss') == ''


def test_company_visits_are_recorded_separately_from_job_details(store):
    from app import discovery
    conn, run = store
    data = snapshot()
    data['receipts'].append({'id': 'company1', 'kind': 'company', 'status': 'completed',
        'started_at': data['updated_at'], 'finished_at': data['updated_at'],
        'url': 'https://www.shixiseng.com/com/com_acme'})
    discovery.ingest_snapshot(conn, run, data)
    costs = discovery.receipt_costs(conn, run['id'])
    assert costs['company_attempts'] == 1 and costs['detail_attempts'] == 1
    data['receipts'][-1]['url'] = 'https://unrelated.test/company'
    with pytest.raises(ValueError):
        discovery.ingest_snapshot(conn, run, data)


@pytest.mark.parametrize('label,profile', [
    ('BOSS', 'https://www.zhipin.com/gongsi/acme.html'),
    ('实习僧', 'https://www.shixiseng.com/com/com_acme'),
])
def test_local_snapshot_accepts_bounded_company_receipts(label, profile):
    import time
    from app.local_collector import validate_snapshot
    task = 'a' * 32
    data = {'task_id': task, 'run_id': task, 'status': 'completed', 'items': [],
        'heartbeat': time.time(), 'candidates': [], 'receipts': [{'id': 'company1', 'kind': 'company',
            'status': 'completed', 'url': profile, 'started_at': '2026-11-11T01:00:00Z', 'finished_at': '2026-11-11T01:00:01Z'}]}
    validate_snapshot(data, task, canonical_url=lambda _: False, list_url=lambda _: False, label=label)
    data['receipts'][0]['url'] = 'https://unrelated.test/company'
    with pytest.raises(RuntimeError):
        validate_snapshot(data, task, canonical_url=lambda _: False, list_url=lambda _: False, label=label)


def test_polling_waits_for_company_fields_but_interruption_keeps_the_saved_jd():
    import copy
    from app.local_collector import page_results
    ready = {'title': '第一岗', 'jd': '第一岗原文'}
    pending = {'title': '第二岗', 'jd': '已保存的完整 JD', 'company_lookup_pending': True}
    data = {'status': 'running', 'items': [ready, pending], 'total': 2}
    live = page_results(copy.deepcopy(data), 200, 0)
    assert live['items'] == [ready] and live['total'] == 1
    terminal = page_results(dict(copy.deepcopy(data), status='failed'), 200, 1)
    assert terminal['items'] == [pending] and terminal['total'] == 2
    pending.pop('company_lookup_pending')
    pending['company_headcount'] = '100-499人'
    resumed = page_results(data, 200, 1)
    assert resumed['items'][0]['company_headcount'] == '100-499人'


def test_recollecting_existing_job_fills_missing_scale_without_another_job(client):
    batch = client.post('/api/capture/batches', json={'label': '重新采集', 'platform': 'boss'}).json()['id']
    raw = {'url': 'https://www.zhipin.com/job_detail/recollect.html', 'company': '原有企业', 'title': '财务实习', 'jd': '原始完整 JD'}
    with connect() as conn:
        assert _enqueue_from_adapter(conn, batch, normalize_bosshunter_job(raw))
        ident = conn.execute('SELECT id FROM capture_items').fetchone()[0]
    assert client.post(f'/api/capture/items/{ident}/commit', json={
        'company': raw['company'], 'title': raw['title'], 'jd_text': raw['jd'], 'source_url': raw['url']}).status_code == 200
    assert client.get('/api/companies').json()[0]['headcount_source_url'] == ''
    with connect() as conn:
        assert not _enqueue_from_adapter(conn, batch, normalize_bosshunter_job(dict(raw, company_headcount='20-99人')))
    assert len(client.get('/api/jobs').json()) == 1
    assert client.get('/api/companies').json()[0]['headcount_text'] == '20-99人'


def test_recollection_keeps_user_edits_and_cannot_move_facts_to_a_renamed_company(client):
    batch = client.post('/api/capture/batches', json={'label': '保留修改', 'platform': 'boss'}).json()['id']
    raw = {'url': 'https://www.zhipin.com/job_detail/recollect.html', 'company': '原企业', 'title': '财务实习', 'jd': '原始 JD'}
    with connect() as conn:
        _enqueue_from_adapter(conn, batch, normalize_bosshunter_job(raw))
        ident = conn.execute('SELECT id FROM capture_items').fetchone()[0]
    assert client.patch(f'/api/capture/items/{ident}', json={'jd_text': '用户整理后的 JD'}).status_code == 200
    with connect() as conn:
        _enqueue_from_adapter(conn, batch, normalize_bosshunter_job(dict(raw, company_headcount='20-99人')))
        saved = json.loads(conn.execute('SELECT draft_json FROM capture_items').fetchone()[0])
        assert saved['jd_text'] == '用户整理后的 JD'
    assert client.patch(f'/api/capture/items/{ident}', json={'company': '用户另选的企业'}).status_code == 200
    with connect() as conn:
        _enqueue_from_adapter(conn, batch, normalize_bosshunter_job(dict(raw, company_headcount='1000人以上')))
        saved = json.loads(conn.execute('SELECT draft_json FROM capture_items').fetchone()[0])
        assert saved['company'] == '用户另选的企业'
        assert saved['company_evidence']['headcount_text'] == '20-99人'
