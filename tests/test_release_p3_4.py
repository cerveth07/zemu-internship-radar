"""P3.4 release guards: batch manifest contracts and safe v14 -> v15 migration."""
import hashlib
import io
import json
import sqlite3
import zipfile

import pytest

from app.db import SCHEMA_VERSION, connect, init_db
from app.exports import download, json_bytes


@pytest.mark.parametrize("kind,items,key,id_key", [
    ("jobs", [{"job_id": 2}, {"job_id": 3}], "jobs", "job_id"),
    ("career", [{"job_id": 2}, {"job_id": 3}], "jobs", "job_id"),
    ("companies", [{"company_id": 2}, {"company_id": 3}], "companies", "company_id"),
])
def test_all_multi_batch_manifests_have_correct_identifiers(kind, items, key, id_key):
    """The career kind must never fall into the company_id branch."""
    batches = []
    for ix, row in enumerate(items, 1):
        data = json_bytes({key: [row], "package_id": f"package-{ix}"})
        batches.append((f"{kind}-{ix:02d}of02.json", data, f"package-{ix}"))
    response = download(kind, "12345678-1234-1234-1234-1234567890ab",
                        batches, {"direction": "agent"}, count=2)
    assert response.media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.body)) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["kind"] == kind
        assert manifest["total_items"] == 2
        assert manifest["filters"] == {"direction": "agent"}
        assert [entry["count"] for entry in manifest["batches"]] == [1, 1]
        assert [entry["ids"] for entry in manifest["batches"]] == [[2], [3]]
        for entry in manifest["batches"]:
            payload = z.read(entry["filename"])
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
            assert json.loads(payload)[key][0][id_key] in (2, 3)


def test_schema15_upgrades_v14_findings_and_career_snapshot_without_data_loss(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-v14.sqlite"
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
        PRAGMA user_version = 14;
        CREATE TABLE analysis_findings (
          id INTEGER PRIMARY KEY, package_id TEXT NOT NULL, kind TEXT NOT NULL,
          title TEXT NOT NULL, content TEXT NOT NULL,
          related_skill TEXT NOT NULL DEFAULT '',
          priority TEXT NOT NULL DEFAULT 'medium', job_ids_json TEXT NOT NULL,
          fingerprint TEXT NOT NULL UNIQUE,
          adopted_task_id INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE career_export_snapshots (
          package_id TEXT PRIMARY KEY, export_id TEXT NOT NULL,
          mode TEXT NOT NULL, selection_json TEXT NOT NULL,
          requirement_hashes_json TEXT NOT NULL, evidence_hashes_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO analysis_findings
        (id,package_id,kind,title,content,job_ids_json,fingerprint)
        VALUES(7,'legacy-pkg','learning_task','旧分析结论','原始内容','[1]','legacy-fingerprint');
        INSERT INTO career_export_snapshots
        (package_id,export_id,mode,selection_json,requirement_hashes_json,evidence_hashes_json)
        VALUES('legacy-pkg','export-old','compact','{}','{}','{}');
        """)
    init_db()
    init_db()  # migration must be idempotent
    with connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        f = conn.execute("SELECT * FROM analysis_findings WHERE id=7").fetchone()
        assert f["content"] == "原始内容"
        assert f["fingerprint"] == "legacy-fingerprint"
        assert json.loads(f["requirement_ids_json"]) == []
        assert json.loads(f["personal_evidence_ids_json"]) == []
        assert json.loads(f["career_stale_ack_json"]) == {}
        p = conn.execute("SELECT * FROM career_export_snapshots WHERE package_id='legacy-pkg'").fetchone()
        assert p["mode"] == "compact"
        assert json.loads(p["included_requirement_ids_json"]) == []
