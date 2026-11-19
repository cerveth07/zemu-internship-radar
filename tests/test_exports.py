"""P0 export contract: current ID selection, complete evidence and round-trip IDs."""
import hashlib
import io
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect
from fe_helpers import frontend_source


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "export.sqlite"))
    with TestClient(app) as c:
        yield c


def company(client, name="样本公司", headcount_text="10000人以上", notes="PRIVATE COMPANY NOTE"):
    r = client.post("/api/companies", json={
        "name": name, "sector": "Agent 平台", "headcount_text": headcount_text,
        "city": "北京", "website": "https://example.com", "notes": notes,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def job(client, cid, *, title="AI 产品实习生", jd="完整 JD：Python / Agent", priority="high"):
    r = client.post("/api/jobs", json={
        "company_id": cid, "title": title, "direction": "agent", "city": "北京",
        "jd_text": jd, "priority": priority, "notes": "PRIVATE JOB NOTE",
        "eligibility": "硕士在读", "source_url": "https://example.com/jd",
        "source_platform": "company",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def response_file(response, kind):
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    assert response.headers["x-export-count"].isdigit()
    if response.headers["content-type"].startswith("application/zip"):
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            manifest = json.loads(z.read("manifest.json"))
            assert manifest["kind"] == kind
            batches = []
            for item in manifest["batches"]:
                data = z.read(item["filename"])
                assert hashlib.sha256(data).hexdigest() == item["sha256"]
                batches.append(json.loads(data))
            return batches, manifest
    assert response.headers["content-type"].startswith("application/json")
    return [response.json()], None


def test_job_export_is_complete_private_free_and_importable(client):
    cid = company(client, headcount_text="100-499 人")
    jid = job(client, cid, jd="负责：Python；要求：具备 Agent 开发实践。")
    r = client.post("/api/exports/jobs", json={
        "ids": [jid], "filters": {"q": "Python", "headcount_max": "99"},
    })
    batches, manifest = response_file(r, "jobs")
    assert manifest is None
    data = batches[0]
    assert data["schema"] == "ai-internship-radar-analysis-v1"
    assert data["export_metadata"]["selection_filters"]["headcount_max"] == "99"
    assert data["export_metadata"]["selection_total"] == 1
    assert data["jobs"][0]["job_id"] == jid
    assert data["jobs"][0]["jd_text"] == "负责：Python；要求：具备 Agent 开发实践。"
    assert data["jobs"][0]["company_profile"]["headcount_text"] == "100-499 人"
    assert "size" not in data["jobs"][0]["company_profile"]
    assert "private_notes" not in data["jobs"][0]
    assert "application" not in data["jobs"][0]
    assert "PRIVATE" not in r.text
    package_id = data["package_id"]
    preview = client.post("/api/analysis/import", json={
        "package_id": package_id, "dry_run": True,
        "findings": [{"kind": "skill_gap", "title": "Python",
                      "content": "JD 要求 Python", "job_ids": [jid]}],
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()["stale_job_ids"] == []


def test_export_explicit_selection_and_invalid_ids_are_not_silently_dropped(client):
    cid = company(client)
    first = job(client, cid, title="第一岗")
    second = job(client, cid, title="第二岗")
    one = client.post("/api/exports/jobs", json={"ids": [second]})
    batches, _ = response_file(one, "jobs")
    assert [x["job_id"] for x in batches[0]["jobs"]] == [second]
    for ids in ([], [first, first], [first, 999999], [0]):
        r = client.post("/api/exports/jobs", json={"ids": ids})
        assert r.status_code == 422, (ids, r.text)
    assert client.get("/api/jobs").json()[0]["id"] in {first, second}


def test_multi_batch_zip_has_manifest_and_no_lost_or_reordered_jds(client):
    cid = company(client)
    ids = []
    original = {}
    for i in range(57):
        jd = "正文逐字保留 " + str(i) + "  " + ("长文本，" * 160)
        jid = job(client, cid, title="Agent 实习 " + str(i), jd=jd)
        ids.append(jid)
        original[jid] = jd
    r = client.post("/api/exports/jobs", json={"ids": list(reversed(ids))})
    batches, manifest = response_file(r, "jobs")
    assert manifest is not None
    assert manifest["total_items"] == 57
    assert len(batches) >= 2
    exported = [j for b in batches for j in b["jobs"]]
    assert [j["job_id"] for j in exported] == sorted(ids)
    assert len({j["job_id"] for j in exported}) == 57
    assert all(j["jd_text"] == original[j["job_id"]] for j in exported)
    assert len({b["package_id"] for b in batches}) == len(batches)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_packages").fetchone()[0] == len(batches)


def test_company_export_is_slim_and_uses_explicit_selection(client):
    c1 = company(client, name="公司一", headcount_text="20-99人")
    c2 = company(client, name="公司二", headcount_text="10000人以上")
    j1 = job(client, c1, jd="PRIVATE JD BODY", priority="high")
    job(client, c2)
    obs = client.post(f"/api/companies/{c1}/observations", json={
        "event_type": "product", "observed_on": "2026-10-10",
        "description": "发布 Agent 平台", "source_url": "https://example.com/news",
    })
    assert obs.status_code == 201, obs.text
    r = client.post("/api/exports/companies", json={
        "ids": [c1], "filters": {"q": "公司一", "headcount_max": "99"},
    })
    batches, _ = response_file(r, "companies")
    entry = batches[0]["companies"][0]
    assert entry["company_id"] == c1
    assert entry["headcount_text"] == "20-99人"
    assert entry["job_count"] == entry["high_priority_job_count"] == 1
    assert entry["company_observations"][0]["description"] == "发布 Agent 平台"
    assert entry["tracking_status"] == "watching"
    assert "status" not in entry
    assert entry["jobs"][0]["job_id"] == j1
    assert "jd_text" not in entry["jobs"][0]
    assert "notes" not in entry
    assert "PRIVATE" not in r.text
    assert batches[0]["export_metadata"]["selection_total"] == 1


def test_frontend_buttons_share_exact_filter_functions(client):
    js = frontend_source()
    assert "function selectedJobs()" in js and "const jobs=selectedJobs();" in js
    assert "function selectedCompanies()" in js and "cs=selectedCompanies()" in js
    assert "export-jobs-json" in js and "export-companies-json" in js
    assert "rows.map(x=>x.id)" in js
    assert "exportSelected('jobs')" in js and "exportSelected('companies')" in js


def test_too_large_single_jd_is_rejected_without_partial_snapshot(client):
    cid = company(client)
    jid = job(client, cid, title="超长文本岗位", jd="长" * 1_600_000)
    response = client.post("/api/exports/jobs", json={"ids": [jid]})
    assert response.status_code == 413, response.text[:500]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_packages").fetchone()[0] == 0
