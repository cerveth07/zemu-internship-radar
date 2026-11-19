"""Favorite-list import/persistence and owned runner outputs. No BOSS requests."""
import json
import time
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect
from app import boss_favorites as bf, boss_runtime

URL='https://www.zhipin.com/job_detail/fixture.html'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path/'radar.db'))
    monkeypatch.setattr(boss_runtime,'supported_cities',lambda:{'北京':'101010100','上海':'101020100','全国':'0'})
    monkeypatch.setattr(bf,'runner_command',lambda *args:['node','offline-fixture'])
    tasks={}
    def launch(task,preferences):
        directory=bf.run_directory(task);directory.mkdir(parents=True)
        tasks[task]={'task_id':task,'run_id':task,'status':'running','items':[],'results':[],
                     'heartbeat':time.time(),'error':'','finished_at':None,'platform_state':{'boss':{'phase':'查找对应岗位'}}}
        write(task)
    def write(task):
        (bf.run_directory(task)/'result.json').write_text(json.dumps(tasks[task],ensure_ascii=False),encoding='utf-8')
    monkeypatch.setattr(bf,'launch',launch)
    with TestClient(app) as client:
        yield client,tasks,write,tmp_path


def create(client,text='企业,岗位,城市,链接\n样例企业,运营实习,北京,',**extra):
    source={'text':text,'format':'text','city':'北京'}
    response=client.post('/api/boss-favorites/preview',json=source)
    assert response.status_code==200,response.text
    data={**source,'name':'测试清单','preview_hash':response.json()['preview_hash'],**extra}
    response=client.post('/api/boss-favorites/batches',json=data)
    assert response.status_code==201,response.text
    return response.json()


def start(client,batch,action='match',**extra):
    request={'action':action,'request_id':uuid.uuid4().hex,**extra}
    response=client.post(f"/api/boss-favorites/batches/{batch['batch']['id']}/runs",json=request)
    assert response.status_code==200,response.text
    return response.json()['run'],request


def candidate(**extra):
    return {'url':URL,'title':'运营实习','company':'样例企业','city':'北京','salary':'100-200元/天','jd':'参与运营工作',
            'checked_at':'2026-11-16T04:00:00Z','availability':'active','favorite_status':'not_checked',
            'selected':True,'match_reason':'公司与岗位一致',**extra}


def finish(store,run,results):
    client,tasks,write,_=store
    tasks[run['task_id']].update(status='completed',results=results)
    write(run['task_id'])
    response=client.post(f"/api/boss-favorites/runs/{run['task_id']}/refresh")
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.parametrize('text,format',[
    ('运营实习\n财务分析实习','text'),('\ufeff岗位\r\n运营实习\r\n财务分析实习\r\n','csv'),
    ('企业\t岗位\t城市\t链接\n样例企业\t运营实习\t上海\t','text'),
    ('运营实习\n运营实习\n财务分析实习','txt'),
    (URL+'?securityId=transient','txt'),
])
def test_import_names_bom_excel_and_links_without_creating_jobs(store,text,format):
    client,_,_,_=store
    response=client.post('/api/boss-favorites/preview',json={'text':text,'format':format,'city':'北京'})
    assert response.status_code==200,response.text
    assert response.json()['errors']==[]
    assert client.get('/api/jobs').json()==[]
    assert client.get('/api/boss-favorites/batches').json()==[]
    if 'securityId' in text: assert response.json()['items'][0]['url']==URL
    if text.count('运营实习')==2: assert len(response.json()['duplicates'])==1


def test_template_is_excel_bom_and_only_name_is_required(store):
    client,*_=store
    result=client.get('/api/boss-favorites/template')
    assert result.content.startswith(b'\xef\xbb\xbf')
    assert create(client,'运营实习')['targets'][0]['company']==''


def test_errors_do_not_silently_save_valid_subset_and_changed_preview_is_rejected(store):
    client,*_=store
    source={'text':'岗位,城市,链接\n运营实习,北京,\n财务,未知城市,','city':'北京'}
    preview=client.post('/api/boss-favorites/preview',json=source).json()
    assert len(preview['items'])==1 and len(preview['errors'])==1
    assert client.post('/api/boss-favorites/batches',json={**source,'preview_hash':preview['preview_hash']}).status_code==400
    valid={'text':'运营实习','city':'北京'}
    digest=client.post('/api/boss-favorites/preview',json=valid).json()['preview_hash']
    assert client.post('/api/boss-favorites/batches',json={**valid,'text':'财务实习','preview_hash':digest}).status_code==409
    assert client.get('/api/boss-favorites/batches').json()==[]


@pytest.mark.parametrize('text',[
    '企业\t岗位\n样例\t运营\t北京',
    '企业,岗位,链接\n样例,运营,https://www.zhipin.com/web/geek/chat',
    '岗位,链接\n运营,https://www.zhipin.com.evil.test/job_detail/id.html',
    '岗位,链接\n运营,https://user@www.zhipin.com/job_detail/id.html',
])
def test_invalid_shapes_and_nonjob_destinations_are_reported(store,text):
    client,*_=store
    result=client.post('/api/boss-favorites/preview',json={'text':text}).json()
    assert result['errors']


def test_match_selection_favorite_state_and_restart_persistence(store):
    client,tasks,write,_=store
    batch=create(client)
    run,request=start(client,batch)
    duplicate=client.post(f"/api/boss-favorites/batches/{batch['batch']['id']}/runs",json=request)
    assert duplicate.status_code==200 and duplicate.json()['run']['task_id']==run['task_id']
    data=finish(store,run,[{'id':batch['targets'][0]['id'],'state':'matched','candidates':[candidate()]}])
    selected=data['targets'][0]['candidates'][0]
    assert selected['selected']==1
    # Starting another collection cannot compete for the same BOSS profile.
    favorite,_=start(client,data,'favorite')
    capture=client.post('/api/capture/batches',json={'platform':'boss','keyword':'运营实习','city':'北京'}).json()
    blocked=client.post('/api/capture/connectors/bosshunter/start-collect',json={'confirmed':True,'batch_id':capture['id']})
    assert blocked.status_code==409 and '收藏任务' in blocked.text
    data=finish(store,favorite,[dict(candidate(id=selected['id'],favorite_status='saved',selected=False))])
    saved=data['targets'][0]['candidates'][0]
    assert saved['favorite_status']=='saved' and saved['selected']==0
    bf._processes.clear()
    restored=client.get(f"/api/boss-favorites/batches/{batch['batch']['id']}").json()
    assert restored['targets'][0]['candidates'][0]['favorite_status']=='saved'
    assert client.get('/api/jobs').json()==[]
    assert client.get('/api/applications').json()==[]


def test_foreign_target_snapshot_never_partially_changes_candidates(store):
    client,*_=store
    first=create(client);second=create(client,'另一个岗位')
    run,_=start(client,first)
    data=finish(store,run,[{'id':first['targets'][0]['id'],'state':'matched','candidates':[candidate()]},
                           {'id':second['targets'][0]['id'],'state':'matched','candidates':[candidate()]}])
    assert data['run']['status']=='failed'
    assert data['targets'][0]['candidates']==[]
    assert client.get(f"/api/boss-favorites/batches/{second['batch']['id']}").json()['targets'][0]['candidates']==[]


def test_unknown_favorite_keeps_retry_selection_and_does_not_report_success(store):
    client,*_=store
    batch=create(client);run,_=start(client,batch)
    data=finish(store,run,[{'id':batch['targets'][0]['id'],'state':'matched','candidates':[candidate()]}])
    favorite,_=start(client,data,'favorite')
    c=data['targets'][0]['candidates'][0]
    data=finish(store,favorite,[candidate(id=c['id'],favorite_status='unknown',error='请求超时，需要重读状态')])
    c=data['targets'][0]['candidates'][0]
    assert c['favorite_status']=='unknown' and c['selected']==1


def test_clear_selection_is_persistent_and_cross_batch_ids_cannot_be_selected(store):
    client,*_=store
    batch=create(client);run,_=start(client,batch)
    data=finish(store,run,[{'id':batch['targets'][0]['id'],'state':'matched','candidates':[candidate()]}])
    candidate_id=data['targets'][0]['candidates'][0]['id']
    second=create(client,'财务实习')
    assert client.put(f"/api/boss-favorites/batches/{second['batch']['id']}/selection",json={'ids':[candidate_id]}).status_code==409
    cleared=client.put(f"/api/boss-favorites/batches/{batch['batch']['id']}/selection",json={'ids':[]}).json()
    assert not cleared['targets'][0]['candidates'][0]['selected']
    response=client.post(f"/api/boss-favorites/batches/{batch['batch']['id']}/runs",json={'action':'favorite','request_id':uuid.uuid4().hex})
    assert response.status_code==400


def test_backup_contains_lists_candidates_and_owned_favorite_snapshot(store,tmp_path):
    from tools.restore_capture_bundle import restore_bundle
    client,*_=store
    batch=create(client);run,_=start(client,batch)
    finish(store,run,[{'id':batch['targets'][0]['id'],'state':'matched','candidates':[candidate()]}])
    active,_=start(client,client.get(f"/api/boss-favorites/batches/{batch['batch']['id']}").json(),'favorite')
    response=client.get('/api/capture/backup')
    assert response.status_code==200,response.text
    archive=tmp_path/'backup.zip';archive.write_bytes(response.content)
    with zipfile.ZipFile(archive) as package:
        assert f"boss-favorite-runs/{run['task_id']}/result.json" in package.namelist()
    destination=tmp_path/'restored'/'radar.db'
    restore_bundle(archive,destination)
    import sqlite3
    with sqlite3.connect(destination) as conn:
        assert conn.execute('SELECT COUNT(*) FROM boss_favorite_targets').fetchone()[0]==1
        assert conn.execute('SELECT selected FROM boss_favorite_candidates').fetchone()[0]==1
        assert conn.execute('SELECT status FROM boss_favorite_runs WHERE task_id=?',(active['task_id'],)).fetchone()[0]=='failed'
    assert (destination.parent/'boss-favorite-runs'/active['task_id']/'interrupted').exists()
    exported=client.get('/api/export').json()
    assert len(exported['boss_favorite_batches'])==1


def test_job_detail_favorite_uses_only_original_boss_source(store):
    client,tasks,*_=store
    with connect() as conn:
        company=conn.execute("INSERT INTO companies(name) VALUES('本地企业')").lastrowid
        job=conn.execute("INSERT INTO jobs(company_id,title,city) VALUES(?,'运营实习','北京')",(company,)).lastrowid
        conn.execute("INSERT INTO job_sources(job_id,platform,url) VALUES(?,'boss',?)",(job,URL))
    response=client.post(f'/api/boss-favorites/from-job/{job}')
    assert response.status_code==200,response.text
    data=response.json()
    assert data['targets'][0]['source_url']==URL and data['run']['action']=='favorite'
    assert client.get('/api/jobs').json()[0]['id']==job


def test_link_closing_after_matching_updates_target_and_allows_similar_search(store):
    client,*_=store
    batch=create(client);run,_=start(client,batch)
    data=finish(store,run,[{'id':batch['targets'][0]['id'],'state':'matched','candidates':[candidate()]}])
    favorite,_=start(client,data,'favorite')
    c=data['targets'][0]['candidates'][0]
    data=finish(store,favorite,[candidate(id=c['id'],availability='closed',favorite_status='not_checked')])
    assert data['targets'][0]['state']=='closed'
    assert not data['targets'][0]['candidates'][0]['selected']


def test_failed_read_can_be_retried_without_losing_selection(store):
    client,*_=store
    batch=create(client);run,_=start(client,batch)
    data=finish(store,run,[{'id':batch['targets'][0]['id'],'state':'matched','candidates':[candidate()]}])
    favorite,_=start(client,data,'favorite')
    c=data['targets'][0]['candidates'][0]
    data=finish(store,favorite,[candidate(id=c['id'],availability='failed',favorite_status='failed',error='网络读取失败')])
    assert data['targets'][0]['candidates'][0]['selected']==1
    retry,_=start(client,data,'favorite')
    assert retry['status']=='starting'


def test_headerless_import_is_also_limited_to_one_hundred_rows(store):
    client,*_=store
    response=client.post('/api/boss-favorites/preview',json={'text':'\n'.join('岗位'+str(i) for i in range(101))})
    assert response.status_code==400


def test_duplicate_matches_enqueue_only_one_favorite_and_update_both_rows(store):
    client,*_=store
    batch=create(client,'运营实习\n产品运营实习');run,_=start(client,batch)
    data=finish(store,run,[{'id':row['id'],'state':'matched','candidates':[candidate()]} for row in batch['targets']])
    favorite,_=start(client,data,'favorite')
    with connect() as conn:
        prefs=json.loads(conn.execute('SELECT preferences_json FROM boss_favorite_runs WHERE task_id=?',(favorite['task_id'],)).fetchone()[0])
    assert len(prefs['targets'])==1
    data=finish(store,favorite,[candidate(id=prefs['targets'][0]['id'],favorite_status='saved')])
    assert all(row['candidates'][0]['favorite_status']=='saved' and not row['candidates'][0]['selected'] for row in data['targets'])
