"""v0.6 intake durability, safety and SQLite migration regression tests."""
import os
import sqlite3

from fastapi.testclient import TestClient

from app.main import app


def client_at(tmp_path, monkeypatch):
    path=tmp_path/'capture.sqlite'
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH',str(path))
    return TestClient(app)


def batch(c):
    r=c.post('/api/capture/batches',json={'label':'北京 Agent 实习','platform':'boss','keyword':'Agent','city':'北京'})
    assert r.status_code==201,r.text
    return r.json()['id']


def item(c,b,text='完整JD：负责产品设计及大模型交互研发',url='https://www.zhipin.com/job_detail/example.html'):
    r=c.post(f'/api/capture/batches/{b}/items',json={'raw_text':text,'source_url':url})
    assert r.status_code==201,r.text
    return r.json()['id']


def confirm(**kwargs):
    return {'company':'测试 AI 创业公司','title':'Agent 产品实习生','city':'北京',
            'jd_text':'负责产品设计及大模型交互研发','source_url':'https://www.zhipin.com/job_detail/example.html',**kwargs}


def test_durable_capture_and_resume_after_restart(tmp_path,monkeypatch):
    with client_at(tmp_path,monkeypatch) as c:
        b=batch(c); i=item(c,b)
        assert c.patch(f'/api/capture/batches/{b}',json={'action':'pause'}).json()['remaining']==1
        assert c.post(f'/api/capture/items/{i}/commit',json=confirm()).status_code==409
    with client_at(tmp_path,monkeypatch) as c:
        assert c.get(f'/api/capture/batches/{b}/items').json()[0]['id']==i
        assert c.patch(f'/api/capture/batches/{b}',json={'action':'complete'}).status_code==409
        c.patch(f'/api/capture/batches/{b}',json={'action':'resume'})
        r=c.post(f'/api/capture/items/{i}/commit',json=confirm());assert r.json()['status']=='imported'
        assert c.get('/api/dashboard').json()['jobs']==1
        again=c.post(f'/api/capture/items/{i}/commit',json=confirm())
        assert again.json()['already_processed'] is True
        assert c.get('/api/dashboard').json()['jobs']==1
        assert c.patch(f'/api/capture/batches/{b}',json={'action':'complete'}).json()['remaining']==0
        assert c.post(f'/api/capture/batches/{b}/items',json={'raw_text':'JD'}).status_code==409


def test_same_input_idempotent_and_cross_batch_source_dedup(tmp_path,monkeypatch):
    with client_at(tmp_path,monkeypatch) as c:
        b=batch(c);i=item(c,b)
        assert item(c,b)==i
        assert c.post(f'/api/capture/items/{i}/commit',json=confirm()).json()['status']=='imported'
        b2=batch(c);i2=item(c,b2)
        duplicate=c.post(f'/api/capture/items/{i2}/commit',json=confirm())
        assert duplicate.json()['status']=='duplicate'
        assert c.get('/api/dashboard').json()['jobs']==1


def test_similar_jd_queued_for_manual_review_no_overwrite(tmp_path,monkeypatch):
    with client_at(tmp_path,monkeypatch) as c:
        b=batch(c); i=item(c,b)
        old=c.post(f'/api/capture/items/{i}/commit',json=confirm()).json()['job_id']
        b2=batch(c);i2=item(c,b2,'JD 变更','https://www.zhipin.com/job_detail/different.html')
        data=confirm(jd_text='这是另一团队岗位的 JD',source_url='https://www.zhipin.com/job_detail/different.html')
        r=c.post(f'/api/capture/items/{i2}/commit',json=data)
        assert r.json()['status']=='review'
        assert c.get('/api/dashboard').json()['jobs']==1
        assert c.get('/api/reviews').json()[0]['match_job_id']==old
        assert c.get(f'/api/jobs/{old}').json()['jd_text']==confirm()['jd_text']


def test_draft_without_screenshot_cannot_claim_extracted_jd(tmp_path,monkeypatch):
    with client_at(tmp_path,monkeypatch) as c:
        b=batch(c)
        i=item(c,b,'','')
        assert c.post(f'/api/capture/items/{i}/commit',json={**confirm(),'jd_text':''}).status_code==422
        assert c.get('/api/dashboard').json()['jobs']==0
        assert c.get(f'/api/capture/batches/{b}/items').json()[0]['status']=='pending'
        assert c.patch(f'/api/capture/items/{i}',json={'jd_text':'JD 手动转录'}).status_code==200


def test_screenshot_validated_saved_and_protected(tmp_path,monkeypatch):
    from PIL import Image
    from io import BytesIO
    output=BytesIO();Image.new('RGB',(2,2)).save(output,'PNG');png=output.getvalue()
    with client_at(tmp_path,monkeypatch) as c:
        b=batch(c);i=item(c,b,'','')
        bad=c.post(f'/api/capture/items/{i}/screenshot',content=b'<svg onload=alert(1)>')
        assert bad.status_code==415
        resp=c.post(f'/api/capture/items/{i}/screenshot',content=png,headers={'Content-Type':'image/png'})
        assert resp.status_code==200,resp.text
        assert c.get(f'/api/capture/items/{i}/screenshot').content==png
        assert c.get(f'/api/capture/batches/{b}/items').json()[0]['has_screenshot']
        assert c.post(f'/api/capture/items/{i}/screenshot',content=b'x'*(6*1024*1024+1)).status_code==413
        assert c.get(f'/api/capture/items/{i}/screenshot').content==png


def test_url_credential_guard_and_source_conflict(tmp_path,monkeypatch):
    with client_at(tmp_path,monkeypatch) as c:
        b=batch(c)
        assert c.post(f'/api/capture/batches/{b}/items',json={'source_url':'https://name:secret@host.tld/job'}).status_code==422
        i=item(c,b)
        assert c.post(f'/api/capture/items/{i}/commit',json={**confirm(),'source_url':'https://new.tld/job'}).status_code==409
        assert c.get('/api/dashboard').json()['jobs']==0


def test_schema_v5_upgrade_keeps_existing_data_and_pending_batches(tmp_path,monkeypatch):
    path=tmp_path/'upgrade.sqlite';monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH',str(path))
    from app.db import init_db
    init_db()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO companies(name) VALUES('原有企业')")
        db.execute('PRAGMA user_version=5')
    init_db()
    with sqlite3.connect(path) as db:
        from app.db import SCHEMA_VERSION
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert db.execute("SELECT name FROM companies").fetchone()[0]=='原有企业'
        assert 'capture_items' in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_immutable_raw_evidence_and_complete_backup(tmp_path,monkeypatch):
    from io import BytesIO
    from PIL import Image
    from zipfile import ZipFile
    with client_at(tmp_path,monkeypatch) as c:
        b=batch(c);i=item(c,b,'最初复制的岗位原文')
        data=confirm(jd_text='人工核对后的完整 JD')
        assert c.patch(f'/api/capture/items/{i}',json={'jd_text':data['jd_text']}).status_code==200
        copy=c.get(f'/api/capture/batches/{b}/items').json()[0]
        assert copy['raw_text']=='最初复制的岗位原文'
        assert copy['draft']['jd_text']=='人工核对后的完整 JD'
        image=BytesIO();Image.new('RGB',(2,2)).save(image,'PNG')
        assert c.post(f'/api/capture/items/{i}/screenshot',content=image.getvalue()).status_code==200
        assert c.post(f'/api/capture/items/{i}/commit',json=data).json()['status']=='imported'
        backup=c.get('/api/capture/backup')
        assert backup.status_code==200
        with ZipFile(BytesIO(backup.content)) as z:
            assert 'radar.sqlite' in z.namelist()
            assert any(x.startswith('capture-evidence/') for x in z.namelist())
            assert 'manifest.json' in z.namelist()


def test_offline_restores_screenshots_and_database(tmp_path,monkeypatch):
    from io import BytesIO
    from PIL import Image
    from zipfile import ZipFile
    from tools.restore_capture_bundle import restore_bundle
    with client_at(tmp_path,monkeypatch) as c:
        b=batch(c);i=item(c,b)
        image=BytesIO();Image.new('RGB',(2,3)).save(image,'PNG')
        c.post(f'/api/capture/items/{i}/screenshot',content=image.getvalue())
        content=c.get('/api/capture/backup').content
    archive=tmp_path/'backup.zip';archive.write_bytes(content)
    target=tmp_path/'restored'/'radar.db'
    restore_bundle(archive,target)
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT COUNT(*) FROM capture_items').fetchone()[0]==1
    assert len(list((target.parent/'capture-evidence').glob('*.png')))==1
    with ZipFile(archive) as z:
        assert 'manifest.json' in z.namelist()
