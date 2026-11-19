"""P3.1/P3.2: manual career handoff preserves coverage, privacy and history."""
import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.db import SCHEMA_VERSION, connect, init_db
from app.main import app
from fe_helpers import frontend_source


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "career.sqlite"))
    with TestClient(app) as c:
        yield c


def add_company(client, name="甲公司", headcount_text="10000人以上"):
    response = client.post("/api/companies", json={
        "name": name, "sector": "Agent 工具", "headcount_text": headcount_text,
        "notes": "PRIVATE COMPANY NOTES MUST NOT EXPORT",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_job(client, company_id, title="Agent 实习", jd="JD：精通 Python 和 Agent",
            priority="high"):
    response = client.post("/api/jobs", json={
        "company_id": company_id, "title": title, "jd_text": jd,
        "eligibility": "至少实习四个月", "direction": "agent", "priority": priority,
        "city": "北京", "notes": "PRIVATE JOB NOTES MUST NOT EXPORT",
        "source_platform": "manual", "source_url": "https://example.com/jd",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_personal(client, skill, description, review_status="documented"):
    response = client.post("/api/evidence", json={
        "skill": skill, "description": description, "evidence_type": "internship",
        "proficiency": "working", "review_status": review_status,
        "source_url": "https://example.com/portfolio",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_confirmed(client, job_id, name="Python", evidence="精通 Python"):
    original = client.post("/api/analysis/packages", json={
        "job_ids": [job_id],
    })
    assert original.status_code == 200, original.text
    package_id = original.json()["package"]["package_id"]
    data = {"package_id": package_id, "dry_run": False, "requirements": [{
        "job_id": job_id, "requirement_type": "technical_skill", "name": name,
        "strength": "must", "evidence": evidence, "source_field": "jd_text",
        "option_logic": "single", "alternatives": [],
    }]}
    imported = client.post("/api/requirements/import", json=data)
    assert imported.status_code == 200, imported.text
    pending = client.get("/api/requirements?status=pending").json()["items"]
    target = next(x for x in pending if x["job_id"] == job_id)
    verified = client.post(f"/api/requirements/{target['id']}/review",
                           json={"action": "confirm"})
    assert verified.status_code == 200, verified.text


def career(client, job_ids, mode="compact", evidence_ids=None, **extra):
    return client.post("/api/career-analysis/export", json={
        "job_ids": job_ids, "mode": mode, "evidence_ids": evidence_ids or [],
        "selection_filters": {"headcount_min": "10000", "priority": "high"},
        "goal": "gap", **extra,
    })


def decode_package(response):
    assert response.status_code == 200, response.text[:1000]
    assert response.headers["x-export-count"].isdigit()
    if response.headers["content-type"].startswith("application/json"):
        return [response.json()], None
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        packages = []
        for entry in manifest["batches"]:
            data = archive.read(entry["filename"])
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            packages.append(json.loads(data))
    return packages, manifest


def test_compact_keeps_all_jobs_and_marks_missing_structured_requirements(client):
    cid = add_company(client)
    first = add_job(client, cid, title="已提取岗位")
    second = add_job(client, cid, title="未提取岗位", jd="独特的 JD 私密文本标记")
    make_confirmed(client, first)
    response = career(client, [second, first])
    packages, manifest = decode_package(response)
    assert manifest is None
    payload = packages[0]
    assert payload["schema"] == "ai-internship-radar-career-export-v1"
    assert payload["coverage"]["selection_total_jobs"] == 2
    assert payload["coverage"]["selection_confirmed_covered_jobs"] == 1
    assert payload["coverage"]["selection_uncovered_jobs"] == 1
    assert payload["coverage"]["batch_uncovered_job_ids"] == [second]
    assert [x["job_id"] for x in payload["jobs"]] == [first, second]
    assert payload["jobs"][0]["confirmed_requirements"][0]["name"] == "Python"
    assert payload["jobs"][1]["confirmed_requirements"] == []
    assert payload["jobs"][1]["requirements_coverage"] == "no_confirmed_requirements"
    assert payload["demand_statistics"]["selected_jobs"] == 2
    assert payload["demand_statistics"]["confirmed_covered_jobs"] == 1
    assert payload["demand_statistics"]["skills"][0]["skill"] == "Python"
    assert "jd_text" not in payload["jobs"][0]
    assert "eligibility" not in payload["jobs"][0]
    assert "独特的 JD 私密文本标记" not in response.text
    assert "PRIVATE" not in response.text
    assert payload["personal_evidence"] == []


def test_full_mode_preserves_complete_jd_and_exports_only_opted_in_evidence(client):
    cid = add_company(client)
    full_jd = "完整原文开始：Python 和 Agent" + "远程开发，" * 500 + "原文结束"
    jid = add_job(client, cid, jd=full_jd)
    selected = add_personal(client, "AI 产品", "AUTHORIZED SELECTED EXPERIENCE")
    unselected = add_personal(client, "RAG", "NOT SELECTED PRIVATE MATERIAL")
    response = career(client, [jid], mode="full", evidence_ids=[selected])
    batches, _ = decode_package(response)
    payload = batches[0]
    assert payload["jobs"][0]["jd_text"] == full_jd
    assert payload["jobs"][0]["eligibility"] == "至少实习四个月"
    assert payload["privacy"]["selected_personal_evidence_ids"] == [selected]
    assert payload["personal_evidence"][0]["description"] == "AUTHORIZED SELECTED EXPERIENCE"
    assert payload["personal_evidence"][0]["source_url"] == "https://example.com/portfolio"
    assert "NOT SELECTED PRIVATE MATERIAL" not in response.text
    assert "PRIVATE JOB NOTES" not in response.text
    assert "PRIVATE COMPANY NOTES" not in response.text
    assert "recruiter" not in payload["jobs"][0]
    assert "application" not in payload["jobs"][0]


def test_compact_mode_contains_no_full_jd_even_when_selected(client):
    cid = add_company(client)
    jid = add_job(client, cid, jd="VERY LONG SECRET JD TEXT that must be excluded in compact mode")
    personal = add_personal(client, "Python", "MY ABILITY NOTE")
    r = career(client, [jid], evidence_ids=[personal])
    p, _ = decode_package(r)
    assert "jd_text" not in p[0]["jobs"][0]
    assert "VERY LONG SECRET JD TEXT" not in r.text
    assert "MY ABILITY NOTE" in r.text


def test_selected_ids_and_personal_ids_validated_as_all_or_nothing(client):
    cid = add_company(client)
    one = add_job(client, cid)
    for ids in ([], [one, one], [0], [one, 9999999], [True]):
        r = career(client, ids)
        assert r.status_code == 422, (ids, r.text)
    r = career(client, [one], evidence_ids=[9999999])
    assert r.status_code == 422, r.text
    r = career(client, [one], evidence_ids=[5, 5])
    assert r.status_code == 422
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM career_export_snapshots").fetchone()[0] == 0


def test_many_jobs_zip_is_complete_and_uses_one_export_id(client):
    cid = add_company(client)
    originals = {}
    for i in range(55):
        jd = "职位要求" + str(i) + " 专属独立正文 " + "Agent 开发" * 200
        jid = add_job(client, cid, title=f"Agent 实习 #{i}", jd=jd)
        originals[jid] = jd
    response = career(client, list(reversed(list(originals))), mode="full")
    packs, manifest = decode_package(response)
    assert manifest is not None
    assert manifest["kind"] == "career"
    assert manifest["total_items"] == len(originals)
    assert len(packs) >= 2
    manifest_ids = [jid for entry in manifest['batches'] for jid in entry['ids']]
    assert manifest_ids == sorted(originals)
    assert len(manifest_ids) == len(set(manifest_ids))
    for entry, payload in zip(manifest['batches'], packs):
        assert entry['ids'] == [job['job_id'] for job in payload['jobs']]
        assert entry['count'] == len(payload['jobs'])
    values = [job for payload in packs for job in payload["jobs"]]
    assert {x["job_id"]: x["jd_text"] for x in values} == originals
    assert len({p["package_id"] for p in packs}) == len(packs)
    assert all(p["export_metadata"]["export_id"] == manifest["export_id"] for p in packs)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM career_export_snapshots").fetchone()[0] == len(packs)


def test_career_package_retains_existing_analysis_findings_import(client):
    cid = add_company(client)
    jid = add_job(client, cid)
    res = career(client, [jid])
    p = res.json()
    imported = client.post("/api/analysis/import", json={
        "package_id": p["package_id"], "dry_run": True,
        "findings": [{
            "kind": "learning_task", "title": "补强 Agent 实践",
            "content": "根据岗位要求练习", "job_ids": [jid],
        }],
    })
    assert imported.status_code == 200, imported.text
    assert imported.json()["stale_job_ids"] == []


def test_snapshot_hashes_track_requirements_and_selected_personal_data(client):
    cid = add_company(client)
    jid = add_job(client, cid)
    personal = add_personal(client, "项目", "只保存散列而非文本")
    make_confirmed(client, jid)
    p = career(client, [jid], evidence_ids=[personal]).json()
    with connect() as conn:
        snapshot = conn.execute(
            "SELECT * FROM career_export_snapshots WHERE package_id=?", (p["package_id"],)
        ).fetchone()
        assert snapshot is not None
        assert snapshot["mode"] == "compact"
        assert str(jid) in json.loads(snapshot["requirement_hashes_json"])
        assert str(personal) in json.loads(snapshot["evidence_hashes_json"])
        assert "只保存散列而非文本" not in snapshot["evidence_hashes_json"]


def test_oversized_full_jd_rejected_without_any_career_snapshot(client):
    cid = add_company(client)
    jid = add_job(client, cid, jd="长" * 1_200_000)
    response = career(client, [jid], mode="full")
    assert response.status_code == 413, response.text[:500]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM career_export_snapshots").fetchone()[0] == 0


def test_schema14_idempotent_preserves_existing_requirements(client):
    cid = add_company(client)
    jid = add_job(client, cid)
    make_confirmed(client, jid)
    assert SCHEMA_VERSION >= 14
    init_db()
    with connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM job_requirements").fetchone()[0] == 1
        assert conn.execute("SELECT jd_text FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "JD：精通 Python 和 Agent"


def test_career_ui_has_controls_but_no_auto_model_calls(client):
    js = frontend_source()
    for text in ("career-export-panel", "career-export-mode", "career-export-goal",
                 "data-career-evidence", "export-career-json", "careerEvidenceIds",
                 "'/api/career-analysis/export'"):
        assert text in js
