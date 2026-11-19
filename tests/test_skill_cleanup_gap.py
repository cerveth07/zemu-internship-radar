"""Staged skill-cleanup V1 candidates stay a separate, lower-ranked origin.

They must never be presented as human-confirmed JD claims, must respect the live
check marker, and must lose to a confirmed claim for the same (job, skill) pair.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "skill-cleanup.sqlite"))
    with TestClient(app) as c:
        yield c


def company(c, name="示例科技"):
    r = c.post("/api/companies", json={"name": name, "headcount_text":"10000人以上"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def job(c, cid, jd, priority="high"):
    r = c.post("/api/jobs", json={"company_id": cid, "title": "AI 实习岗位",
                                  "priority": priority, "direction": "agent",
                                  "jd_text": jd})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def confirm_requirement(c, jid, name, quote):
    package = c.post("/api/analysis/packages", json={"job_ids": [jid]})
    assert package.status_code == 200, package.text
    pid = package.json()["package"]["package_id"]
    imported = c.post("/api/requirements/import", json={
        "package_id": pid, "dry_run": False,
        "requirements": [{"job_id": jid, "requirement_type": "technical_skill",
                          "name": name, "strength": "must", "evidence": quote,
                          "source_field": "jd_text", "option_logic": "single",
                          "alternatives": []}]})
    assert imported.status_code == 200, imported.text
    listed = c.get("/api/requirements?status=pending&limit=50").json()["items"]
    target = next(item for item in listed if item["job_id"] == jid)
    reviewed = c.post(f"/api/requirements/{target['id']}/review",
                      json={"action": "confirm"})
    assert reviewed.status_code == 200, reviewed.text


def stage(rows, live_check="candidate_supported_by_current_jd"):
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS radar_skill_cleanup_links(
            batch_id TEXT NOT NULL, job_id INTEGER NOT NULL, skill_name TEXT NOT NULL,
            category TEXT NOT NULL, necessity TEXT NOT NULL, evidence_json TEXT NOT NULL,
            old_skill_ids_json TEXT NOT NULL, live_check TEXT NOT NULL,
            review_status TEXT NOT NULL, PRIMARY KEY(batch_id, job_id, skill_name))""")
        for job_id, skill, necessity in rows:
            conn.execute("INSERT OR REPLACE INTO radar_skill_cleanup_links "
                         "VALUES(?,?,?,?,?,?,?,?,?)",
                         ("batch-v1", job_id, skill, "technical", necessity,
                          json.dumps(["熟悉 " + skill + " 者优先"]), "[]",
                          live_check, "candidate"))


def gap(client, scope="all"):
    r = client.get(f"/api/skill-gap?scope={scope}&include_pending=true")
    assert r.status_code == 200, r.text
    return r.json()


def row_for(data, skill):
    return next((s for s in data["skills"] if s["skill"] == skill), None)


def test_staged_candidate_is_its_own_origin(client):
    cid = company(client)
    jid = job(client, cid, "熟悉 RAG 者优先。熟悉 Axure 者优先。")
    stage([(jid, "RAG", "must")])
    data = gap(client)
    row = row_for(data, "RAG")
    assert row is not None, "清洗候选技能应出现在能力差距列表"
    assert row["cleanup_jobs"] == 1
    assert row["confirmed_jobs"] == 0 and row["gpt_jobs"] == 0
    assert row["candidate_jobs"] == 0
    assert row["must_jobs"] == 1 and row["bonus_jobs"] == 0
    assert row["examples"][0]["origin"] == "cleanup_candidate"
    assert "熟悉 RAG 者优先" in row["examples"][0]["text"]
    assert data["cleanup_covered_jobs"] == 1


def test_held_or_stale_live_check_rows_are_ignored(client):
    cid = company(client)
    jid = job(client, cid, "熟悉 RAG 者优先。熟悉 Axure 者优先。")
    stage([(jid, "RAG", "must")], live_check="held_pending_review")
    data = gap(client)
    assert row_for(data, "RAG") is None
    assert data["cleanup_covered_jobs"] == 0


def test_confirmed_claim_wins_over_staged_candidate(client):
    quote = "熟悉 Python 者优先"
    cid = company(client)
    jid = job(client, cid, "岗位要求：" + quote + "。")
    confirm_requirement(client, jid, "Python", quote)
    stage([(jid, "Python", "must")])
    data = gap(client)
    row = row_for(data, "Python")
    assert row["job_count"] == 1, "同岗位同技能必须只计一次"
    assert row["confirmed_jobs"] == 1
    assert row["cleanup_jobs"] == 0


def test_missing_shadow_table_is_not_an_error(client):
    cid = company(client)
    job(client, cid, "负责数据分析工作。")
    data = gap(client)
    assert data["cleanup_covered_jobs"] == 0
    assert data["skills"] == []


def test_bonus_strength_is_kept(client):
    cid = company(client)
    jid = job(client, cid, "熟悉 RAG 者优先。熟悉 Axure 者优先。")
    stage([(jid, "Axure", "bonus")])
    row = row_for(gap(client), "Axure")
    assert row["bonus_jobs"] == 1 and row["must_jobs"] == 0


def test_candidate_toggle_controls_list_and_evidence(client):
    jid = job(client, company(client), "熟悉 RAG 者优先")
    stage([(jid, "RAG", "bonus")])
    assert row_for(gap(client), "RAG") is not None
    assert client.get('/api/skill-gap?scope=all').json()['skills'] == []
    route = '/api/skill-gap/evidence?scope=all&skill=RAG'
    assert client.get(route).json()['total'] == 0
    assert client.get(route + '&include_pending=true').json()['total'] == 1


def test_changed_jd_invalidates_import_time_live_marker(client):
    jid = job(client, company(client), "熟悉 RAG 者优先")
    stage([(jid, "RAG", "bonus")])
    assert row_for(gap(client), "RAG") is not None
    changed = client.patch(f'/api/jobs/{jid}', json={'jd_text': '只要求 Excel'})
    assert changed.status_code == 200
    assert row_for(gap(client), "RAG") is None
    assert client.get('/api/skill-gap/evidence?scope=all&skill=RAG&include_pending=true').json()['total'] == 0


@pytest.mark.parametrize('payload', ['{', '{}', '"熟悉 RAG 者优先"', '[]', '[null]', '[42]', '[""]'])
def test_malformed_evidence_is_not_displayed_or_fabricated(client, payload):
    jid = job(client, company(client), "熟悉 RAG 者优先")
    stage([(jid, "RAG", "bonus")])
    with connect() as conn:
        conn.execute('UPDATE radar_skill_cleanup_links SET evidence_json=?', (payload,))
    assert row_for(gap(client), "RAG") is None


def test_rejected_and_invalid_strength_candidates_are_excluded(client):
    jid = job(client, company(client), "熟悉 RAG 者优先。熟悉 Axure 者优先")
    stage([(jid, "RAG", "must"), (jid, "Axure", "invalid")])
    with connect() as conn:
        conn.execute("UPDATE radar_skill_cleanup_links SET review_status='rejected' WHERE skill_name='RAG'")
    assert gap(client)['skills'] == []
