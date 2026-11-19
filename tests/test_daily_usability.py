"""Regression checks for daily-use workflow changes in PR #15."""
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from fe_helpers import frontend_source


ROOT = Path(__file__).resolve().parents[1] / "static"


def make_zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_zip_expansion_accepts_multiple_json_and_reports_bad_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "zip.sqlite"))
    archive = make_zip({
        "manifest.json": json.dumps({"schema": "manifest"}),
        "results/one.json": json.dumps({"package_id": "first", "requirements": []}),
        "results/two.json": json.dumps({"batch_id": "second", "decisions": []}),
        "results/broken.json": "{not valid",
        "notes.md": "not a result",
    })
    with TestClient(app) as client:
        result = client.post("/api/gpt-import/expand-zip", content=archive,
                             headers={"Content-Type": "application/zip"})
        assert result.status_code == 200, result.text
        items = result.json()["entries"]
        assert len(items) == 3
        assert items[0]["name"] == "results/one.json"
        assert items[0]["data"]["package_id"] == "first"
        assert items[1]["data"]["batch_id"] == "second"
        assert "error" in items[2]
        assert client.post("/api/gpt-import/expand-zip", content=b"garbage").status_code == 422


def test_zip_expansion_rejects_oversized_inflated_json(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "zip-large.sqlite"))
    archive = make_zip({"oversized.json": "a" * (8 * 1024 * 1024 + 1)})
    with TestClient(app) as client:
        result = client.post("/api/gpt-import/expand-zip", content=archive,
                             headers={"Content-Type": "application/zip"})
        assert result.status_code == 413


def test_selected_jobs_feed_analysis_and_export_without_extra_filtering():
    jobs = (ROOT / "job-bulk.js").read_text(encoding="utf-8")
    analysis = (ROOT / "analysis-workflows.js").read_text(encoding="utf-8")
    app_js = frontend_source()
    assert "data-job-export-chosen" in jobs and "data-job-analyze-chosen" in jobs
    assert "ids:rows.map(j=>j.id)" in jobs
    assert "jobAnalysisIds=rows.map(j=>j.id)" in jobs
    assert "const jobs=analysisJobs()" in analysis
    assert "const selected=analysisJobs()" in app_js
    assert "const rows=analysisJobs()" in app_js
    assert "data-clear-analysis-selection" in app_js


def test_read_only_job_and_continuous_jd_review():
    js = frontend_source()
    assert 'id="job-read-view"' in js
    assert 'id="job-edit-area" hidden' in js
    assert "data-job-reading-to" in js
    assert "data-job-reading-edit" in js
    assert "showJobEdit()" in js
    assert "data-req-seq=\"confirm\"" in js
    assert "data-req-seq=\"reject\"" in js
    assert "data-req-seq=\"skip\"" in js
    assert "function openRequirementSequence(" in js
    assert "function applyRequirementSequence(" in js
    assert "await changeRequirementPage('pending',state.requirementFilters.offset)" in js


def test_bulk_filter_is_synchronized_and_common_writes_reload_only_related_data():
    app_js = frontend_source()
    capture = (ROOT / "capture.js").read_text(encoding="utf-8")
    jobs = (ROOT / "job-bulk.js").read_text(encoding="utf-8")
    gpt = (ROOT / "gpt-import.js").read_text(encoding="utf-8")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "async function refreshAfterWrite(message,resources=null)" in app_js
    assert "['jobs','summary']" in jobs
    assert "['jobs','summary','reviews','quality']" in capture
    assert "captureSelectedForBulk.clear();updateCaptureResults()" in capture
    assert "multiple hidden" in html
    assert "async function importGPTFiles(files)" in gpt
    assert "await api('/gpt-import/expand-zip'" in gpt
    assert "try{await load()}" in gpt
