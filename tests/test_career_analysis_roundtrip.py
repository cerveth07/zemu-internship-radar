"""P3.3: career prompt and verified ChatGPT findings round-trip."""
import json

import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db, SCHEMA_VERSION
from app.main import app
from fe_helpers import frontend_source


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "p33.sqlite"))
    with TestClient(app) as instance:
        yield instance


def setup_job(c, name="模型公司", jd="熟悉 Python，参与 Agent 开发"):
    company=c.post("/api/companies",json={"name":name,"headcount_text":"10000人以上"})
    assert company.status_code==201,company.text
    job=c.post("/api/jobs",json={
        "company_id": company.json()["id"],"title":"Agent 实习",
        "jd_text":jd,"direction":"agent","notes":"PRIVATE JOB NOTE",
    })
    assert job.status_code==201,job.text
    return job.json()["id"]


def add_evidence(c, description="已有实习作品"):
    r=c.post("/api/evidence",json={
        "skill":"Python","proficiency":"working","evidence_type":"internship",
        "description":description,"source_url":"https://example.com/work",
        "review_status":"documented",
    })
    assert r.status_code==201,r.text
    return r.json()["id"]


def add_confirmed(c,jid,name="Python"):
    pkg=c.post("/api/analysis/packages",json={"job_ids":[jid]})
    assert pkg.status_code==200,pkg.text
    package_id=pkg.json()["package"]["package_id"]
    r=c.post("/api/requirements/import",json={
        "package_id":package_id,"dry_run":False,
        "requirements":[{
            "job_id":jid,"requirement_type":"technical_skill","name":name,
            "strength":"must","evidence":"熟悉 Python","source_field":"jd_text",
            "option_logic":"single","alternatives":[],
        }],
    })
    assert r.status_code==200,r.text
    pending=c.get("/api/requirements?status=pending").json()["items"]
    req=next(x for x in pending if x["job_id"]==jid)
    result=c.post(f"/api/requirements/{req['id']}/review",json={"action":"confirm"})
    assert result.status_code==200,result.text
    return req["id"]


def career(c,job_ids,evidence_ids=None):
    result=c.post("/api/career-analysis/export",json={
        "job_ids":job_ids,"mode":"compact","goal":"gap",
        "evidence_ids":evidence_ids or [],
    })
    assert result.status_code==200,result.text
    return result.json()["package_id"]


def findings(pid,jids=None,reqs=None,evidence=None):
    return {"package_id":pid,"findings":[{
        "kind":"learning_task","title":"提升 Python 开发",
        "content":"以 JD 作为学习证据","job_ids":jids or [],
        "requirement_ids":reqs or [],
        "personal_evidence_ids":evidence or [],"related_skill":"Python",
        "priority":"medium",
    }]}


def import_findings(c,data,**kwargs):
    return c.post("/api/analysis/import",json={**data,**kwargs})


def test_schema15_keeps_existing_records_and_idempotent_migration(client):
    jid=setup_job(client)
    evidence=add_evidence(client)
    rid=add_confirmed(client,jid)
    pid=career(client,[jid],[evidence])
    init_db()
    with connect() as conn:
        assert SCHEMA_VERSION>=15
        assert conn.execute("PRAGMA user_version").fetchone()[0]==SCHEMA_VERSION
        finding_fields={r["name"] for r in conn.execute("PRAGMA table_info(analysis_findings)")}
        snap_fields={r["name"] for r in conn.execute("PRAGMA table_info(career_export_snapshots)")}
        assert {"requirement_ids_json","personal_evidence_ids_json","career_stale_ack_json"}<=finding_fields
        assert "included_requirement_ids_json" in snap_fields
        snap=conn.execute("SELECT * FROM career_export_snapshots WHERE package_id=?",(pid,)).fetchone()
        assert json.loads(snap["included_requirement_ids_json"])==[rid]


def test_verified_refs_persist_and_are_carried_to_learning_task(client):
    jid=setup_job(client)
    personal=add_evidence(client)
    rid=add_confirmed(client,jid)
    pid=career(client,[jid],[personal])
    payload=findings(pid,[jid],[rid],[personal])
    preview=import_findings(client,payload,dry_run=True)
    assert preview.status_code==200,preview.text
    assert preview.json()["career_stale_token"]==""
    result=import_findings(client,payload,dry_run=False)
    assert result.status_code==200,result.text
    assert result.json()["new_count"]==1
    saved=client.get("/api/analysis/findings").json()[0]
    assert saved["requirement_ids"]==[rid]
    assert saved["personal_evidence_ids"]==[personal]
    assert saved["career_stale_ack"]=={}
    adopted=client.post(f"/api/analysis/findings/{saved['id']}/adopt-learning-task")
    assert adopted.status_code==200,adopted.text
    with connect() as conn:
        note=conn.execute("SELECT notes FROM learning_tasks WHERE id=?",
                          (adopted.json()["id"],)).fetchone()[0]
    assert str(rid) in note and str(personal) in note
    assert import_findings(client,payload,dry_run=False).json()["new_count"]==0


def test_refs_outside_export_or_legacy_package_are_rejected(client):
    jid=setup_job(client)
    selected=add_evidence(client)
    unselected=add_evidence(client,"只在其他记录中")
    rid=add_confirmed(client,jid)
    pid=career(client,[jid],[selected])
    for payload in (findings(pid,[jid],[rid+1000],[selected]),
                    findings(pid,[jid],[rid],[unselected])):
        response=import_findings(client,payload,dry_run=False)
        assert response.status_code==422,response.text
    legacy=client.post("/api/analysis/packages",json={"job_ids":[jid]}).json()["package"]["package_id"]
    assert import_findings(client,findings(legacy,[jid],[rid],[]),dry_run=False).status_code==422
    assert import_findings(client,findings(legacy,[jid],[],[selected]),dry_run=False).status_code==422


def test_personal_evidence_revision_needs_explicit_version_token(client):
    jid=setup_job(client)
    personal=add_evidence(client)
    rid=add_confirmed(client,jid)
    pid=career(client,[jid],[personal])
    payload=findings(pid,[jid],[rid],[personal])
    with connect() as conn:
        conn.execute("UPDATE profile_evidence SET description='证据改动版本 A' WHERE id=?",(personal,))
    preview=import_findings(client,payload,dry_run=True)
    assert preview.status_code==200,preview.text
    body=preview.json()
    assert body["career_stale_evidence_ids"]==[personal]
    assert body["career_stale_requirement_job_ids"]==[]
    token=body["career_stale_token"]
    assert len(token)==64
    assert import_findings(client,payload,dry_run=False).status_code==409
    with connect() as conn:
        conn.execute("UPDATE profile_evidence SET description='证据改动版本 B' WHERE id=?",(personal,))
    wrong=import_findings(client,payload,dry_run=False,acknowledge_career_stale_token=token)
    assert wrong.status_code==409,wrong.text
    fresh=import_findings(client,payload,dry_run=True).json()
    saved=import_findings(client,payload,dry_run=False,
                         acknowledge_career_stale_token=fresh["career_stale_token"])
    assert saved.status_code==200,saved.text
    audit=client.get("/api/analysis/findings").json()[0]["career_stale_ack"]
    assert audit["career_stale_evidence_ids"]==[personal]


def test_deleted_personal_evidence_is_reported_without_disclosing_content(client):
    jid=setup_job(client)
    selected=add_evidence(client,"敏感个人项目证据")
    pid=career(client,[jid],[selected])
    assert client.delete(f"/api/evidence/{selected}").status_code==200
    preview=import_findings(client,findings(pid,[jid],[],[selected]),dry_run=True)
    assert preview.status_code==200,preview.text
    assert preview.json()["career_missing_evidence_ids"]==[selected]
    assert "敏感个人项目证据" not in preview.text
    token=preview.json()["career_stale_token"]
    committed=import_findings(client,findings(pid,[jid],[],[selected]),
                              dry_run=False,acknowledge_career_stale_token=token)
    assert committed.status_code==200,committed.text


def test_confirmed_requirement_changed_blocks_silent_import(client):
    jid=setup_job(client)
    rid=add_confirmed(client,jid)
    pid=career(client,[jid])
    payload=findings(pid,[jid],[rid],[])
    # Reopening removes the requirement from current confirmed demand.
    reopened=client.post(f"/api/requirements/{rid}/review",json={"action":"reopen"})
    assert reopened.status_code==200,reopened.text
    preview=import_findings(client,payload,dry_run=True)
    assert preview.status_code==200,preview.text
    assert preview.json()["career_stale_requirement_job_ids"]==[jid]
    assert import_findings(client,payload,dry_run=False).status_code==409
    ok=import_findings(client,payload,dry_run=False,
                       acknowledge_career_stale_token=preview.json()["career_stale_token"])
    assert ok.status_code==200,ok.text
    assert client.get("/api/analysis/findings").json()[0]["career_stale_ack"]["career_stale_requirement_job_ids"]==[jid]


def test_old_findings_fingerprint_stays_idempotent(client):
    jid=setup_job(client)
    legacy=client.post("/api/analysis/packages",json={"job_ids":[jid]}).json()["package"]["package_id"]
    payload=findings(legacy,[jid])
    old={"package_id":legacy,"findings":[{
        "kind":"learning_task","title":"提升 Python 开发",
        "content":"以 JD 作为学习证据","job_ids":[jid],
        "related_skill":"Python","priority":"medium",
    }]}
    first=import_findings(client,old,dry_run=False)
    assert first.status_code==200,first.text
    assert first.json()["new_count"]==1
    again=import_findings(client,payload,dry_run=False)
    assert again.status_code==200,again.text
    assert again.json()["new_count"]==0


def test_unchanged_other_evidence_does_not_invalidate_selected_subset(client):
    jid=setup_job(client)
    opted=add_evidence(client)
    other=add_evidence(client,"没有上传的记录")
    pid=career(client,[jid],[opted])
    with connect() as conn:
        conn.execute("UPDATE profile_evidence SET description='未上传的资料更新' WHERE id=?",(other,))
    result=import_findings(client,findings(pid,[jid],[],[opted]),dry_run=True)
    assert result.status_code==200,result.text
    assert result.json()["career_stale_token"]==""


def test_invalid_ids_and_duplicates_rejected(client):
    jid=setup_job(client)
    pid=career(client,[jid])
    invalid=findings(pid,[jid],[-5],[])
    assert import_findings(client,invalid,dry_run=True).status_code==422
    invalid=findings(pid,[jid],[1,1],[])
    assert import_findings(client,invalid,dry_run=True).status_code==422
    invalid=findings(pid,[jid],[],[0])
    assert import_findings(client,invalid,dry_run=True).status_code==422


def test_frontend_templates_and_stale_warning_ui_are_integrated(client):
    js=frontend_source()
    html=client.get("/").text
    template=client.get("/static/career-prompts.js")
    assert template.status_code==200,template.text[:120]
    for name in ("gap","direction","preparation"):
        assert name in template.text
    assert 'careerPromptFor(goal' in template.text
    for key in ("download-career-prompt","careerChangeSummary",
                "acknowledge_career_stale_token",
                "career_stale_ack"):
        assert key in js
    assert "/static/career-prompts.js" in html


def test_uncited_jd_change_is_detected_for_whole_export_batch(client):
    jid=setup_job(client)
    pid=career(client,[jid])
    # A cross-role finding may cite no specific job, but relies on this batch.
    summary=findings(pid,[],[],[])
    revised=client.patch(f"/api/jobs/{jid}",json={
        "jd_text":"现在需要 Go，不再要求 Python",
    })
    assert revised.status_code==200,revised.text
    preview=import_findings(client,summary,dry_run=True)
    assert preview.status_code==200,preview.text
    assert preview.json()["career_stale_job_ids"]==[jid]
    assert import_findings(client,summary,dry_run=False).status_code==409
    token=preview.json()["career_stale_token"]
    committed=import_findings(client,summary,dry_run=False,acknowledge_career_stale_token=token)
    assert committed.status_code==200,committed.text


def test_deleting_uncited_job_flags_missing_source(client):
    jid=setup_job(client)
    pid=career(client,[jid])
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?",(jid,))
    preview=import_findings(client,findings(pid),dry_run=True)
    assert preview.status_code==200,preview.text
    assert preview.json()["career_missing_job_ids"]==[jid]
    assert import_findings(client,findings(pid),dry_run=False).status_code==409
