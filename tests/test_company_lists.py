import csv
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app.company_lists import builtin_data, BUILTIN_KEY
from app.db import connect, init_db
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'lists.sqlite'))
    with TestClient(app) as client:
        yield client


def detail(client, ident=1):
    response = client.get(f'/api/company-lists/{ident}', params={'page_size': 100})
    assert response.status_code == 200, response.text
    data = response.json()
    for page in range(2, data['pages'] + 1):
        data['entries'].extend(client.get(f'/api/company-lists/{ident}', params={'page_size': 100, 'page': page}).json()['entries'])
    return data


def import_list(client, content, name='我的名单', format='text', target=None):
    data = {'name': name, 'format': format, 'content': content, 'target_id': target}
    preview = client.post('/api/company-lists/import/preview', json=data)
    assert preview.status_code == 200, preview.text
    assert preview.json()['valid'], preview.json()
    response = client.post('/api/company-lists/import/commit', json={**data, 'preview_token': preview.json()['preview_token']})
    assert response.status_code == 201, response.text
    return response.json(), preview.json()


def company(client, name, title=None, **job_fields):
    response = client.post('/api/companies', json={'name': name})
    assert response.status_code == 201, response.text
    ident = response.json()['id']
    if title:
        response = client.post('/api/jobs', json={'company_id': ident, 'title': title, **job_fields})
        assert response.status_code == 201, response.text
    return ident


def test_synthetic_list_is_complete_ordered_and_offline(client):
    data = builtin_data()
    rows = data['entries']
    assert len(rows) == 100 == len({r['company_name'] for r in rows})
    assert [r['company_name'] for r in rows[:5]] == ['合成样例一号有限公司', '合成样例二号有限公司', '合成样例三号有限公司', '合成样例四号有限公司', '虚构青穹数据有限公司']
    assert rows[-1]['company_name'] == '合成样例百号有限公司'
    assert data['publisher'] == '项目合成样例' and not data['source_url']
    assert '虚构' in data['order_note']
    assert all(r['business_brands'] and r['region'] for r in rows)
    first = detail(client)
    assert first['builtin'] and not first['customized'] and first['entry_count'] == 100
    assert first['matched_count'] == first['job_count'] == 0
    assert client.get('/api/companies').json() == client.get('/api/jobs').json() == []
    init_db()
    assert len(client.get('/api/company-lists').json()['lists']) == 1
    assert detail(client)['entry_count'] == 100
    original = client.get('/api/company-lists/builtin.csv')
    assert original.content.startswith(b'\xef\xbb\xbf')
    csv_rows = list(csv.DictReader(io.StringIO(original.content.decode('utf-8-sig'))))
    assert len(csv_rows) == 100
    assert [r['企业名称'] for r in csv_rows] == [r['company_name'] for r in rows]
    assert [r['主要业务和品牌'] for r in csv_rows] == [r['business_brands'] for r in rows]
    assert all(r['来源链接'] == data['source_url'] and r['发布日期'] == '2026-11-18' for r in csv_rows)
    template = client.get('/api/company-lists/template.csv').content
    assert template.startswith(b'\xef\xbb\xbf')
    assert list(csv.reader(io.StringIO(template.decode('utf-8-sig')))) == [['企业名称', '别名', '主要业务和品牌', '地区', '来源机构', '来源链接', '报告链接', '发布日期']]


@pytest.mark.parametrize('format,content', [
    ('text', '企业名称\r\n甲公司\r\n乙公司\r\n甲公司\r\n'),
    ('text', '甲公司\t\r\n乙公司\t\r\n甲公司\t'),
    ('csv', '\ufeff企业名称\r\n甲公司\r\n乙公司\r\n甲公司\r\n'),
    ('csv', '甲公司\n乙公司\n甲公司'),
])
def test_name_only_csv_text_and_excel_column_imports(client, format, content):
    saved, preview = import_list(client, content, format=format)
    assert preview['new_count'] == 2 and preview['duplicate_count'] == 1
    assert preview['rows'][-1]['status'] == 'duplicate'
    assert saved['added'] == 2
    assert [e['company_name'] for e in detail(client, saved['id'])['entries']] == ['甲公司', '乙公司']
    assert not client.get('/api/companies').json() and not client.get('/api/jobs').json()


def test_optional_csv_fields_and_original_csv_can_be_reimported(client):
    saved, _ = import_list(client, '企业名称,别名,主要业务和品牌,地区,来源链接\n甲公司,甲品牌;甲简称,"业务A,业务B",浙江省,https://example.org/about\n', format='csv')
    [entry] = detail(client, saved['id'])['entries']
    assert entry['aliases'] == ['甲品牌', '甲简称']
    assert entry['business_brands'] == '业务A,业务B' and entry['region'] == '浙江省'
    assert entry['source_url'] == 'https://example.org/about'
    original = client.get('/api/company-lists/builtin.csv').content.decode('utf-8-sig')
    saved, _ = import_list(client, original, name='我的示例副本', format='csv')
    copied = detail(client, saved['id'])
    assert copied['entry_count'] == 100 and not copied['builtin']
    assert copied['entries'][2]['aliases'] == []  # Business brands are not aliases.


@pytest.mark.parametrize('format,content', [
    ('csv', '企业名称,地区\n有效公司,上海\n,北京'),
    ('csv', '企业名称,地区\n有效公司,上海\n坏行,北京,额外值'),
    ('csv', '企业名称,来源链接\n有效公司,https://example.org\n坏公司,javascript:alert(1)'),
    ('csv', '企业名称,地区\n"没有闭合引号,北京'),
    ('text', '有效公司\n错误公司\t第二列'),
])
def test_format_errors_never_partially_save(client, format, content):
    data = {'name': '错误输入', 'format': format, 'content': content}
    before = len(client.get('/api/company-lists').json()['lists'])
    preview = client.post('/api/company-lists/import/preview', json=data).json()
    assert not preview['valid'] and preview['errors']
    result = client.post('/api/company-lists/import/commit', json={**data, 'preview_token': preview['preview_token']})
    assert result.status_code == 422
    assert len(client.get('/api/company-lists').json()['lists']) == before
    assert not client.get('/api/companies').json()


def test_changed_content_or_target_invalidates_the_preview(client):
    data = {'name': '预览名单', 'content': '原始公司'}
    preview = client.post('/api/company-lists/import/preview', json=data).json()
    for changed in ({'content': '另一公司'}, {'name': '另一名单名称'}):
        assert client.post('/api/company-lists/import/commit', json={**data, **changed, 'preview_token': preview['preview_token']}).status_code == 409
    saved, _ = import_list(client, '原始公司')
    append = {'target_id': saved['id'], 'content': '准备追加公司'}
    preview = client.post('/api/company-lists/import/preview', json=append).json()
    import_list(client, '另一批追加公司', target=saved['id'])
    assert client.post('/api/company-lists/import/commit', json={**append, 'preview_token': preview['preview_token']}).status_code == 409
    assert detail(client, saved['id'])['entry_count'] == 2


def test_identity_matching_is_conservative_and_manual_links_are_remembered(client):
    tencent = company(client, '青穹', '财务实习')
    outsourced = company(client, '外包服务有限公司', '项目实习', jd_text='服务虚构青穹数据有限公司、青穹实验室和QQ的大厂项目')
    builtin = detail(client)
    entry = builtin['entries'][4]
    assert entry['match_status'] == 'pending' and not entry['matches']
    assert tencent in [r['id'] for r in entry['candidates']]
    assert outsourced not in [r['id'] for r in entry['candidates']]
    assert not client.get('/api/jobs', params={'company_list_id': 1}).json()
    exact = company(client, entry['company_name'], '产品运营实习', direction='产品运营')
    assert [r['id'] for r in detail(client)['entries'][4]['matches']] == [exact]
    route = f'/api/company-lists/1/entries/{entry["id"]}/matches'
    assert client.put(route, json={'company_ids': [tencent, exact]}).status_code == 200
    assert {r['company_id'] for r in client.get('/api/jobs', params={'company_list_id': 1}).json()} == {tencent, exact}
    assert len(client.get('/api/jobs', params={'company_list_id': 1, 'direction': '产品运营'}).json()) == 1
    copy, _ = import_list(client, entry['company_name'])
    assert detail(client, copy['id'])['entries'][0]['match_method'] == 'remembered'
    assert client.put(route, json={'company_ids': []}).status_code == 200
    assert detail(client)['entries'][4]['matches'] == []
    init_db()
    assert detail(client)['entries'][4]['match_method'] == 'cancelled'
    assert client.put(route, json={'company_ids': [exact]}).status_code == 200
    assert client.post('/api/jobs', json={'company_id': exact, 'title': '后加入的新岗位'}).status_code == 201
    assert detail(client)['entries'][4]['job_count'] == 2
    assert detail(client, copy['id'])['entries'][0]['matches'][0]['job_count'] == 2
    assert client.put(route, json={'company_ids': [exact, 999999]}).status_code == 409
    assert [r['id'] for r in detail(client)['entries'][4]['matches']] == [exact]


def test_nonunique_normalized_names_and_imported_aliases_are_only_candidates(client):
    company(client, 'ＡＣＭＥ 有限公司')
    company(client, 'ACME有限公司')
    company(client, '甲品牌')
    saved, _ = import_list(client, '企业名称,别名\nACME有限公司,\n甲企业有限公司,甲品牌', format='csv')
    entries = detail(client, saved['id'])['entries']
    assert all(not e['matches'] and e['match_status'] == 'pending' for e in entries)
    assert len(entries[0]['candidates']) == 2


def test_append_remove_rename_delete_and_restore_never_delete_user_records(client):
    company(client, '合成星桥公司', '财务实习')
    [job] = client.get('/api/jobs').json()
    assert client.put(f'/api/jobs/{job["id"]}/application', json={'stage': 'contacted'}).status_code == 200
    original = client.get('/api/company-lists/builtin.csv').content
    added, _ = import_list(client, '自行添加企业\n合成星桥公司', target=1)
    assert added['added'] == 1 and added['duplicates_skipped'] == 1
    current = detail(client)
    assert current['customized'] and current['entries'][-1]['origin'] == 'user'
    assert client.delete('/api/company-lists/1/entries/'+str(current['entries'][-1]['id'])).status_code == 200
    assert client.patch('/api/company-lists/1', json={'name': '改了名称的示例'}).status_code == 200
    assert detail(client)['customized']
    assert client.get('/api/company-lists/builtin.csv').content == original
    assert client.delete('/api/company-lists/1').status_code == 200
    init_db()
    assert not client.get('/api/company-lists').json()['lists']
    restored = client.post('/api/company-lists/builtin/restore').json()['id']
    assert restored != 1
    assert detail(client, restored)['entry_count'] == 100 and not detail(client, restored)['customized']
    assert len(client.get('/api/companies').json()) == len(client.get('/api/jobs').json()) == len(client.get('/api/applications').json()) == 1
    created = client.post('/api/company-lists', json={'name': '空名单'}).json()['id']
    assert detail(client, created)['entry_count'] == 0
    assert client.delete(f'/api/company-lists/{created}').status_code == 200


def test_backup_contains_lists_tombstone_and_identity_links(client, tmp_path, monkeypatch):
    ident = company(client, '手动对应主体', '制造业实习')
    saved, _ = import_list(client, '关注的企业')
    entry = detail(client, saved['id'])['entries'][0]
    client.put(f'/api/company-lists/{saved["id"]}/entries/{entry["id"]}/matches', json={'company_ids': [ident]})
    client.delete('/api/company-lists/1')
    exported = client.get('/api/export').json()
    assert exported['company_name_links'][0]['company_id'] == ident
    assert exported['company_list_defaults'][0] == {'key': BUILTIN_KEY, 'state': 'deleted'}
    backup = client.get('/api/capture/backup')
    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        restored = tmp_path / 'restored.sqlite'
        restored.write_bytes(archive.read('radar.sqlite'))
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(restored))
    init_db()
    assert client.get('/api/company-lists').json()['builtin_id'] is None
    assert detail(client, saved['id'])['entries'][0]['matches'][0]['id'] == ident
    assert detail(client, saved['id'])['job_count'] == 1
    with connect() as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_existing_database_gets_one_example_without_new_company_records(client):
    company(client, '旧企业', '旧岗位')
    with connect() as db:
        db.execute('DELETE FROM company_lists')
        db.execute('DELETE FROM company_list_defaults')
        db.execute('PRAGMA user_version=26')
    init_db()
    assert len(client.get('/api/company-lists').json()['lists']) == 1
    init_db()
    assert len(client.get('/api/companies').json()) == len(client.get('/api/jobs').json()) == 1
