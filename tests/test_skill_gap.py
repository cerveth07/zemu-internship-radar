"""Skill gap aggregation, manual status persistence, and GPT provenance regression."""
import pytest
from fastapi.testclient import TestClient

from app.db import SCHEMA_VERSION, connect, init_db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "skill-gap.sqlite"))
    with TestClient(app) as c:
        yield c


def company(c, name):
    r = c.post("/api/companies", json={"name": name, "headcount_text":"10000人以上"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def job(c, cid, jd, priority="high", direction="agent"):
    r = c.post("/api/jobs", json={"company_id": cid, "title": "AI 实习岗位",
       "priority": priority, "direction": direction, "jd_text": jd})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def extract(c, ids, names, confirm=False):
    p = c.post("/api/analysis/packages", json={"job_ids": ids})
    assert p.status_code == 200, p.text
    pid = p.json()["package"]["package_id"]
    rows = []
    for jid, name, quote, strength in names:
        rows.append({"job_id": jid, "requirement_type": "technical_skill",
            "name": name, "strength": strength, "evidence": quote,
            "source_field": "jd_text", "option_logic": "single", "alternatives": []})
    r = c.post("/api/requirements/import", json={
        "package_id": pid, "requirements": rows, "dry_run": False})
    assert r.status_code == 200, r.text
    if confirm:
        for row in c.get("/api/requirements?status=pending&limit=100").json()["items"]:
            if row["package_id"] == pid:
                approved = c.post(f"/api/requirements/{row['id']}/review",
                                  json={"action": "confirm"})
                assert approved.status_code == 200, approved.text
    return pid


def test_target_scope_and_per_job_deduplication(client):
    cid = company(client, "测试甲")
    a = job(client, cid, "熟悉 Python，Python 项目加分", "high")
    b = job(client, cid, "熟悉 Python", "medium")
    c = job(client, cid, "熟悉 Python", "skip")
    extract(client, [a,b,c], [
       (a,"Python","熟悉 Python","must"),
       (a,"python","Python 项目加分","bonus"),
       (b,"Python","熟悉 Python","must"),
       (c,"Python","熟悉 Python","must"),
    ], confirm=True)
    data = client.get("/api/skill-gap").json()
    assert data["selected_jobs"] == 2
    assert len(data["skills"]) == 1
    skill = data["skills"][0]
    assert skill["job_count"] == 2
    assert skill["company_count"] == 1
    assert skill["must_jobs"] == 2
    assert skill["confirmed_jobs"] == 2
    assert skill["gpt_jobs"] == 0
    assert skill["status"] == "unknown"
    assert client.get("/api/skill-gap?scope=all").json()["skills"][0]["job_count"] == 3
    assert client.get("/api/skill-gap?direction=pm").json()["skills"] == []


def test_first_gpt_extraction_is_visible_as_clearly_pending_only_when_requested(client):
    cid = company(client, "测试候选")
    jid = job(client, cid, "熟悉 Agent，掌握 Python")
    extract(client, [jid], [
        (jid,"Agent","熟悉 Agent","must"),
        (jid,"Python","掌握 Python","must"),
    ], confirm=False)
    vetted = client.get("/api/skill-gap").json()
    assert vetted["skills"] == []
    provisional = client.get("/api/skill-gap", params={"include_pending": True}).json()
    assert provisional["covered_jobs"] == 0
    assert provisional["candidate_covered_jobs"] == 1
    assert {r["skill"] for r in provisional["skills"]} == {"Agent", "Python"}
    assert all(r["candidate_jobs"] == 1 and r["confirmed_jobs"] == 0
               and r["gpt_jobs"] == 0 for r in provisional["skills"])
    evidence = client.get("/api/skill-gap/evidence", params={
        "skill":"Python", "include_pending":True}).json()
    assert evidence["items"][0]["origin"] == "pending_candidate"
    assert client.get("/api/skill-gap/evidence",params={"skill":"Python"}).json()["total"] == 0


def test_manual_status_and_evidence_survive_restart_and_jd_change(client):
    cid = company(client,"测试乙")
    jid = job(client,cid,"负责 CI/CD，熟悉 Python")
    extract(client,[jid],[(jid,"CI/CD","负责 CI/CD","mentioned"),
                         (jid,"Python","熟悉 Python","must")],confirm=True)
    r = client.put("/api/skill-gap/status",json={"skill":"CI/CD",
        "status":"partial","note":"会配置流水线，尚缺真实项目"})
    assert r.status_code == 200, r.text
    assert client.put("/api/skill-gap/status",json={"skill":"Python",
        "status":"met","note":""}).status_code == 200
    export=client.get("/api/export").json()
    assert any(row["skill_key"]=="ci/cd" and row["status"]=="partial"
               for row in export["personal_skill_status"])
    profile = client.post("/api/evidence",json={"skill":"CI/CD",
        "proficiency":"working","evidence_type":"project",
        "description":"GitHub Actions 实战", "review_status":"documented"})
    assert profile.status_code == 201, profile.text
    task = client.post("/api/learning",json={"title":"CI/CD 部署项目",
        "related_skill":"CI/CD"})
    assert task.status_code == 201, task.text
    current = {s["skill"]:s for s in client.get("/api/skill-gap").json()["skills"]}
    assert current["CI/CD"]["status"] == "partial"
    assert current["CI/CD"]["personal_evidence"][0]["description"] == "GitHub Actions 实战"
    assert current["CI/CD"]["learning_tasks"][0]["title"] == "CI/CD 部署项目"
    init_db()
    assert SCHEMA_VERSION >= 20
    assert client.get("/api/skill-gap").json()["skills"][0]["status"] in ("partial","met")
    with connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    edited = client.patch(f"/api/jobs/{jid}",json={"jd_text":"现只需 Go"})
    assert edited.status_code == 200, edited.text
    assert client.get("/api/skill-gap").json()["skills"] == []
    with connect() as conn:
        assert conn.execute(
            "SELECT status FROM personal_skill_status WHERE skill_key='ci/cd'").fetchone()[0] == "partial"
    assert client.put("/api/skill-gap/status",json={"skill":"CI/CD",
        "status":"unknown","note":""}).status_code == 200
    assert client.put("/api/skill-gap/status",json={"skill":"Python",
        "status":"fake","note":""}).status_code == 422
    assert client.put("/api/skill-gap/status",json={"skill":" ",
        "status":"met","note":""}).status_code == 422
    # The JD parser permits 160-character requirement names; assessments must too.
    long_skill="技术"+("A"*145)
    assert client.put("/api/skill-gap/status",json={"skill":long_skill,
        "status":"partial","note":""}).status_code == 200
    assert client.put("/api/skill-gap/status",json={"skill":long_skill+"B"*20,
        "status":"partial","note":""}).status_code == 422


def test_gpt_reviewed_skill_never_becomes_human_confirmed(client):
    import io
    import json
    import zipfile

    cid = company(client,"测试丙")
    jid = job(client,cid,"熟悉 RAG 开发，负责 Agent 系统")
    extract(client,[jid],[(jid,"RAG","熟悉 RAG 开发","must")],confirm=False)
    before = client.get("/api/skill-gap").json()
    assert before["skills"] == []
    exported = client.post("/api/ai-review/export",json={"job_ids":[jid]})
    assert exported.status_code == 200, exported.text[:300]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
        batch = json.loads(z.read(manifest["batches"][0]["file"]))
    decision = [{
        "group_id": group["group_id"],
        "reviewed_ids": [item["requirement_id"] for item in group["items"]],
        "verdict": "verified", "normalized_name": "RAG",
        "reason": "逐字比对证据成立", "exceptions": [],
    } for group in batch["groups"]]
    imported = client.post("/api/ai-review/import",json={
        "batch_id":batch["batch_id"],"decisions":decision,"dry_run":False})
    assert imported.status_code == 200, imported.text
    data = client.get("/api/skill-gap").json()
    assert data["skills"][0]["skill"] == "RAG"
    assert data["skills"][0]["job_count"] == 1
    assert data["skills"][0]["confirmed_jobs"] == 0
    assert data["skills"][0]["gpt_jobs"] == 1
    assert data["skills"][0]["status"] == "unknown"
    assert client.get("/api/requirements/stats").json()["skills"] == []
    evidence = client.get("/api/skill-gap/evidence",params={"skill":"RAG"}).json()
    assert evidence["items"][0]["origin"] == "gpt_assisted"
    assert evidence["items"][0]["evidence"] == "熟悉 RAG 开发"
    assert client.patch(f"/api/jobs/{jid}",json={"jd_text":"现只需 SQL"}).status_code == 200
    assert client.get("/api/skill-gap").json()["skills"] == []


def test_filters_evidence_and_personal_status_are_independent(client):
    cid=company(client,"测试丁")
    high=job(client,cid,"熟悉 SQL","high","agent")
    low=job(client,cid,"熟悉 SQL","low","research")
    extract(client,[high,low],[
        (high,"SQL","熟悉 SQL","must"),(low,"SQL","熟悉 SQL","bonus")
    ],confirm=True)
    assert client.put("/api/skill-gap/status",json={
        "skill":"SQL","status":"missing","note":"准备学习"}).status_code == 200
    target=client.get("/api/skill-gap?scope=target&direction=agent").json()
    assert target["selected_jobs"]==1
    assert target["skills"][0]["status"]=="missing"
    assert target["skills"][0]["job_count"]==1
    assert len(client.get("/api/skill-gap/evidence",params={
        "skill":"SQL","scope":"target","direction":"agent"}).json()["items"])==1
    all_rows=client.get("/api/skill-gap/evidence",params={
        "skill":"SQL","scope":"all","limit":1}).json()
    assert all_rows["total"]==2 and len(all_rows["items"])==1
    assert client.get("/api/skill-gap?scope=all&direction=research").json()["skills"][0]["status"]=="missing"



def test_one_click_learning_creates_once_without_changing_mastery(client):
    cid = company(client, "学习测试公司")
    jid = job(client, cid, "熟悉 Python 与 Agent")
    extract(client, [jid], [(jid, "Python", "熟悉 Python", "must")], confirm=True)
    assert client.put("/api/skill-gap/status", json={
        "skill": "Python", "status": "partial", "note": "打算补足工程实践"
    }).status_code == 200

    created = client.post("/api/skill-gap/learning", json={"skill": " Python "})
    assert created.status_code == 200, created.text
    assert created.json()["created"] is True
    task = created.json()["task"]
    assert task["related_skill"] == "Python"
    assert task["status"] == "todo" and task["priority"] == "medium"

    repeat = client.post("/api/skill-gap/learning", json={"skill": "python"})
    assert repeat.status_code == 200
    assert repeat.json()["created"] is False
    assert repeat.json()["task"]["id"] == task["id"]
    assert len(client.get("/api/learning").json()) == 1

    gap = client.get("/api/skill-gap").json()["skills"][0]
    assert gap["status"] == "partial"
    assert gap["note"] == "打算补足工程实践"
    assert gap["has_active_learning"] is True
    assert gap["learning_tasks"][0]["id"] == task["id"]

    # Completed tasks are historical evidence, not an indefinitely active queue.
    assert client.patch(f"/api/learning/{task['id']}", json={
        "status": "done"
    }).status_code == 200
    assert client.get("/api/skill-gap").json()["skills"][0]["has_active_learning"] is False
    again = client.post("/api/skill-gap/learning", json={"skill": "Python"})
    assert again.json()["created"] is True
    assert again.json()["task"]["id"] != task["id"]
    assert client.get("/api/skill-gap").json()["skills"][0]["has_active_learning"] is True


def test_one_click_learning_recognizes_an_existing_manual_task(client):
    previous = client.post("/api/learning", json={
        "title": "自行学习 RAG", "related_skill": "RAG", "status": "doing"
    })
    assert previous.status_code == 201
    result = client.post("/api/skill-gap/learning", json={"skill": "rag"})
    assert result.status_code == 200, result.text
    assert result.json()["created"] is False
    assert result.json()["task"]["id"] == previous.json()["id"]
    assert len(client.get("/api/learning").json()) == 1
    assert client.post("/api/skill-gap/learning", json={"skill": "  "}).status_code == 422
