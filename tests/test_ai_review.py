"""P4 minimal integration: enterprise navigation, GPT review ZIP, proof, undo and P2/P3 provenance."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.db import SCHEMA_VERSION, connect, init_db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "ai-review.sqlite"))
    with TestClient(app) as c:
        yield c


def company_and_job(c, company_name="Agent 团队"):
    co = c.post("/api/companies", json={
        "name": company_name, "headcount_text":"1000-9999人",
        "headcount_source_url": "https://example.com/company",
    })
    assert co.status_code == 201, co.text
    cid = co.json()["id"]
    job = c.post("/api/jobs", json={"company_id":cid,"title":"LLM Agent 实习",
        "jd_text":"需要熟悉 Python 和 SQL；负责 Agent 系统开发；Python 经验优先",
        "eligibility":"2027 届，至少实习四天", "priority":"high"})
    assert job.status_code == 201, job.text
    return cid, job.json()["id"]


def add_requirements(c,job_id, rows=None):
    pkg=c.post("/api/analysis/packages",json={"job_ids":[job_id]})
    assert pkg.status_code==200,pkg.text
    pid=pkg.json()["package"]["package_id"]
    rows=rows or [
        ("Python","需要熟悉 Python 和 SQL","technical_skill","must"),
        ("SQL","需要熟悉 Python 和 SQL","technical_skill","must"),
        ("Agent 开发","负责 Agent 系统开发","responsibility","mentioned"),
    ]
    payload=[{"job_id":job_id,"name":name,"evidence":evidence,
              "source_field":"jd_text","requirement_type":rtype,
              "strength":strength,"option_logic":"single","alternatives":[]}
             for name,evidence,rtype,strength in rows]
    response=c.post("/api/requirements/import",json={
        "package_id":pid,"requirements":payload,"dry_run":False})
    assert response.status_code==200,response.text
    return pid


def review_bundle(c,job_ids=None,include_reviewed=False):
    r=c.post("/api/ai-review/export",json={
        "job_ids":job_ids or [],"include_reviewed":include_reviewed})
    assert r.status_code==200,r.text[:400]
    assert r.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        manifest=json.loads(archive.read("manifest.json"))
        assert manifest["total"]>0
        content=[]
        for meta in manifest["batches"]:
            raw=archive.read(meta["file"])
            import hashlib
            assert hashlib.sha256(raw).hexdigest()==meta["sha256"]
            content.append(json.loads(raw))
        assert "review-instructions.md" in archive.namelist()
    return content


def model_answer(batch,exclusion=False):
    decisions=[]
    for group in batch["groups"]:
        decisions.append({
            "group_id":group["group_id"],
            "reviewed_ids":[row["requirement_id"] for row in group["items"]],
            "verdict":"excluded" if exclusion or group["type"]=="responsibility" else "verified",
            "normalized_name":"",
            "reason":"人工复核测试：逐条对照原文和条件",
            "exceptions":[],
        })
    return {"batch_id":batch["batch_id"],"decisions":decisions}


def test_company_jobs_and_scale_are_visible_and_editable(client):
    cid,jid=company_and_job(client)
    linked=client.get(f"/api/companies/{cid}/jobs")
    assert linked.status_code==200,linked.text
    assert [j["id"] for j in linked.json()]==[jid]
    assert linked.json()[0]["title"]=="LLM Agent 实习"
    jobs=client.get("/api/jobs").json()
    assert next(j for j in jobs if j["id"]==jid)["company_headcount_text"] == '1000-9999人'
    updated=client.patch(f"/api/companies/{cid}",json={
        "headcount_text":"20-99人"})
    assert updated.status_code==200,updated.text
    newer=client.get("/api/jobs").json()
    matched=next(j for j in newer if j["id"]==jid)
    assert matched["company_headcount_text"]=="20-99人"
    assert matched["company_headcount_min"]==20
    assert client.get("/api/companies/99999/jobs").status_code==404
    init_db()
    with connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0]==SCHEMA_VERSION
        assert conn.execute("PRAGMA quick_check").fetchone()[0]=="ok"


def test_same_evidence_for_python_and_sql_is_not_false_duplicate(client):
    _,jid=company_and_job(client)
    add_requirements(client,jid)
    report=client.get("/api/ai-review/diagnosis").json()
    assert report["pending"]==3
    assert report["shared_quote_rows"]==2
    assert report["groups"]==3
    assert report["flags"]["non_skill"]==1
    assert client.get("/api/ai-review/groups").json()["total_groups"]==3


def test_gpt_review_does_not_promote_human_confirmation_and_can_undo(client):
    _,jid=company_and_job(client)
    add_requirements(client,jid)
    batch=review_bundle(client,[jid])[0]
    answer=model_answer(batch)
    preview=client.post("/api/ai-review/import",json={**answer,"dry_run":True})
    assert preview.status_code==200,preview.text
    assert preview.json()["covered"]==3
    assert client.get("/api/requirements?status=pending").json()["total"]==3
    before=client.get("/api/requirements/stats").json()
    assert before["confirmed_requirements"]==0
    assert before["ai_reviewed_requirements"]==0
    committed=client.post("/api/ai-review/import",json={**answer,"dry_run":False})
    assert committed.status_code==200,committed.text
    event=committed.json()["event_id"]
    assert committed.json()["changed"]==3
    assert client.get("/api/requirements?status=pending").json()["total"]==3
    stats=client.get("/api/requirements/stats?include_ai_reviewed=true").json()
    assert stats["confirmed_requirements"]==0
    assert stats["ai_reviewed_requirements"]==2
    assert {x["skill"] for x in stats["ai_skills"]}=={"Python","SQL"}
    assert client.get("/api/requirements/stats").json()["ai_skills"]==[]
    exported=client.post("/api/career-analysis/export",json={
        "job_ids":[jid],"mode":"compact","include_ai_reviewed":True})
    assert exported.status_code==200,exported.text[:500]
    career=exported.json()
    assert career["jobs"][0]["confirmed_requirements"]==[]
    assert len(career["jobs"][0]["gpt_reviewed_requirements"])==2
    assert all(x["review_origin"]=="gpt_assisted_not_human_confirmed"
               for x in career["jobs"][0]["gpt_reviewed_requirements"])
    from app.career_export import compare_career_snapshot
    with connect() as conn:
        assert compare_career_snapshot(conn,career["package_id"])["career_stale_token"]==""
    undone=client.post(f"/api/ai-review/undo/{event}")
    assert undone.status_code==200,undone.text
    assert client.get("/api/requirements/stats?include_ai_reviewed=true").json()["ai_reviewed_requirements"]==0
    with connect() as conn:
        assert compare_career_snapshot(conn,career["package_id"])["career_stale_token"]
    assert client.post(f"/api/ai-review/undo/{event}").status_code==404


def test_gpt_batch_must_cover_every_group_and_member(client):
    _,jid=company_and_job(client)
    add_requirements(client,jid)
    batch=review_bundle(client,[jid])[0]
    answer=model_answer(batch)
    bad=json.loads(json.dumps(answer));bad["decisions"].pop()
    assert client.post("/api/ai-review/import",json={**bad,"dry_run":False}).status_code==422
    bad=json.loads(json.dumps(answer))
    bad["decisions"][0]["reviewed_ids"]=[]
    assert client.post("/api/ai-review/import",json={**bad,"dry_run":False}).status_code==422
    bad=json.loads(json.dumps(answer))
    bad["decisions"][0]["reviewed_ids"]=[999999]
    assert client.post("/api/ai-review/import",json={**bad,"dry_run":False}).status_code==422
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_requirement_reviews").fetchone()[0]==0


def test_changed_jd_blocks_stale_gpt_review_without_partial_writes(client):
    _,jid=company_and_job(client)
    add_requirements(client,jid)
    batch=review_bundle(client,[jid])[0]
    with connect() as conn:
        conn.execute("UPDATE jobs SET jd_text='现要求 Go 编程' WHERE id=?",(jid,))
    response=client.post("/api/ai-review/import",
                          json={**model_answer(batch),"dry_run":False})
    assert response.status_code==409,response.text
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_requirement_reviews").fetchone()[0]==0


def test_bulk_import_atomic_and_idempotent(client):
    _,jid=company_and_job(client)
    add_requirements(client,jid)
    batches=review_bundle(client,[jid])
    # Separate exported copies represent different attempts; duplicate sources
    # in the same bulk request must fail as ambiguous.
    second=review_bundle(client,[jid])
    combined={"results":[model_answer(batches[0]),model_answer(second[0])],
              "dry_run":False}
    response=client.post("/api/ai-review/import-bulk",json=combined)
    assert response.status_code==422,response.text
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_requirement_reviews").fetchone()[0]==0
    okay=client.post("/api/ai-review/import-bulk",json={
        "results":[model_answer(batches[0])],"dry_run":False})
    assert okay.status_code==200,okay.text
    assert okay.json()["changed"]==3
    again=client.post("/api/ai-review/import-bulk",json={
        "results":[model_answer(batches[0])],"dry_run":False})
    assert again.status_code==200,again.text
    assert again.json()["changed"]==0


def test_gpt_reimport_cannot_override_user_group_decision(client):
    _,jid=company_and_job(client)
    add_requirements(client,jid)
    batch=review_bundle(client,[jid])[0]
    answer=model_answer(batch)
    assert client.post("/api/ai-review/import",json={
        **answer,"dry_run":False}).status_code==200
    group=client.get("/api/ai-review/groups").json()["manual_queue"][0]
    decision=client.post("/api/ai-review/decide",json={
        "key":group["key"],"verdict":"uncertain","note":"需要额外人工证据"})
    assert decision.status_code==200,decision.text
    rejected=client.post("/api/ai-review/import",json={
        **answer,"dry_run":False})
    assert rejected.status_code==409,rejected.text
    assert client.post(f"/api/ai-review/undo/{decision.json()['event_id']}").status_code==200
    restored=client.post("/api/ai-review/import",json={
        **answer,"dry_run":False})
    assert restored.status_code==200,restored.text


def test_manual_group_action_and_undo_are_separate_from_human_review(client):
    _,jid=company_and_job(client)
    add_requirements(client,jid)
    group=client.get("/api/ai-review/groups").json()["manual_queue"][0]
    assert client.post("/api/ai-review/decide",json={
        "key":group["key"],"verdict":"verified",
        "note":"仅有三个样例，不应冒充全部 GPT 复核"}).status_code==409
    saved=client.post("/api/ai-review/decide",json={
        "key":group["key"],"verdict":"uncertain",
        "note":"该组需要继续和 GPT 确认"})
    assert saved.status_code==200,saved.text
    assert saved.json()["affected"]==1
    event=saved.json()["event_id"]
    assert client.get("/api/requirements?status=pending").json()["total"]==3
    assert client.post(f"/api/ai-review/undo/{event}").status_code==200



def test_more_than_150_requirements_split_into_atomic_gpt_batches(client):
    _,jid=company_and_job(client)
    rows=[(f"能力概念 {n:03d}", "需要熟悉 Python 和 SQL",
           "technical_skill","must") for n in range(160)]
    add_requirements(client,jid,rows)
    batches=review_bundle(client,[jid])
    assert len(batches)>=2
    all_ids=[x["requirement_id"] for batch in batches
             for g in batch["groups"] for x in g["items"]]
    assert len(all_ids)==160 and len(set(all_ids))==160
    queue=client.get("/api/ai-review/groups").json()
    assert queue["total_groups"]==160
    assert len(queue["manual_queue"])==40
    assert queue["deferred_groups"]==120
    results=[model_answer(batch) for batch in batches]
    preview=client.post("/api/ai-review/import-bulk",
                        json={"results":results,"dry_run":True})
    assert preview.status_code==200,preview.text
    assert preview.json()["covered"]==160
    committed=client.post("/api/ai-review/import-bulk",
                          json={"results":results,"dry_run":False})
    assert committed.status_code==200,committed.text
    assert committed.json()["changed"]==160
    assert client.get("/api/requirements?status=pending").json()["total"]==160



def test_frontend_and_desktop_review_routes():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    appjs=(root/"static"/"app.js").read_text(encoding="utf-8")
    aijs=(root/"static"/"ai-review.js").read_text(encoding="utf-8")
    html=(root/"static"/"index.html").read_text(encoding="utf-8")
    assert "viewCompanyJobs" in appjs and "jumpToCompanyJob" in appjs
    assert "企业规模" in appjs and "companyScaleSummary" in appjs
    assert "aiReviewPanel()" in (root/"static"/"analysis-workflows.js").read_text(encoding="utf-8")
    assert html.index("ai-review.js")<html.index("analysis-workflows.js")
    for token in ("ai-review/export","ai-review/import","ai-review/groups",
                  "ai-review/undo","data-ai-review-file"):
        assert token in aijs


def test_requirement_edit_invalidates_gpt_verdict_and_resume_export(client):
    _, jid = company_and_job(client)
    add_requirements(client, jid)
    batch = review_bundle(client, [jid])[0]
    first = client.post("/api/ai-review/import", json={
        **model_answer(batch), "dry_run": False})
    assert first.status_code == 200, first.text
    assert client.get("/api/requirements/stats?include_ai_reviewed=true").json()[
        "ai_reviewed_requirements"] == 2

    with connect() as conn:
        python = dict(conn.execute(
            "SELECT * FROM job_requirements WHERE job_id=? AND name='Python'",
            (jid,)).fetchone())
        responsibility = dict(conn.execute(
            "SELECT * FROM job_requirements WHERE job_id=? AND name='Agent 开发'",
            (jid,)).fetchone())

    edited = client.put(f"/api/requirements/{python['id']}", json={
        "job_id": jid, "requirement_type": "technical_skill",
        "name": "Python 编程", "strength": "must",
        "evidence": python["evidence"], "source_field": "jd_text",
        "option_logic": "single", "alternatives": []})
    assert edited.status_code == 200, edited.text
    stats = client.get("/api/requirements/stats?include_ai_reviewed=true").json()
    assert stats["ai_reviewed_requirements"] == 1
    assert {x["skill"] for x in stats["ai_skills"]} == {"SQL"}
    assert client.get("/api/ai-review/diagnosis").json()["not_reviewed"] == 1

    # Previously excluded responsibilities also return to pending on edit.
    changed = client.put(f"/api/requirements/{responsibility['id']}", json={
        "job_id": jid, "requirement_type": "responsibility",
        "name": "Agent 实际开发职责", "strength": "mentioned",
        "evidence": responsibility["evidence"], "source_field": "jd_text",
        "option_logic": "single", "alternatives": []})
    assert changed.status_code == 200, changed.text
    pending = client.get("/api/requirements/stats?include_pending=true").json()
    assert pending["pending_requirements"] == 3

    outstanding = review_bundle(client, [jid])
    ids = {r["requirement_id"] for b in outstanding
           for group in b["groups"] for r in group["items"]}
    assert ids == {python["id"], responsibility["id"]}
    full = review_bundle(client, [jid], include_reviewed=True)
    assert sum(len(g["items"]) for b in full for g in b["groups"]) == 3
    stale_answer = client.post("/api/ai-review/import", json={
        **model_answer(batch), "dry_run": False})
    assert stale_answer.status_code == 409, stale_answer.text


def test_manual_acceptance_keeps_gpt_normalized_name_and_undo(client):
    _, jid = company_and_job(client)
    add_requirements(client, jid)
    batch = review_bundle(client, [jid])[0]
    answer = model_answer(batch)
    for group, decision in zip(batch["groups"], answer["decisions"]):
        if group["name"] == "Python":
            decision["normalized_name"] = "Python 3"
    imported = client.post("/api/ai-review/import", json={
        **answer, "dry_run": False})
    assert imported.status_code == 200, imported.text
    group = next(x for x in client.get("/api/ai-review/groups").json()[
        "manual_queue"] if x["name"] == "Python")
    accepted = client.post("/api/ai-review/decide", json={
        "key": group["key"], "verdict": "verified",
        "note": "模型逐条复核，确认保留其名称归一化"})
    assert accepted.status_code == 200, accepted.text
    with connect() as conn:
        assert conn.execute(
            "SELECT normalized_name FROM ai_requirement_reviews "
            "WHERE requirement_id IN (SELECT id FROM job_requirements "
            "WHERE job_id=? AND name='Python')", (jid,)).fetchone()[0] == "Python 3"
    assert client.post(
        f"/api/ai-review/undo/{accepted.json()['event_id']}").status_code == 200
    assert {x["skill"] for x in client.get(
        "/api/requirements/stats?include_ai_reviewed=true").json()["ai_skills"]
        } == {"Python 3", "SQL"}


def test_large_review_group_uses_bounded_sql_placeholders(client):
    _, jid = company_and_job(client)
    add_requirements(client, jid)
    with connect() as conn:
        template = dict(conn.execute(
            "SELECT * FROM job_requirements WHERE job_id=? LIMIT 1",
            (jid,)).fetchone())
        conn.executemany("""INSERT INTO job_requirements(
            job_id,package_id,requirement_type,name,strength,evidence,
            source_field,option_logic,alternatives_json,source_jd_hash,fingerprint)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            [(jid, template["package_id"], "technical_skill", "规模测试技能",
              "must", template["evidence"], "jd_text", "single", "[]",
              template["source_jd_hash"], f"group-scale-{n}")
             for n in range(1002)])
    group = next(x for x in client.get("/api/ai-review/groups").json()[
        "manual_queue"] if x["name"] == "规模测试技能")
    assert group["count"] == 1002
    decided = client.post("/api/ai-review/decide", json={
        "key": group["key"], "verdict": "uncertain",
        "note": "该分组只是安全待定，不代表已人工审过千条"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["affected"] == 1002
    assert client.post(
        f"/api/ai-review/undo/{decided.json()['event_id']}").status_code == 200


def test_group_exclusion_requires_full_gpt_coverage(client):
    _, jid = company_and_job(client)
    add_requirements(client, jid)
    queue = client.get("/api/ai-review/groups").json()["manual_queue"]
    group = next(x for x in queue if x["name"] == "Python")
    assert client.post("/api/ai-review/decide", json={
        "key": group["key"], "verdict": "excluded",
        "note": "仅凭前三条示例不应排除整组"}).status_code == 409
    batch = review_bundle(client, [jid])[0]
    imported = client.post("/api/ai-review/import", json={
        **model_answer(batch), "dry_run": False})
    assert imported.status_code == 200, imported.text
    refreshed = next(x for x in client.get("/api/ai-review/groups").json()[
        "manual_queue"] if x["name"] == "Python")
    assert refreshed["gpt_reviewed"] == refreshed["count"]
    saved = client.post("/api/ai-review/decide", json={
        "key": refreshed["key"], "verdict": "excluded",
        "note": "已核对整组模型复核与岗位原文"})
    assert saved.status_code == 200, saved.text
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM job_requirements WHERE job_id=? "
            "AND review_status='confirmed'", (jid,)).fetchone()[0] == 0
    assert client.post(
        f"/api/ai-review/undo/{saved.json()['event_id']}").status_code == 200
