import time

from app.db import connect
from test_company_profiles import client
from test_company_lists import import_list, detail, company


def test_excel_empty_first_column_is_not_a_company(client):
    data = {'name': '列检查', 'content': '正常企业\n\t上海\n\t北京'}
    preview = client.post('/api/company-lists/import/preview', json=data).json()
    assert not preview['valid']
    assert len(preview['errors']) == 2 and all('请只复制企业名称一列' in e for e in preview['errors'])
    assert preview['rows'][1]['company_name'] == ''
    assert '第 2 行' in preview['errors'][0]
    assert client.post('/api/company-lists/import/commit', json={**data, 'preview_token': preview['preview_token']}).status_code == 422
    assert len(client.get('/api/company-lists').json()['lists']) == 1


def test_cancelled_entries_are_not_pending(client):
    ident = company(client, '腾讯')
    saved, _ = import_list(client, '深圳市腾讯计算机系统有限公司')
    entry = detail(client, saved['id'])['entries'][0]
    assert entry['match_status'] == 'pending'
    assert client.put(f'/api/company-lists/{saved["id"]}/entries/{entry["id"]}/matches', json={'company_ids': []}).status_code == 200
    result = detail(client, saved['id'])
    assert result['cancelled_count'] == 1 and result['pending_count'] == 0
    assert result['entries'][0]['match_status'] == 'cancelled'
    assert client.put(f'/api/company-lists/{saved["id"]}/entries/{entry["id"]}/matches', json={'company_ids': [ident]}).status_code == 200
    assert detail(client, saved['id'])['cancelled_count'] == 0


def test_example_can_reinstall_with_retained_company_observations(client):
    assert client.post('/api/examples/install').status_code == 200
    [row] = client.get('/api/companies').json()
    observation = {'event_type': 'note', 'observed_on': '2026-11-12', 'description': '我的观察记录'}
    assert client.post(f'/api/companies/{row["id"]}/observations', json=observation).status_code == 201
    assert client.post('/api/examples/remove').status_code == 200
    assert client.post('/api/examples/install').status_code == 200
    assert client.get('/api/companies').json()[0]['id'] == row['id']
    assert client.get(f'/api/companies/{row["id"]}/observations').json()[0]['description'] == observation['description']


def test_thousand_entry_first_page_only_expands_visible_candidates(client, monkeypatch):
    import app.company_lists as module
    ident = client.post('/api/company-lists', json={'name': '千家性能样本'}).json()['id']
    with connect() as db:
        db.executemany('INSERT INTO companies(name) VALUES(?)', [(f'目标科技企业{i:04d}有限公司',) for i in range(1000)])
        for i in range(1000):
            module.insert_entry(db, ident, i+1, {'company_name': f'目标科技{i:04d}有限公司'})
    expanded = []
    original = module.entry_candidates
    def counted(entry, context):
        expanded.append(entry['id'])
        return original(entry, context)
    monkeypatch.setattr(module, 'entry_candidates', counted)
    start = time.perf_counter()
    result = client.get(f'/api/company-lists/{ident}').json()
    elapsed = time.perf_counter()-start
    print(f'1000 entries / 1000 employers, first page: {elapsed:.3f}s; expanded {len(expanded)} rows')
    assert result['entry_count'] == result['filtered_count'] == 1000
    assert len(result['entries']) == 20 and len(expanded) <= 20
    next_page = client.get(f'/api/company-lists/{ident}', params={'page': 2}).json()
    assert next_page['entries'][0]['position'] == 21
    assert not {e['id'] for e in result['entries']} & {e['id'] for e in next_page['entries']}
    matched = client.get(f'/api/company-lists/{ident}', params={'q': '0999', 'status': 'pending'}).json()
    assert matched['filtered_count'] == 1
