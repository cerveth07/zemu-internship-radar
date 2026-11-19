"""v0.4 upgrades must preserve v0.3 data and require human approval."""
import os
import sqlite3
from fastapi.testclient import TestClient


def test_migrate_existing_v03_without_losing_records(tmp_path):
    db_file = tmp_path / 'legacy.sqlite'
    # Exact v0.3 CREATE TABLE definition for jobs, with an existing company/job.
    with sqlite3.connect(db_file) as db:
        db.executescript('''
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL,
         title TEXT NOT NULL,direction TEXT NOT NULL DEFAULT 'other',city TEXT DEFAULT '',
         salary TEXT DEFAULT '', duration TEXT DEFAULT '',onsite_days TEXT DEFAULT '',
         jd_text TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'candidate',
         priority TEXT NOT NULL DEFAULT 'unrated',notes TEXT DEFAULT '',
         created_at TEXT NOT NULL DEFAULT (datetime('now')),
         updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO companies(id,name) VALUES(1,'旧企业');
        INSERT INTO jobs(id,company_id,title,jd_text) VALUES(1,1,'旧岗位','要求 Python');
        ''')
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(db_file)
    from app.db import init_db
    init_db()
    init_db()  # idempotent migration and initialization
    with sqlite3.connect(db_file) as db:
        from app.db import SCHEMA_VERSION
        assert db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert db.execute('SELECT title,jd_text,team,posted_at FROM jobs').fetchone() == ('旧岗位','要求 Python','','')
        assert db.execute('SELECT count(*) FROM companies').fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='company_observations'").fetchone()


def test_skill_review_is_read_only_until_explicitly_confirmed(tmp_path):
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(tmp_path / 'review.sqlite')
    from app.main import app
    from app.db import connect
    with TestClient(app) as client:
        company = client.post('/api/companies',json={'name':'AI 小厂甲'}).json()['id']
        text = '岗位职责：负责 Agent 工作流开发\n任职要求：熟悉 Python、SQL 和 RAG\n加分项：熟悉 LangChain'
        job = client.post('/api/jobs',json={'company_id':company,'title':'Agent 实习生',
            'jd_text':text,'team':'智能体组','eligibility':'2027 届优先',
            'posted_at':'2026-10-07','deadline':'2026-11-30'}).json()['id']
        suggests = client.get(f'/api/jobs/{job}/skill-suggestions').json()
        assert suggests['mode']=='offline_keyword_review_only'
        tags = suggests['candidates']
        python_tag = next(x for x in tags if x['name']=='Python')
        lc_tag = next(x for x in tags if x['name']=='LangChain')
        assert python_tag['necessity']=='must'
        assert lc_tag['necessity']=='bonus'
        assert python_tag['evidence'] in text
        assert client.get(f'/api/jobs/{job}').json()['skills']==[]  # suggestions never write
        assert client.put(f'/api/jobs/{job}/skills',json={'skills':[python_tag]}).status_code==422
        fail = client.put(f'/api/jobs/{job}/skills',json={'confirmed':True,'skills':[
            {'name':'Python','necessity':'must','evidence':'编造的岗位原文'}]})
        assert fail.status_code==422
        good = client.put(f'/api/jobs/{job}/skills',json={'confirmed':True,'skills':[
            {k:python_tag[k] for k in ('name','category','necessity','evidence')},
            {k:lc_tag[k] for k in ('name','category','necessity','evidence')}]})
        assert good.status_code==200 and good.json()['count']==2
        assert good.json()['changed']
        assert client.get('/api/skills').json()[0]['job_count']==1
        detail=client.get(f'/api/jobs/{job}').json()
        assert len(detail['skills'])==2
        assert any(x['event']=='skills_reviewed' for x in detail['history'])
        # Updating original JD does not silently erase stale evidence.
        client.patch(f'/api/jobs/{job}',json={'jd_text':'新的 JD，不再包括原文技能'})
        audit=client.get('/api/quality').json()
        assert audit['issue_counts']['stale_skill_evidence']==1
        assert client.put(f'/api/jobs/{job}/skills',json={'confirmed':True,'skills':[]}).status_code==200
        assert client.get('/api/quality').json()['issue_counts']['stale_skill_evidence']==0
        with connect() as conn:
            assert conn.execute('SELECT count(*) FROM job_history WHERE job_id=? AND event=?',(job,'skills_reviewed')).fetchone()[0] == 2


def test_company_observations_and_source_verification(tmp_path):
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(tmp_path/'observations.sqlite')
    from app.main import app
    with TestClient(app) as client:
        cid=client.post('/api/companies',json={'name':'初创 AI 公司'}).json()['id']
        assert client.post(f'/api/companies/{cid}/observations',json={
            'event_type':'hiring','observed_on':'2026-02-30','description':'招聘开启'}).status_code==422
        obs=client.post(f'/api/companies/{cid}/observations',json={
            'event_type':'hiring','observed_on':'2026-10-08','description':'在公开招聘帖发现新岗位',
            'source_url':'https://example.org/post'}).json()['id']
        assert len(client.get(f'/api/companies/{cid}/observations').json())==1
        job=client.post('/api/jobs',json={'company_id':cid,'title':'AI 产品实习','jd_text':''}).json()['id']
        s=client.post(f'/api/jobs/{job}/sources',json={'platform':'boss','url':'https://example.org/job/123'})
        assert s.status_code==201
        sources=client.get(f'/api/jobs/{job}').json()['sources']
        sid=next(x['id'] for x in sources if x['platform']=='boss')
        assert client.patch(f'/api/jobs/{job}/sources/{sid}',json={'verification':'verified'}).status_code==200
        source=next(x for x in client.get(f'/api/jobs/{job}').json()['sources'] if x['id']==sid)
        assert source['last_checked_at'] and source['verification']=='verified'
        assert client.get(f'/api/jobs/{job}').json()['status']=='candidate'  # no fake opening confirmation
        assert client.patch(f'/api/jobs/{job}/sources/999999',json={'verification':'verified'}).status_code==404
        quality=client.get('/api/quality').json()
        assert quality['issue_counts']['jd']==1
        assert quality['issue_counts']['unverified_source']==0
        assert 'company_observations' in client.get('/api/export').json()
        assert client.delete(f'/api/companies/{cid}/observations/{obs}').status_code==200
        assert client.get(f'/api/companies/{cid}/observations').json()==[]


def test_new_job_fields_survive_import_and_validation(tmp_path):
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(tmp_path/'imports.sqlite')
    from app.main import app
    with TestClient(app) as client:
        row={'company':'公司己','title':'研究策略实习','team':'战略研究部','eligibility':'2028 届在校生',
             'posted_at':'2026-10-01','deadline':'2026-12-15','source_url':'https://example.org/job/research'}
        assert client.post('/api/import/jobs',json={'rows':[row],'dry_run':False}).status_code==200
        job=client.get('/api/jobs').json()[0]
        for k in ('team','eligibility','posted_at','deadline'):
            assert job[k]==row[k]
        assert client.patch(f"/api/jobs/{job['id']}",json={'deadline':'2026-02-30'}).status_code==422
        assert client.patch(f"/api/jobs/{job['id']}",json={'deadline':'2026-12-30'}).status_code==200
        assert client.get(f"/api/jobs/{job['id']}").json()['deadline']=='2026-12-30'
