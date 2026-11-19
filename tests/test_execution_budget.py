"""Plan allowances, staged persistence and uncertainty without platform requests."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'budget.sqlite'))
    tasks = {}
    received = []
    def start(preferences):
        received.append(preferences)
        task = f'owned-{len(tasks)+1}'
        tasks[task] = {'task_id':task,'run_id':task,'status':'running','items':[],
                       'candidates':[], 'receipts':[], 'total':0,'error':'','finished_at':None}
        return {'task':{'id':task,'status':'running','mode':preferences.get('collection_mode','collect')}}
    def page(task, limit=200, offset=0):
        value = tasks[task]
        return {**value,'total':len(value['items']),'items':value['items'][offset:offset+limit]}
    runtime = SimpleNamespace(capabilities=lambda:{'version':1,'matched_results':True},
        readiness=lambda:{'ok':True},start_search=start,result_page=page)
    import app as package
    monkeypatch.setattr(package,'shixiseng_runtime',runtime,raising=False)
    with TestClient(app) as client:
        yield client,tasks,received


def plan(client):
    family={'id':'B01','route':'Agent','name':'Agent产品','priority':'P0','representative':'Agent产品'}
    assert client.post('/api/planner/library/import',json={'families':[family],'expected_version':0}).status_code==200
    snapshot=client.post('/api/planner/plans',json={'family_ids':['B01'],'cities':['北京'],'platforms':['shixiseng'],
        'list_page_budget':2,'detail_attempt_budget':4,'time_budget_minutes':1,'pages_per_query':2,'expected_version':1}).json()
    result=client.post(f"/api/planner/plans/{snapshot['id']}/actions/{snapshot['actions'][0]['id']}/prepare").json()
    return result['batch_id']


def collect(client,batch,mode='discover',**extra):
    result=client.post('/api/capture/connectors/shixiseng/start-collect',json={
        'batch_id':batch,'confirmed':True,'collection_mode':mode,'max_pages':3,'max_jobs':60,**extra})
    assert result.status_code==200,result.text
    return result.json()['search_run']


def receipt(key,kind,url='https://www.shixiseng.com/intern/inn_one',status='completed'):
    stamp=datetime.now(timezone.utc).isoformat()
    return {'id':key,'kind':kind,'status':status,'url':url,'keyword':'Agent产品','city':'北京','page':1,
            'started_at':stamp,'finished_at':stamp if status=='completed' else None}


def test_discovery_persists_without_import_and_hydrate_reserves_only_details(fixture):
    client,tasks,received=fixture
    batch=plan(client)
    run=collect(client,batch)
    assert received[-1]['max_list_attempts']==2
    assert received[-1]['_execution_budget']['detail_attempts']==0
    value=tasks[run['task_id']]
    value.update(status='completed',finished_at=datetime.now(timezone.utc).isoformat(),
        receipts=[receipt('list-1','list')],candidates=[{'url':'https://www.shixiseng.com/intern/inn_one',
            'title':'Agent产品实习','company':'测试公司','city':'北京','detail_state':'pending',
            'hits':[{'keyword':'Agent产品','city':'北京','page':1}]}])
    assert client.get(f"/api/capture/search-runs/{run['id']}").status_code==200
    assert client.get(f"/api/capture/search-runs/{run['id']}/discovery").json()['pending_count']==1
    assert client.get(f'/api/capture/batches/{batch}/items').json()==[]
    later=collect(client,batch,'hydrate')
    assert later['status']=='running'
    assert received[-1]['collection_mode']=='hydrate'
    assert received[-1]['max_list_attempts']==0
    assert received[-1]['max_jobs']==4
    assert received[-1]['candidates'][0]['url'].endswith('inn_one')
    # Simulate a crashed visit. Its allowance stays reserved instead of being refunded.
    with connect() as conn:
        conn.execute("UPDATE capture_search_runs SET status='failed',finished_at=datetime('now') WHERE id=?",(later['id'],))
    blocked=client.post('/api/capture/connectors/shixiseng/search-preview',json={'batch_id':batch,'collection_mode':'hydrate'})
    assert blocked.status_code==409
    assert '详情额度已用尽' in blocked.json()['detail'] or '时间额度已用尽' in blocked.json()['detail']


def test_completed_receipts_release_unused_allowance_but_count_failed(fixture):
    client,tasks,received=fixture
    batch=plan(client)
    run=collect(client,batch,'collect')
    value=tasks[run['task_id']]
    value.update(status='completed_with_errors',finished_at=datetime.now(timezone.utc).isoformat(),
        receipts=[receipt('l1','list'),receipt('d1','detail',status='failed')])
    assert client.get(f"/api/capture/search-runs/{run['id']}").status_code==200
    preview=client.post('/api/capture/connectors/shixiseng/search-preview',json={'batch_id':batch,'max_pages':3,'max_jobs':60}).json()
    assert preview['preferences']['max_pages']['shixiseng']==1
    assert preview['max_jobs']==3
    # Altering a prepared query cannot bypass the plan's identity restrictions.
    with connect() as conn:
        conn.execute("UPDATE capture_batches SET keyword='other' WHERE id=?",(batch,))
    assert client.post('/api/capture/connectors/shixiseng/search-preview',json={'batch_id':batch}).status_code==409


def test_prepare_failure_does_not_spend_visit_budget(fixture):
    client,tasks,received=fixture
    batch=plan(client)
    import app as package
    package.shixiseng_runtime.readiness=lambda:{'ok':False,'messages':['fixture not ready']}
    failed=client.post('/api/capture/connectors/shixiseng/start-collect',json={'batch_id':batch,'confirmed':True})
    assert failed.status_code==409
    preview=client.post('/api/capture/connectors/shixiseng/search-preview',json={'batch_id':batch,'max_pages':3,'max_jobs':60})
    assert preview.status_code==200
    assert preview.json()['max_jobs']==4
    assert received==[]


def test_plan_time_budget_prevents_new_actions_after_spend(fixture):
    client,tasks,received=fixture
    batch=plan(client)
    run=collect(client,batch)
    with connect() as conn:
        conn.execute("UPDATE capture_search_runs SET status='completed',created_at=datetime('now','-70 seconds'),finished_at=datetime('now'),platform_state_json=? WHERE id=?",
                     (json.dumps({'ledger_reported':True}),run['id']))
    response=client.post('/api/capture/connectors/shixiseng/search-preview',json={'batch_id':batch})
    assert response.status_code==409
    assert '时间额度已用尽' in response.json()['detail']


def test_legacy_plan_visit_without_ledger_is_not_refunded(fixture):
    client,tasks,received=fixture
    batch=plan(client)
    with connect() as conn:
        conn.execute("INSERT INTO capture_search_runs(batch_id,platform,preferences_json,status,finished_at) VALUES(?,'shixiseng',?,'completed',datetime('now'))",(batch,json.dumps({'keywords':['Agent产品'],'cities':['北京']})))
    response=client.post('/api/capture/connectors/shixiseng/search-preview',json={'batch_id':batch})
    assert response.status_code==409
    assert '额度已用尽' in response.json()['detail']
