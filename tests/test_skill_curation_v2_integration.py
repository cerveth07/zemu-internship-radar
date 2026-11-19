"""API integration tests for three-pass skill curation."""
import json
import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "v2.sqlite"))
    with TestClient(app) as c:
        yield c


def test_general_traits_are_last_and_review_flags_keep_source_strength(client):
    response = client.post("/api/companies", json={"name": "测试企业", "headcount_text":"10000人以上"})
    assert response.status_code == 201
    cid = response.json()["id"]
    jd = "熟悉 RAG 者优先。具备沟通协调能力。"
    r = client.post("/api/jobs", json={"company_id": cid, "title": "Agent实习生",
                                       "direction": "agent", "priority": "high", "jd_text": jd})
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    with connect() as conn:
        conn.execute("""CREATE TABLE radar_skill_cleanup_links(
            batch_id TEXT, job_id INTEGER, skill_name TEXT, category TEXT,
            necessity TEXT, evidence_json TEXT, old_skill_ids_json TEXT,
            live_check TEXT, review_status TEXT,
            PRIMARY KEY(batch_id,job_id,skill_name))""")
        for name, category, quote in (
            ("RAG", "technical", "熟悉 RAG 者优先"),
            ("沟通协调", "general", "具备沟通协调能力"),
        ):
            conn.execute("""INSERT INTO radar_skill_cleanup_links VALUES(
                ?,?,?,?,?,?,?,?,?)""", (
                "v2", jid, name, category, "must", json.dumps([quote]), "[]",
                "candidate_supported_by_current_jd", "candidate"))
    r = client.get("/api/skill-gap?scope=all&include_pending=true")
    assert r.status_code == 200, r.text
    data = r.json()["skills"]
    names = [s["skill"] for s in data]
    assert names.index("RAG") < names.index("沟通协调")
    rag = next(s for s in data if s["skill"] == "RAG")
    assert rag["must_jobs"] == 1
    assert rag["curation_flags"] == ["must_has_bonus_word"]
    soft = next(s for s in data if s["skill"] == "沟通协调")
    assert soft["actionable"] is False
    assert soft["cleanup_jobs"] == 1


def test_old_alias_mastery_is_visible_on_canonical_skill(client):
    c = client.post("/api/companies", json={"name": "测试企业二", "headcount_text":"10000人以上"})
    assert c.status_code == 201
    r = client.post("/api/jobs", json={
        "company_id": c.json()["id"], "title": "编程实习",
        "direction": "agent", "priority": "high", "jd_text": "要求熟悉编程能力"})
    assert r.status_code == 201
    with connect() as conn:
        conn.execute("INSERT INTO skill_aliases(alias, canonical) VALUES(?,?)",
                     ("编程能力", "编程基础"))
        conn.execute("""INSERT INTO personal_skill_status(skill_key,status,note)
                        VALUES('编程能力','met','已有基础')""")
        conn.execute("""CREATE TABLE radar_skill_cleanup_links(
            batch_id TEXT, job_id INTEGER, skill_name TEXT, category TEXT,
            necessity TEXT, evidence_json TEXT, old_skill_ids_json TEXT,
            live_check TEXT, review_status TEXT,
            PRIMARY KEY(batch_id,job_id,skill_name))""")
        conn.execute("INSERT INTO radar_skill_cleanup_links VALUES(?,?,?,?,?,?,?,?,?)", (
            "v2", r.json()["id"], "编程能力", "technical", "must",
            json.dumps(["要求熟悉编程能力"]), "[]", "candidate_supported_by_current_jd",
            "candidate"))
    data = client.get("/api/skill-gap?scope=all&include_pending=true").json()["skills"]
    row = next(s for s in data if s["skill"] == "编程基础")
    assert row["status"] == "met"
    assert row["note"] == "已有基础"
    r = client.put("/api/skill-gap/status", json={
        "skill": "编程能力", "status": "partial", "note": "改为部分满足"})
    assert r.status_code == 200, r.text
    assert r.json()["skill"] == "编程基础"
    with connect() as conn:
        assert conn.execute("""SELECT status FROM personal_skill_status
                               WHERE skill_key='编程基础'""").fetchone()[0] == "partial"
