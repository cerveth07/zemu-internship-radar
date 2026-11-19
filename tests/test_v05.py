"""v0.5: local-only handoff, stable delta snapshots, reviewed AI result imports."""
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path/'radar-v05.sqlite'))
    from app.main import app
    with TestClient(app) as c:
        yield c


def seed(client):
    company=client.post('/api/companies',json={'name':'创业团队甲', 'sector':'AI Agent'}).json()['id']
    j=client.post('/api/jobs',json={'company_id':company, 'title':'AI Agent 实习生',
        'direction':'agent', 'city':'北京','jd_text':'负责 Agent 应用开发，熟悉 Python 和 RAG',
        'notes':'个人筛选笔记（不对外）', 'source_url':'https://example.org/job/1',
        'skills':[{'name':'Python','evidence':'Python'}]}).json()['id']
    client.put(f'/api/jobs/{j}/application',json={'stage':'contacted','recruiter':'私人联系人',
                                                    'resume_version':'内部简历 v2'})
    client.post('/api/evidence',json={'skill':'Python','description':'私人作品描述'})
    return company,j


def package(client, **kwargs):
    return client.post('/api/analysis/packages',json=kwargs)


def test_privacy_default_and_explicit_opt_in(client):
    cid,jid=seed(client)
    response=package(client, direction='agent',save_snapshot=True)
    assert response.status_code==200,response.text
    bundle=response.json()['package']
    assert bundle['jobs'][0]['jd_text']=='负责 Agent 应用开发，熟悉 Python 和 RAG'
    assert bundle['jobs'][0]['job_id']==jid
    assert 'private_notes' not in bundle['jobs'][0]
    assert 'application' not in bundle['jobs'][0]
    assert 'personal_evidence' not in bundle
    assert '私人联系人' not in response.text
    assert '内部简历 v2' not in response.text
    assert '私人作品描述' not in response.text
    assert '私人筛选笔记' not in response.text
    assert '```json' in response.json()['markdown']
    private=package(client,direction='agent',include_private=True).json()['package']
    assert private['jobs'][0]['application']['recruiter']=='私人联系人'
    assert private['personal_evidence'][0]['description']=='私人作品描述'


def test_snapshot_delta_reacts_to_sources_skills_and_company_observations(client):
    cid,jid=seed(client)
    first=package(client,direction='agent').json()['package']
    base=first['package_id']
    unchanged=package(client,direction='agent',baseline_id=base).json()['package']
    assert unchanged['jobs']==[] and unchanged['unchanged_count']==1
    assert not unchanged['removed_job_ids']
    assert package(client,direction='pm',baseline_id=base).status_code==422
    source_id=client.get(f'/api/jobs/{jid}').json()['sources'][0]['id']
    assert client.patch(f'/api/jobs/{jid}/sources/{source_id}',json={'verification':'verified'}).status_code==200
    changed=package(client,direction='agent',baseline_id=base).json()['package']
    assert len(changed['jobs'])==1 and changed['jobs'][0]['sources'][0]['verification']=='verified'
    nextbase=package(client,direction='agent').json()['package']['package_id']
    client.post(f'/api/companies/{cid}/observations',json={'event_type':'hiring',
        'observed_on':'2026-10-08','description':'新增招聘动态','source_url':'https://example.org/post'})
    changed2=package(client,direction='agent',baseline_id=nextbase).json()['package']
    assert changed2['jobs'][0]['company_observations'][0]['description']=='新增招聘动态'
    # Moving a job out of a filter is a removal in that scope, not proof it closed.
    client.patch(f'/api/jobs/{jid}',json={'direction':'research'})
    removed=package(client,direction='agent',baseline_id=base).json()['package']
    assert removed['jobs']==[] and removed['removed_job_ids']==[jid]
    assert removed['total_matching_jobs']==0


def test_export_preview_has_no_mutations_and_validates_ids(client):
    _,jid=seed(client)
    before=len(client.get('/api/analysis/packages').json())
    preview=package(client,save_snapshot=False)
    assert preview.status_code==200 and preview.json()['package']['package_id'] is None
    assert len(client.get('/api/analysis/packages').json())==before
    assert package(client,job_ids=[jid,99999]).status_code==422
    assert package(client,job_ids=[jid,jid]).status_code==422
    assert package(client,city='深圳',job_ids=[jid]).status_code==422


def test_import_preview_validation_idempotency_and_adoption(client):
    _,jid=seed(client)
    baseline=package(client).json()['package']['package_id']
    suggestion={'kind':'learning_task','title':'Agent 工程作品','content':'结合 JD 完成可复现演示',
        'job_ids':[jid], 'related_skill':'Agent', 'priority':'high'}
    request={'package_id':baseline,'findings':[suggestion,suggestion]}
    reject=client.post('/api/analysis/import',json={**request,'findings':[{**suggestion,'job_ids':[12345]}]})
    assert reject.status_code==422
    dry=client.post('/api/analysis/import',json={**request,'dry_run':True})
    assert dry.status_code==200 and dry.json()['new_count']==1
    assert len(client.get('/api/analysis/findings').json())==0
    saved=client.post('/api/analysis/import',json={**request,'dry_run':False})
    assert saved.json()['new_count']==1
    assert client.post('/api/analysis/import',json={**request,'dry_run':False}).json()['new_count']==0
    findings=client.get('/api/analysis/findings').json()
    assert len(findings)==1 and findings[0]['job_ids']==[jid]
    result=client.post(f"/api/analysis/findings/{findings[0]['id']}/adopt-learning-task")
    assert result.status_code==200 and not result.json()['already_adopted']
    result2=client.post(f"/api/analysis/findings/{findings[0]['id']}/adopt-learning-task")
    assert result2.json()['already_adopted'] and result2.json()['id']==result.json()['id']
    learning=client.get('/api/learning').json()
    assert len(learning)==1 and learning[0]['source_job_id']==jid
    assert len(client.get('/api/jobs').json())==1
    assert client.get(f'/api/jobs/{jid}').json()['jd_text']=='负责 Agent 应用开发，熟悉 Python 和 RAG'
    # Full JSON audit export includes results, but does not mislabel them as original JD.
    exported=client.get('/api/export').json()
    assert len(exported['analysis_findings'])==1
    assert len(exported['analysis_packages'])==1


def test_incremental_package_cannot_cite_unincluded_unmodified_jobs(client):
    cid,jid=seed(client)
    first=package(client).json()['package']['package_id']
    incremental=package(client,baseline_id=first).json()['package']
    assert incremental['jobs']==[]
    assert client.post('/api/analysis/import',json={'package_id':incremental['package_id'],
        'findings':[{'kind':'job_assessment','title':'缺少当前 JD','content':'不可凭空引用','job_ids':[jid]}]}).status_code==422
    # General insight with no specific job citations is still allowed.
    assert client.post('/api/analysis/import',json={'package_id':incremental['package_id'],
        'findings':[{'kind':'career_strategy','title':'总体观察','content':'无本批次新岗位','job_ids':[]}],
        'dry_run':False}).status_code==200


def test_migrate_v04_without_losing_user_records(tmp_path, monkeypatch):
    path=tmp_path/'migrate.sqlite';monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH',str(path))
    from app.db import init_db
    init_db()
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=4')
        db.execute("INSERT INTO companies(name) VALUES('保留企业')")
        db.execute("INSERT INTO jobs(company_id,title,jd_text) VALUES(1,'旧实习','历史 JD')")
    init_db()
    with sqlite3.connect(path) as db:
        from app.db import SCHEMA_VERSION
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert db.execute('SELECT title,jd_text FROM jobs').fetchone()==('旧实习','历史 JD')
        assert db.execute("SELECT name FROM sqlite_master WHERE name='analysis_packages'").fetchone()


def test_stale_analysis_requires_explicit_acknowledgement(client):
    _,jid=seed(client)
    pkg=package(client).json()['package']['package_id']
    payload={'package_id':pkg,'findings':[{'kind':'learning_task','title':'旧分析','content':'基于旧岗位说明', 'job_ids':[jid]}]}
    client.patch(f'/api/jobs/{jid}',json={'jd_text':'完全不同的新岗位说明'})
    preview=client.post('/api/analysis/import',json={**payload,'dry_run':True})
    assert preview.status_code==200 and preview.json()['stale_job_ids']==[jid]
    assert client.post('/api/analysis/import',json={**payload,'dry_run':False}).status_code==409
    assert client.get('/api/analysis/findings').json()==[]
    approved=client.post('/api/analysis/import',json={**payload,'dry_run':False,'acknowledge_stale':True})
    assert approved.status_code==200 and approved.json()['new_count']==1
    assert client.get(f'/api/jobs/{jid}').json()['jd_text']=='完全不同的新岗位说明'
