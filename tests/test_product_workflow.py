"""Protect user edits across windows and make large libraries usable."""
import json
import shutil

import pytest
from fastapi.testclient import TestClient
from app.main import app, ApplicationIn
from fe_helpers import HARNESS, run_node


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'workflow.sqlite'))
    with TestClient(app) as client:
        yield client


def make_job(client):
    company = client.post('/api/companies', json={'name': '窗口测试企业'}).json()
    result = client.post('/api/jobs', json={
        'company_id': company['id'], 'title': '产品实习', 'jd_text': '旧 JD'})
    assert result.status_code == 201, result.text
    return '/api/jobs/' + str(result.json()['id'])


def test_unrelated_job_change_survives_application_save(client):
    url = make_job(client)
    assert client.patch(url, json={'jd_text': '新 JD', 'priority': 'high'}).status_code == 200
    body = {'application': {'stage': 'resume_sent'},
            'expected_application': ApplicationIn().model_dump()}
    assert client.patch(url, json=body).status_code == 200
    saved = client.get(url).json()
    assert (saved['jd_text'], saved['priority'], saved['application']['stage']) == ('新 JD', 'high', 'resume_sent')
    # Retrying an identical saved form is harmless.
    assert client.patch(url, json=body).json()['changed'] is False


def test_conflicting_job_or_application_save_is_atomic(client):
    url = make_job(client)
    client.patch(url, json={'jd_text': '其他窗口 JD'})
    before = client.get(url).json()
    result = client.patch(url, json={'jd_text': '我的 JD', 'expected_fields': {'jd_text': '旧 JD'},
                                    'application': {'stage': 'resume_sent'}})
    assert result.status_code == 409, result.text
    assert client.get(url).json() == before
    client.patch(url, json={'application': {'stage': 'interview', 'notes': '明天面试'}})
    before = client.get(url).json()
    result = client.patch(url, json={'priority': 'high', 'expected_fields': {'priority': 'unrated'},
                                    'application': {'stage': 'resume_sent'},
                                    'expected_application': ApplicationIn().model_dump()})
    assert result.status_code == 409, result.text
    assert client.get(url).json() == before


def test_field_guard_is_scoped_and_rejects_malformed_fields(client):
    url = make_job(client)
    client.patch(url, json={'jd_text': '新 JD'})
    body = {'priority': 'high', 'expected_fields': {'priority': 'unrated'}}
    assert client.patch(url, json=body).status_code == 200
    assert client.patch(url, json=body).json()['changed'] is False
    for expected in ({'missing': ''}, {}, {'priority': 'high', 'jd_text': '旧 JD'}):
        response = client.patch(url, json={'priority': 'low', 'expected_fields': expected})
        assert response.status_code == 422, response.text


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js not installed')
def test_dirty_fields_and_search_over_702_jobs():
    run_node('const fe=require(' + json.dumps(str(HARNESS)) + ');\n' + r"""
const assert=require('node:assert/strict');
const result={innerHTML:''};
const ctx=fe.boot({state:{jobs:Array.from({length:702},(_,i)=>({id:i+1,title:'岗位 '+(i+1),company:'企业 '+i}))},
 $:()=>result,esc:x=>String(x??''),stages:{}});
fe.run(ctx,fe.read('job-library.js'));
ctx.renderApplyJobResults();
assert.equal((result.innerHTML.match(/data-apply-job=/g)||[]).length,25);
assert.ok(result.innerHTML.includes('702'));
ctx.renderApplyJobResults('岗位 702');
assert.ok(result.innerHTML.includes('data-apply-job="702"'));
ctx.renderApplyJobResults('不存在');
assert.ok(result.innerHTML.includes('没有匹配'));
const data=Object.fromEntries(['title','direction','city','salary','duration','onsite_days','team','eligibility','posted_at','deadline','status','priority','jd_text','notes','resume_version','recruiter','last_contact','next_action','application_notes'].map(k=>[k,'']));
data.stage='not_applied';data.jd_text='旧 JD';
const application={stage:'not_applied',resume_version:'',recruiter:'',last_contact:'',next_action:'',notes:''};
const form={dataset:{initial:JSON.stringify(data),application:JSON.stringify(application)}};
assert.equal(JSON.stringify(ctx.jobUpdatePayload(form,data)),'{}');
data.stage='resume_sent';
let payload=ctx.jobUpdatePayload(form,data);
assert.equal(payload.jd_text,undefined);
assert.equal(payload.application.stage,'resume_sent');
assert.equal(payload.expected_application.stage,'not_applied');
data.jd_text='我的 JD';payload=ctx.jobUpdatePayload(form,data);
assert.equal(payload.expected_fields.jd_text,'旧 JD');
assert.equal(payload.jd_text,'我的 JD');
""")


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js not installed')
def test_intake_return_only_rechecks_unchanged_snapshots():
    run_node('const fe=require(' + json.dumps(str(HARNESS)) + ');\n' + r"""
const assert=require('node:assert/strict');
const boxes={1:{checked:false},2:{checked:false},3:{checked:false,disabled:true}};
const notice={textContent:''};
const ctx=fe.boot({document:{addEventListener(){},querySelector(selector){return boxes[selector.match(/select="(\d+)"/)[1]]}},
 $:()=>notice});
fe.run(ctx,fe.read('capture-bulk.js'));
fe.run(ctx,`captureIntakePreview={items:[{id:1,version:'same'},{id:2,version:'updated'},{id:3,version:'same'}]};
 captureIntakePreselect=new Map([[1,'same'],[2,'old'],[3,'same']]);
 updateCaptureIntakeSelection=()=>{};applyIntakePreselect();`);
assert.equal(boxes[1].checked,true);
assert.equal(boxes[2].checked,false);
assert.equal(boxes[3].checked,false);
assert.ok(notice.textContent.includes('2 条'));
// Saving a draft or cancelling returns the edited row as well, without checking it.
fe.run(ctx,`captureIntakeReturn={ids:[1,2,3],batch_id:7,selected:[[1,'same']],edited:2};
 openCaptureBulkReview=async function(ids,batch){this.reopened=[ids,batch]};`);
ctx.resumeCaptureIntake(2,false).then(ok=>{
 assert.equal(ok,true);
 assert.equal(JSON.stringify(ctx.reopened),'[[1,2,3],7]');
 fe.run(ctx,'this.preset=[...captureIntakePreselect]');
 assert.equal(JSON.stringify(ctx.preset),'[[1,"same"]]');
}).catch(error=>{console.error(error);process.exit(1)});
""")
