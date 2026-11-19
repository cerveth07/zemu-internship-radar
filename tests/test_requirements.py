"""P1: JD evidence extraction must remain proposed until user review."""
import json

import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db, SCHEMA_VERSION
from app.main import app
from fe_helpers import frontend_source


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "requirements.sqlite"))
    with TestClient(app) as test_client:
        yield test_client


def make_job(c, name="模型应用公司", jd="需要熟悉 Python；熟悉 Java 或 Go 任意一种优先；负责 Agent 评测", eligibility="2027届及之后，至少实习四个月"):
    company = c.post("/api/companies", json={"name": name, "sector": "Agent"})
    assert company.status_code == 201, company.text
    job = c.post("/api/jobs", json={
        "company_id": company.json()["id"], "title": "AI Agent 实习", "jd_text": jd,
        "eligibility": eligibility, "source_platform": "boss",
    })
    assert job.status_code == 201, job.text
    return job.json()["id"]


def package(c, job_ids=None):
    result = c.post("/api/analysis/packages", json={
        "job_ids": job_ids or [], "save_snapshot": True,
    })
    assert result.status_code == 200, result.text
    return result.json()["package"]["package_id"]


def requirement(jid, *, name="Python", evidence="熟悉 Python", source_field="jd_text",
                requirement_type="technical_skill", strength="must", **extra):
    return {
        "job_id": jid, "requirement_type": requirement_type, "name": name,
        "strength": strength, "evidence": evidence, "source_field": source_field,
        "option_logic": "single", "alternatives": [], **extra,
    }


def import_req(c, pid, rows, dry_run=True):
    return c.post("/api/requirements/import", json={
        "package_id": pid, "requirements": rows, "dry_run": dry_run,
    })


def req_items(c, status="pending", offset=0, limit=30):
    result = c.get("/api/requirements", params={
        "status": status, "offset": offset, "limit": limit,
    })
    assert result.status_code == 200, result.text
    return result.json()


def test_schema_v12_without_changing_jd_or_existing_jobs(client):
    assert SCHEMA_VERSION >= 12
    jid = make_job(client)
    init_db()
    with connect() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        assert conn.execute("SELECT jd_text FROM jobs WHERE id=?", (jid,)).fetchone()[0].startswith("需要熟悉")
        assert conn.execute("SELECT count(*) FROM job_requirements").fetchone()[0] == 0


def test_import_preview_pending_then_confirm_skill_only_by_explicit_action(client):
    jid = make_job(client)
    pid = package(client, [jid])
    rows = [requirement(jid)]
    preview = import_req(client, pid, rows)
    assert preview.status_code == 200, preview.text
    assert preview.json()["new_count"] == 1
    assert req_items(client)["total"] == 0
    committed = import_req(client, pid, rows, False)
    assert committed.status_code == 200, committed.text
    row = req_items(client)["items"][0]
    assert row["evidence"] == "熟悉 Python"
    assert row["review_status"] == "pending"
    assert row["can_sync_skill"] is True
    assert client.get("/api/jobs/" + str(jid)).json()["skills"] == []

    confirmed = client.post(f"/api/requirements/{row['id']}/review",
                            json={"action": "confirm", "sync_skill": False})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["skill_synced"] is False
    assert req_items(client, "confirmed")["total"] == 1
    assert client.get(f"/api/jobs/{jid}").json()["skills"] == []

    reopened = client.post(f"/api/requirements/{row['id']}/review", json={"action": "reopen"})
    assert reopened.status_code == 200, reopened.text
    synced = client.post(f"/api/requirements/{row['id']}/review",
                         json={"action": "confirm", "sync_skill": True})
    assert synced.status_code == 200, synced.text
    assert synced.json()["skill_synced"] is True
    assert client.get(f"/api/jobs/{jid}").json()["skills"][0]["necessity"] == "must"
    assert client.get(f"/api/jobs/{jid}").json()["skills"][0]["evidence"] == "熟悉 Python"


def test_invalid_evidence_atomic_import_and_invalid_package_job_membership(client):
    jid = make_job(client)
    other = make_job(client, name="第二家公司")
    pid = package(client, [jid])
    rows = [requirement(jid), requirement(jid, name="Rust", evidence="完全不存在的要求")]
    invalid = import_req(client, pid, rows, False)
    assert invalid.status_code == 422, invalid.text
    assert req_items(client)["total"] == 0
    cross = import_req(client, pid, [requirement(other)], False)
    assert cross.status_code == 422, cross.text
    assert req_items(client)["total"] == 0


def test_duplicate_same_package_and_across_packages(client):
    jid = make_job(client)
    first = package(client, [jid])
    p = requirement(jid)
    assert import_req(client, first, [p,p], False).json()["new_count"] == 1
    assert import_req(client, first, [p], False).json()["new_count"] == 0
    second = package(client, [jid])
    r = import_req(client, second, [p], False)
    assert r.status_code == 200, r.text
    assert r.json()["new_count"] == 0
    assert req_items(client)["total"] == 1


def test_combo_requirements_preserved_but_never_synced_as_independent_skills(client):
    jid = make_job(client)
    pid = package(client, [jid])
    group = requirement(jid, name="Java 或 Go 任意一种",
                        evidence="Java 或 Go 任意一种",
                        option_logic="any_of", alternatives=["Java", "Go"],
                        strength="bonus")
    r = import_req(client, pid, [group], False)
    assert r.status_code == 200, r.text
    row = req_items(client)["items"][0]
    assert row["alternatives"] == ["Java", "Go"]
    assert row["can_sync_skill"] is False
    denied = client.post(f"/api/requirements/{row['id']}/review",
                         json={"action": "confirm", "sync_skill": True})
    assert denied.status_code == 422, denied.text
    assert req_items(client)["total"] == 1
    accepted = client.post(f"/api/requirements/{row['id']}/review",json={"action": "confirm"})
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"/api/jobs/{jid}").json()["skills"] == []


def test_stale_package_prevents_commit_and_stale_import_prevents_confirmation(client):
    jid = make_job(client)
    pid = package(client, [jid])
    row = requirement(jid)
    assert import_req(client, pid, [row], False).status_code == 200
    changed = client.patch(f"/api/jobs/{jid}", json={"jd_text": "新的要求：请掌握 C++"})
    assert changed.status_code == 200, changed.text
    preview = import_req(client, pid, [row], True)
    assert preview.status_code == 422 or (
        preview.status_code == 200 and jid in preview.json()["stale_job_ids"]
    )
    assert import_req(client, pid, [row], False).status_code in (409,422)
    rid = req_items(client)["items"][0]["id"]
    assert client.post(f"/api/requirements/{rid}/review",
                       json={"action": "confirm"}).status_code == 409
    assert req_items(client)["total"] == 1


def test_edit_requires_source_quote_and_pending_status(client):
    jid = make_job(client)
    pid = package(client, [jid])
    assert import_req(client,pid,[requirement(jid)],False).status_code == 200
    rid = req_items(client)["items"][0]["id"]
    revised = requirement(jid, name="模型评测", evidence="负责 Agent 评测",
                          requirement_type="research_skill",strength="mentioned")
    changed = client.put(f"/api/requirements/{rid}", json=revised)
    assert changed.status_code == 200, changed.text
    assert req_items(client)["items"][0]["name"] == "模型评测"
    wrong = {**revised, "evidence": "凭空添加的能力"}
    assert client.put(f"/api/requirements/{rid}",json=wrong).status_code == 422
    assert client.post(f"/api/requirements/{rid}/review",
                       json={"action":"confirm"}).status_code == 200
    assert client.put(f"/api/requirements/{rid}",json=revised).status_code == 409


def test_eligibility_quote_and_batch_review_atomic_and_paginated(client):
    jid = make_job(client)
    pid = package(client, [jid])
    rows = [requirement(jid, name="要求 "+str(i), evidence="熟悉 Python")
            for i in range(35)]
    rows.append(requirement(jid, name="四个月", evidence="至少实习四个月",
                            source_field="eligibility", requirement_type="internship_schedule"))
    r = import_req(client,pid,rows,False)
    assert r.status_code == 200, r.text
    assert req_items(client,limit=30)["total"] == 36
    assert len(req_items(client,offset=30,limit=30)["items"]) == 6
    ids = [r["id"] for r in req_items(client,limit=30)["items"]]
    result = client.post("/api/requirements/review-batch",
                         json={"ids": ids, "action": "confirm"})
    assert result.status_code == 200, result.text
    assert result.json()["reviewed_count"] == 30
    assert req_items(client,"confirmed")["total"] == 30
    assert req_items(client,"pending")["total"] == 6
    mixed = client.post("/api/requirements/review-batch",
                        json={"ids": [ids[0],req_items(client)["items"][0]["id"]],
                              "action": "reject"})
    assert mixed.status_code == 409, mixed.text
    assert req_items(client,"rejected")["total"] == 0


def test_rejection_reopen_and_existing_human_skill_data_not_overwritten(client):
    jid = make_job(client)
    pid = package(client, [jid])
    assert import_req(client,pid,[requirement(jid)],False).status_code == 200
    rid = req_items(client)["items"][0]["id"]
    assert client.post(f"/api/requirements/{rid}/review",
                       json={"action": "reject"}).status_code == 200
    assert req_items(client,"rejected")["total"] == 1
    assert client.post(f"/api/requirements/{rid}/review",
                       json={"action": "reopen"}).status_code == 200
    with connect() as conn:
        conn.execute("INSERT INTO skills(name,category) VALUES('Python','manual')")
        skill_id = conn.execute("SELECT id FROM skills WHERE name='Python'").fetchone()[0]
        conn.execute("INSERT INTO job_skills(job_id,skill_id,necessity,evidence) VALUES(?,?,'bonus','人工原始确认')",
                     (jid,skill_id))
    res = client.post(f"/api/requirements/{rid}/review",
                      json={"action":"confirm","sync_skill":True})
    assert res.status_code == 200, res.text
    assert res.json()["skill_already_present"] is True
    assert client.get(f"/api/jobs/{jid}").json()["skills"][0]["evidence"] == "人工原始确认"


def test_exported_json_package_can_import_its_jd_requirements(client):
    jid = make_job(client)
    response = client.post("/api/exports/jobs", json={"ids":[jid]})
    assert response.status_code == 200, response.text
    pid = response.json()["package_id"]
    result = import_req(client,pid,[requirement(jid)],False)
    assert result.status_code == 200, result.text
    assert result.json()["new_count"] == 1


def test_frontend_review_and_bulk_actions_are_wired(client):
    js = frontend_source()
    for needle in (
        "requirement-import-json", "preview-requirements",
        "commit-requirements", "requirement-status-filter",
        "data-req-review", "data-req-edit", "data-req-select",
        "requirement-batch-confirm", "requirement-batch-reject",
        "requirement-edit-form",
    ):
        assert needle in js


def test_same_quote_after_jd_revision_becomes_new_review_candidate(client):
    jid = make_job(client)
    old_id = package(client, [jid])
    original = requirement(jid)
    assert import_req(client, old_id, [original], False).json()["new_count"] == 1
    edited = client.patch(f"/api/jobs/{jid}", json={
        "jd_text": "需要熟悉 Python；熟悉 Java 或 Go 任意一种优先；负责 Agent 评测；还需掌握 Git",
    })
    assert edited.status_code == 200, edited.text
    new_id = package(client, [jid])
    newer = import_req(client, new_id, [original], False)
    assert newer.status_code == 200, newer.text
    assert newer.json()["new_count"] == 1
    assert req_items(client)["total"] == 2
    current = req_items(client)["items"][0]
    assert client.post(f"/api/requirements/{current['id']}/review",
                       json={"action": "confirm"}).status_code == 200
    old = req_items(client)["items"][0]
    assert client.post(f"/api/requirements/{old['id']}/review",
                       json={"action": "confirm"}).status_code == 409


def test_compound_alternatives_must_appear_in_original_evidence(client):
    jid = make_job(client)
    pid = package(client, [jid])
    fake = requirement(jid, name="Java 或 Rust", evidence="Java 或 Go 任意一种",
                       option_logic="any_of", alternatives=["Java", "Rust"])
    result = import_req(client, pid, [fake], False)
    assert result.status_code == 422, result.text
    assert req_items(client)["total"] == 0
