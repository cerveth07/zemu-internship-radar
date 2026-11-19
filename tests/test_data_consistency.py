"""Regression cases from the Windows audit; isolated databases, no real collector."""
import io
import json
import time
import zipfile
from contextlib import closing
from types import SimpleNamespace
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect
from app import shixiseng_runtime as runtime


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path/'radar.db'))
    monkeypatch.setattr(runtime, '_processes', {})
    with TestClient(app) as c:
        yield c


def search_fixture(client, snapshot=True, status='running'):
    bid=client.post('/api/capture/batches',json={'platform':'shixiseng','keyword':'AI','city':'北京'}).json()['id']
    task='a'*32
    with connect() as c:
        rid=c.execute("INSERT INTO capture_search_runs(batch_id,task_id,run_id,platform,preferences_json,status) VALUES(?,?,?,'shixiseng','{}',?)",(bid,task,task,status)).lastrowid
    if snapshot:
        folder=runtime.run_directory(task);folder.mkdir(parents=True)
        value={'task_id':task,'run_id':task,'status':status,'heartbeat':time.time(),
               'items':[{'title':'AI实习','company':'样例','city':'北京','url':'https://www.shixiseng.com/intern/inn_test','jd':'熟悉 Python','jd_available':True}],
               'total':1,'platform_state':{},'error':'','finished_at':None}
        (folder/'result.json').write_text(json.dumps(value),encoding='utf-8')
    return bid,rid,task


def fake_start(monkeypatch):
    monkeypatch.setattr(runtime,'capabilities',lambda:{'version':1,'matched_results':True})
    monkeypatch.setattr(runtime,'readiness',lambda:{'ok':True})
    monkeypatch.setattr(runtime,'start_search',lambda preferences:{'task':{'id':'b'*32,'mode':'collect','status':'running'}})


def test_bundle_retains_results_and_interrupts_restored_process(client,tmp_path,monkeypatch):
    bid,rid,task=search_fixture(client)
    response=client.get('/api/capture/backup')
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert f'shixiseng-runs/{task}/result.json' in z.namelist()
    archive=tmp_path/'bundle.zip';archive.write_bytes(response.content)
    destination=tmp_path/'restored'/'radar.db'
    from tools.restore_capture_bundle import restore_bundle
    restore_bundle(archive,destination)
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH',str(destination))
    assert runtime.task_active(None) is False
    refreshed=client.get(f'/api/capture/search-runs/{rid}')
    assert refreshed.status_code==200,refreshed.text
    assert refreshed.json()['status']=='failed'
    synced=client.post(f'/api/capture/search-runs/{rid}/sync',json={'confirmed':True})
    assert synced.status_code==200,synced.text
    assert synced.json()['created']==1
    fake_start(monkeypatch)
    started=client.post('/api/capture/connectors/shixiseng/start-collect',json={'confirmed':True,'batch_id':bid})
    assert started.status_code==200,started.text


@pytest.mark.parametrize('operation',['refresh','stop','start'])
def test_missing_snapshot_does_not_permanently_block_search(client,monkeypatch,operation):
    bid,rid,_=search_fixture(client,snapshot=False,status='starting')
    fake_start(monkeypatch)
    if operation=='refresh':
        result=client.get(f'/api/capture/search-runs/{rid}')
        assert result.status_code==200 and result.json()['status']=='failed'
    elif operation=='stop':
        result=client.post(f'/api/capture/search-runs/{rid}/stop',json={'confirmed':True})
        assert result.status_code==200 and result.json()['search_run']['status']=='failed'
    else:
        result=client.post('/api/capture/connectors/shixiseng/start-collect',json={'confirmed':True,'batch_id':bid})
        assert result.status_code==200,result.text


def test_live_writer_missing_snapshot_is_not_freed(client,monkeypatch):
    _,rid,task=search_fixture(client,snapshot=False)
    monkeypatch.setattr(runtime,'_processes',{task:SimpleNamespace(poll=lambda:None)})
    assert client.get(f'/api/capture/search-runs/{rid}').status_code==503
    with connect() as c:
        assert c.execute('SELECT status FROM capture_search_runs WHERE id=?',(rid,)).fetchone()[0]=='running'


def test_sqlite_only_restore_clears_orphaned_active_run(client,tmp_path):
    _,rid,_=search_fixture(client,snapshot=False)
    backup=tmp_path/'old.sqlite';backup.write_bytes(client.get('/api/backup/sqlite').content)
    from tools.restore_backup import restore
    destination=tmp_path/'old-restore.sqlite';restore(backup,destination)
    with closing(sqlite3.connect(destination)) as c:
        assert c.execute('SELECT status FROM capture_search_runs WHERE id=?',(rid,)).fetchone()[0]=='failed'


def add_job(client, **extra):
    cid=client.post('/api/companies',json={'name':'测试企业'}).json()['id']
    r=client.post('/api/jobs',json={'company_id':cid,'title':'AI实习','priority':'high',
                                 'jd_text':'熟悉 Python，参与项目。','eligibility':'本科在读',**extra})
    assert r.status_code==201,r.text
    return r.json()['id']


def extraction(jid, pid):
    return {'package_id':pid,'dry_run':False,'requirements':[{'job_id':jid,'name':'Python',
        'requirement_type':'technical_skill','strength':'must','evidence':'熟悉 Python',
        'source_field':'jd_text'}]}


@pytest.mark.parametrize('changes',[{'priority':'medium'},{'notes':'准备周一投递'},{'status':'verified'}])
@pytest.mark.parametrize('kind',['general','jd_extraction','analysis'])
def test_unrelated_edits_do_not_invalidate_extraction(client,changes,kind):
    jid=add_job(client)
    if kind=='analysis':
        pid=client.post('/api/analysis/packages',json={'job_ids':[jid]}).json()['package']['package_id']
    else:
        pid=client.post('/api/exports/jobs',json={'ids':[jid],'intent':kind}).json()['package_id']
    assert client.patch(f'/api/jobs/{jid}',json=changes).status_code==200
    response=client.post('/api/requirements/import',json=extraction(jid,pid))
    assert response.status_code==200,response.text
    assert response.json()['stale_job_ids']==[]


@pytest.mark.parametrize('changes',[{'jd_text':'熟悉 Python，仅作为加分项。'},{'eligibility':'仅限博士'}])
def test_source_changes_still_block_extraction(client,changes):
    jid=add_job(client)
    pid=client.post('/api/exports/jobs',json={'ids':[jid]}).json()['package_id']
    client.patch(f'/api/jobs/{jid}',json=changes)
    assert client.post('/api/requirements/import',json=extraction(jid,pid)).status_code==409


def test_legacy_extraction_hash_is_used_after_upgrade(client):
    jid=add_job(client)
    pid=client.post('/api/exports/jobs',json={'ids':[jid],'intent':'jd_extraction'}).json()['package_id']
    with connect() as c:
        c.execute("UPDATE analysis_packages SET jd_hashes_json='{}' WHERE id=?",(pid,))
    client.patch(f'/api/jobs/{jid}',json={'priority':'medium'})
    assert client.post('/api/requirements/import',json=extraction(jid,pid)).status_code==200


@pytest.mark.parametrize('changes',[{'jd_text':'新的工作要求'},{'priority':'skip'}])
def test_old_priority_results_never_overwrite_newer_decisions(client,changes):
    jid=add_job(client)
    pid=client.post('/api/exports/jobs',json={'ids':[jid]}).json()['package_id']
    payload={'package_id':pid,'rows':[{'job_id':jid,'priority':'low','reason':'旧分析'}]}
    client.patch(f'/api/jobs/{jid}',json=changes)
    preview=client.post('/api/jobs/priority-import',json=payload)
    assert preview.status_code==200 and preview.json()['stale_job_ids']==[jid]
    assert client.post('/api/jobs/priority-import',json={**payload,'dry_run':False}).status_code==409
    assert client.get(f'/api/jobs/{jid}').json()['priority']==changes.get('priority','high')


def test_priority_commit_rechecks_changes_since_preview(client):
    jid=add_job(client)
    pid=client.post('/api/exports/jobs',json={'ids':[jid]}).json()['package_id']
    payload={'package_id':pid,'rows':[{'job_id':jid,'priority':'medium'}]}
    assert client.post('/api/jobs/priority-import',json=payload).json()['stale_job_ids']==[]
    client.patch(f'/api/jobs/{jid}',json={'priority':'skip'})
    assert client.post('/api/jobs/priority-import',json={**payload,'dry_run':False}).status_code==409
    assert client.post('/api/jobs/priority-import',json={'rows':payload['rows'],'dry_run':False}).status_code==409


def test_manual_skills_are_visible_with_evidence_and_removed_when_cleared(client):
    jid=add_job(client)
    payload={'confirmed':True,'skills':[{'name':'Python','necessity':'must','evidence':'熟悉 Python'},
                                     {'name':'无证据标签','necessity':'must'}]}
    assert client.put(f'/api/jobs/{jid}/skills',json=payload).status_code==200
    skills=client.get('/api/skill-gap').json()['skills']
    assert len(skills)==1 and skills[0]['manual_jobs']==1
    assert skills[0]['job_count']==1 and skills[0]['must_jobs']==1
    evidence=client.get('/api/skill-gap/evidence?skill=Python').json()['items']
    assert evidence[0]['origin']=='manual_skill' and evidence[0]['evidence']=='熟悉 Python'
    # The same human requirement must not double-count the job.
    pid=client.post('/api/exports/jobs',json={'ids':[jid]}).json()['package_id']
    client.post('/api/requirements/import',json=extraction(jid,pid))
    req=client.get('/api/requirements').json()['items'][0]['id']
    client.post(f'/api/requirements/{req}/review',json={'action':'confirm'})
    row=client.get('/api/skill-gap').json()['skills'][0]
    assert row['job_count']==1 and row['confirmed_jobs']==1 and row['manual_jobs']==0
    assert client.post(f'/api/requirements/{req}/review',json={'action':'reopen'}).status_code==200
    assert client.post(f'/api/requirements/{req}/review',json={'action':'reject'}).status_code==200
    client.put(f'/api/jobs/{jid}/skills',json={'confirmed':True,'skills':[]})
    assert client.get('/api/skill-gap').json()['skills']==[]


def test_manual_skill_hash_goes_stale_and_reconfirmation_restores_it(client):
    jid=add_job(client)
    payload={'confirmed':True,'skills':[{'name':'Python','necessity':'must','evidence':'熟悉 Python'}]}
    client.put(f'/api/jobs/{jid}/skills',json=payload)
    client.patch(f'/api/jobs/{jid}',json={'jd_text':'熟悉 Python，仅作为加分项。'})
    assert client.get('/api/skill-gap').json()['skills']==[]
    payload['skills'][0]['necessity']='bonus'
    client.put(f'/api/jobs/{jid}/skills',json=payload)
    assert client.get('/api/skill-gap').json()['skills'][0]['bonus_jobs']==1


def test_imported_skill_tags_are_not_mislabeled_as_human_confirmed(client):
    add_job(client,skills=[{'name':'Python','necessity':'must','evidence':'熟悉 Python'}])
    assert client.get('/api/skill-gap').json()['skills']==[]


@pytest.mark.parametrize('duplicate',[False,True])
def test_capture_dates_survive_draft_commit_and_duplicate_review(client,duplicate):
    if duplicate:
        add_job(client,source_url='https://example.com/old',jd_text='旧 JD')
    bid=client.post('/api/capture/batches',json={'platform':'manual'}).json()['id']
    iid=client.post(f'/api/capture/batches/{bid}/items',json={'raw_text':'新 JD','source_url':'https://example.com/job'}).json()['id']
    dates={'posted_at':'2026-10-19','deadline':'2026-10-21'}
    assert client.patch(f'/api/capture/items/{iid}',json=dates).status_code==200
    assert client.patch(f'/api/capture/items/{iid}',json={'deadline':'2026-02-30'}).status_code==422
    body={'company':'测试企业','title':'AI实习','jd_text':'新 JD','source_url':'https://example.com/job'}
    result=client.post(f'/api/capture/items/{iid}/commit',json=body)
    assert result.status_code==200,result.text
    if duplicate:
        review=client.get('/api/reviews').json()[0]
        # Resolve into a separate job, keeping candidate dates intact.
        created=client.post(f"/api/reviews/{review['id']}/resolve",json={'action':'create_separate'})
        assert created.status_code==200,created.text
        jid=created.json()['job_id']
    else:
        jid=result.json()['job_id']
    record=client.get(f'/api/jobs/{jid}').json()
    assert all(record[k]==v for k,v in dates.items())


def test_priority_conflict_blocks_entire_batch(client):
    first=add_job(client)
    cid=client.get('/api/companies').json()[0]['id']
    second=client.post('/api/jobs',json={'company_id':cid,'title':'另一岗位','priority':'high'}).json()['id']
    pid=client.post('/api/exports/jobs',json={'ids':[first,second]}).json()['package_id']
    client.patch(f'/api/jobs/{second}',json={'priority':'skip'})
    response=client.post('/api/jobs/priority-import',json={'package_id':pid,'dry_run':False,
        'rows':[{'job_id':first,'priority':'low'},{'job_id':second,'priority':'low'}]})
    assert response.status_code==409
    assert client.get(f'/api/jobs/{first}').json()['priority']=='high'
    assert client.get(f'/api/jobs/{second}').json()['priority']=='skip'


def test_foreign_snapshot_in_bundle_is_rejected_without_installing_database(client,tmp_path):
    _,_,task=search_fixture(client)
    source=zipfile.ZipFile(io.BytesIO(client.get('/api/capture/backup').content))
    archive=tmp_path/'tampered.zip'
    with source,zipfile.ZipFile(archive,'w') as z:
        for name in source.namelist():
            blob=source.read(name)
            if name.endswith('/result.json'):
                value=json.loads(blob);value['task_id']='b'*32
                blob=json.dumps(value).encode()
            z.writestr(name,blob)
    from tools.restore_capture_bundle import restore_bundle
    destination=tmp_path/'restore'/'radar.db'
    with pytest.raises(RuntimeError,match='归属'):
        restore_bundle(archive,destination)
    assert not destination.exists()
    assert not (destination.parent/'shixiseng-runs'/task/'result.json').exists()


def test_changed_deadline_alone_enters_duplicate_review(client):
    add_job(client,source_url='https://example.com/job',deadline='2026-10-21')
    bid=client.post('/api/capture/batches',json={}).json()['id']
    iid=client.post(f'/api/capture/batches/{bid}/items',json={'source_url':'https://example.com/job','raw_text':'熟悉 Python，参与项目。'}).json()['id']
    result=client.post(f'/api/capture/items/{iid}/commit',json={'company':'测试企业','title':'AI实习',
        'jd_text':'熟悉 Python，参与项目。','source_url':'https://example.com/job','deadline':'2026-11-01'})
    assert result.status_code==200 and result.json()['status']=='review'
    assert client.get('/api/reviews').json()[0]['input']['deadline']=='2026-11-01'


def test_schema_upgrade_preserves_legacy_packages_without_inventing_source_hashes(client):
    jid=add_job(client)
    pid=client.post('/api/analysis/packages',json={'job_ids':[jid]}).json()['package']['package_id']
    with connect() as c:
        c.execute('ALTER TABLE analysis_packages DROP COLUMN jd_hashes_json')
        c.execute('PRAGMA user_version=20')
    from app.db import init_db, SCHEMA_VERSION
    init_db()
    with connect() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert json.loads(c.execute('SELECT jd_hashes_json FROM analysis_packages WHERE id=?',(pid,)).fetchone()[0])=={}
    assert client.post('/api/requirements/import',json=extraction(jid,pid)).status_code==200
