import os
import sqlite3
from contextlib import closing
from pathlib import Path
from fastapi.testclient import TestClient


def test_company_evidence_reviews_and_backup(tmp_path):
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(tmp_path/'radar.sqlite')
    from app.main import app
    with TestClient(app) as client:
        company=client.post('/api/companies',json={'name':'创业公司甲'}).json()['id']
        assert client.patch(f'/api/companies/{company}',json={'sector':'Agent 应用','headcount_text':'20-99 人'}).json()['changed']
        assert client.get('/api/companies').json()[0]['sector']=='Agent 应用'
        a=client.post('/api/jobs',json={'company_id':company,'title':'AI 产品实习','city':'北京', 'source_url':'https://example.com/a'}).json()['id']
        rows=[{'company':'创业公司甲','title':'AI 产品实习','city':'北京','jd_text':'不同招聘描述',
              'source_url':'https://example.com/b', 'skills':['产品策划']}]
        assert client.post('/api/import/jobs',json={'rows':rows,'dry_run':True}).json()['counts']['review']==1
        assert client.get('/api/reviews').json()==[]
        assert client.post('/api/import/jobs',json={'rows':rows,'dry_run':False}).json()['counts']['review']==1
        r=client.get('/api/reviews').json()[0]
        assert r['match_job_id']==a
        assert client.post(f"/api/reviews/{r['id']}/resolve",json={'action':'link_source'}).json()['job_id']==a
        assert len(client.get(f'/api/jobs/{a}').json()['sources'])==2
        assert client.get('/api/reviews').json()==[]
        assert client.post(f"/api/reviews/{r['id']}/resolve",json={'action':'create_separate'}).status_code==409
        assert client.post(f'/api/jobs/{a}/sources',json={'platform':'manual','url':'https://example.com/b'}).status_code==201
        evid=client.post('/api/evidence',json={'skill':'Agent 开发','evidence_type':'project','description':'已完成项目','proficiency':'working'}).json()['id']
        assert len(client.get('/api/evidence').json())==1
        assert client.delete(f'/api/evidence/{evid}').status_code==200
        assert client.patch(f'/api/jobs/{a}',json={'salary':'250 元/天','direction':'pm','city':'上海'}).json()['changed']
        assert client.get(f'/api/jobs/{a}').json()['salary']=='250 元/天'
        task=client.post('/api/learning',json={'title':'完成 Agent Demo'}).json()['id']
        assert client.patch(f'/api/learning/{task}',json={'status':'doing'}).status_code==200
        assert client.get('/api/learning').json()[0]['status']=='doing'
        resp=client.get('/api/backup/sqlite')
        assert resp.status_code==200 and resp.content.startswith(b'SQLite format 3')
        exported=tmp_path/'backup.sqlite';exported.write_bytes(resp.content)
        with closing(sqlite3.connect(exported)) as c:
            assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
        from tools.restore_backup import restore
        restored=tmp_path/'restored.sqlite'
        restore(exported,restored)
        assert restored.exists()
        with closing(sqlite3.connect(restored)) as c:
            assert c.execute('SELECT count(*) FROM companies').fetchone()[0]==1
        # local safety copy retained on overwrite
        restore(exported,restored)
        assert list(tmp_path.glob('restored-pre-restore-*.sqlite'))
        assert 'import_reviews' in client.get('/api/export').json()


def test_separate_review_and_source_conflict(tmp_path):
    os.environ['RADAR_PRIVATE_RC_DB_PATH']=str(tmp_path/'second.sqlite')
    from app.main import app
    with TestClient(app) as client:
        cid=client.post('/api/companies',json={'name':'公司乙'}).json()['id']
        jid=client.post('/api/jobs',json={'company_id':cid,'title':'FDE 实习','city':'深圳'}).json()['id']
        row={'company':'公司乙','title':'FDE 实习','city':'深圳','source_url':'https://example.com/source-2'}
        client.post('/api/import/jobs',json={'rows':[row],'dry_run':False})
        r=client.get('/api/reviews').json()[0]
        separate=client.post(f"/api/reviews/{r['id']}/resolve",json={'action':'create_separate'})
        assert separate.status_code==200
        assert client.get('/api/dashboard').json()['jobs']==2
        assert client.post(f'/api/jobs/{jid}/sources',json={'platform':'other','url':row['source_url']}).status_code==409
        batch=client.post('/api/import/jobs',json={'rows':[{'company':'公司丙','title':'新职位','source_url':'https://example.com/q'},
            {'company':'公司丁','title':'另一职位','source_url':'https://example.com/q'}],'dry_run':True}).json()
        assert batch['counts']=={'new':1,'duplicate':1,'review':0}
