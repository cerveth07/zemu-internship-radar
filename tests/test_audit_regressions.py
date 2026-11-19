"""Three-pass audit: source change, durable collector cursors, backup integrity."""
import io
import sqlite3
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.main import app
from app import external_collectors
from app.db import connect
from tools.restore_capture_bundle import restore_bundle


def client_for(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.db'))
    return TestClient(app)


def batch(client, platform='boss'):
    response = client.post('/api/capture/batches', json={'label':'审计批次', 'platform':platform})
    assert response.status_code == 201, response.text
    return response.json()['id']


def new_item(client, batch_id, url, jd):
    r = client.post(f'/api/capture/batches/{batch_id}/items', json={'raw_text':jd, 'source_url':url})
    assert r.status_code == 201, r.text
    return r.json()['id']


def commit(client, item_id, url, jd):
    payload = {'company':'测试公司','title':'Agent 实习','city':'北京','source_url':url,'jd_text':jd}
    r = client.post(f'/api/capture/items/{item_id}/commit', json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_same_url_changed_jd_queued_and_review_preserves_history(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        url = 'https://www.zhipin.com/job_detail/example.html'
        old = commit(client, new_item(client, batch(client), url, '旧 JD 内容'), url, '旧 JD 内容')
        changed = commit(client, new_item(client, batch(client), url, '新增职责：Agent 工作流'), url,
                         '新增职责：Agent 工作流')
        assert changed['status'] == 'review'
        assert changed['review_id'] is not None
        # Never mutate the original posting without human action.
        assert client.get(f"/api/jobs/{old['job_id']}").json()['jd_text'] == '旧 JD 内容'
        bad = client.post(f"/api/reviews/{changed['review_id']}/resolve", json={'action':'create_separate'})
        assert bad.status_code == 409
        ok = client.post(f"/api/reviews/{changed['review_id']}/resolve", json={'action':'link_source'})
        assert ok.status_code == 200, ok.text
        record = client.get(f"/api/jobs/{old['job_id']}").json()
        assert record['jd_text'] == '旧 JD 内容'
        events = [e for e in record['history'] if e['event'] == 'source_jd_change_reviewed']
        assert len(events) == 1 and '新增职责' in events[0]['payload_json']


@pytest.mark.parametrize(('action', 'inbox_status', 'jobs'), [
    ('link_source', 'imported', 1),
    ('create_separate', 'imported', 2),
    ('dismiss', 'dismissed', 1),
])
def test_duplicate_review_updates_inbox_and_repairs_legacy_rows(tmp_path, monkeypatch, action, inbox_status, jobs):
    first_url = 'https://www.zhipin.com/job_detail/first.html'
    other_url = 'https://www.zhipin.com/job_detail/other.html'
    jd = '协助核对财务凭证'
    with client_for(tmp_path, monkeypatch) as client:
        original = commit(client, new_item(client, batch(client), first_url, jd), first_url, jd)
        batches = [batch(client), batch(client)]
        captures = [new_item(client, bid, other_url, jd) for bid in batches]
        reviews = [commit(client, ident, other_url, jd) for ident in captures]
        assert reviews[0]['status'] == reviews[1]['status'] == 'review'
        review_id = reviews[0]['review_id']
        assert reviews[1]['review_id'] == review_id

        resolved = client.post(f'/api/reviews/{review_id}/resolve', json={'action': action})
        assert resolved.status_code == 200, resolved.text
        job_id = resolved.json()['job_id']
        if action == 'link_source':
            assert job_id == original['job_id']
        elif action == 'create_separate':
            assert job_id != original['job_id']
        else:
            assert job_id is None
        assert len(client.get('/api/jobs').json()) == jobs
        assert client.get('/api/reviews').json() == []
        for bid in batches:
            result = client.get(f'/api/capture/batches/{bid}/items').json()[0]
            assert (result['status'], result['job_id'], result['review_id']) == (inbox_status, job_id, review_id)
            counts = client.get(f'/api/capture/batches/{bid}').json()['counts']
            assert counts['review'] == 0 and counts[inbox_status] == 1

        repeat = commit(client, new_item(client, batch(client), other_url, jd), other_url, jd)
        assert repeat['status'] == ('dismissed' if action == 'dismiss' else 'duplicate')

    # Older releases kept the capture items in review after a decision was saved.
    with connect() as conn:
        conn.execute("UPDATE capture_items SET status='review',job_id=NULL WHERE id IN (?,?)", captures)
    with client_for(tmp_path, monkeypatch) as client:
        for bid in batches:
            result = client.get(f'/api/capture/batches/{bid}/items').json()[0]
            assert (result['status'], result['job_id']) == (inbox_status, job_id)


def test_collector_cursor_persists_across_restart_and_can_rescan(tmp_path, monkeypatch):
    paths = []
    def upstream(path, *, post=None):
        paths.append(path)
        from urllib.parse import parse_qs, urlsplit
        params = parse_qs(urlsplit(path).query)
        offset, limit = int(params['offset'][0]), int(params['limit'][0])
        return [{'source_platform':'boss', 'url':f'/job_detail/j{i}.html', 'title':f'实习{i}',
                 'company':'演示公司', 'jd':f'JD {i}'} for i in range(offset,min(offset+limit, 250))]
    monkeypatch.setattr(external_collectors, 'request_bosshunter', upstream)
    with client_for(tmp_path, monkeypatch) as client:
        bid = batch(client)
        first = client.post('/api/capture/connectors/bosshunter/import',
                            json={'batch_id':bid,'confirmed':True,'limit':200})
        assert first.status_code == 200, first.text
        assert first.json()['created']==200 and first.json()['next_offset']==200
    with client_for(tmp_path, monkeypatch) as client:
        second = client.post('/api/capture/connectors/bosshunter/import',
                             json={'batch_id':bid,'confirmed':True,'limit':200})
        assert second.status_code == 200, second.text
        assert second.json()['created']==50 and second.json()['next_offset']==250
        assert client.get(f'/api/capture/batches/{bid}/items').json().__len__()==250
        reset = client.post('/api/capture/connectors/bosshunter/import',
                            json={'batch_id':bid,'confirmed':True,'limit':100,'restart_from_first':True})
        assert reset.status_code == 200 and reset.json()['already_exists']==100
        assert reset.json()['next_offset']==100
        assert any('offset=200' in path for path in paths)


def test_missing_evidence_makes_complete_backup_fail(tmp_path,monkeypatch):
    with client_for(tmp_path,monkeypatch) as client:
        bid=batch(client)
        item_id=new_item(client,bid,'','')
        b=io.BytesIO();Image.new('RGB',(10,10),'white').save(b,format='PNG')
        assert client.post(f'/api/capture/items/{item_id}/screenshot',content=b.getvalue()).status_code==200
        files=list((tmp_path/'capture-evidence').iterdir())
        assert len(files)==1
        files[0].unlink()
        # Never return a ZIP labeled 'complete' that cannot restore images.
        result = client.get('/api/capture/backup')
        assert result.status_code == 409 and '截图文件缺失' in result.json()['detail']


def test_restore_rejects_incomplete_archive_before_mutating_database(tmp_path,monkeypatch):
    with client_for(tmp_path,monkeypatch) as client:
        bid=batch(client)
        item_id=new_item(client,bid,'','')
        b=io.BytesIO();Image.new('RGB',(10,10),'white').save(b,format='PNG')
        client.post(f'/api/capture/items/{item_id}/screenshot',content=b.getvalue())
        good=client.get('/api/capture/backup').content
    incomplete=tmp_path/'incomplete.zip'
    with zipfile.ZipFile(io.BytesIO(good)) as src, zipfile.ZipFile(incomplete,'w') as dest:
        for filename in src.namelist():
            if not filename.startswith('capture-evidence/'):
                dest.writestr(filename,src.read(filename))
    target=tmp_path/'dest'/'radar.db'
    with pytest.raises(ValueError,match='不一致'):
        restore_bundle(incomplete,target)
    assert not target.exists()


def test_ocr_long_result_is_not_silently_truncated(monkeypatch,tmp_path):
    from app import ocr_adapter
    class Output:
        txts=['A'*100_001]
        scores=[0.9]
    monkeypatch.setattr(ocr_adapter,'_engine',lambda:lambda _:Output())
    with pytest.raises(ValueError,match='不能静默截断'):
        ocr_adapter.recognize_file(tmp_path/'image.png')


def test_local_api_refuses_cross_origin_browser_writes(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        body={'label':'不得创建'}
        external=client.post('/api/capture/batches', json=body,
                             headers={'Origin':'https://other.example'})
        assert external.status_code == 403
        rebinding=client.post('/api/capture/batches', json=body,
                              headers={'Host':'evil.example'})
        assert rebinding.status_code == 403
        same=client.post('/api/capture/batches', json={'label':'同源可用'},
                         headers={'Host':'127.0.0.1:8785','Origin':'http://127.0.0.1:8785'})
        assert same.status_code == 201
