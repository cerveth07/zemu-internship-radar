"""Read-only workflow status and explicit completion of zero-candidate JD batches.

Status labels describe extracted *records*, never assert full JD coverage.
All batch identities are linked to immutable analysis_packages snapshots.
"""
from __future__ import annotations
import json
from collections import defaultdict
from fastapi import APIRouter, HTTPException
from .db import connect
from .requirements import source_hash
from .analysis import canonical

router = APIRouter(prefix="/api/workflow", tags=["Manual GPT task workflows"])


def register_jd_batch(conn, package_id, jobs):
    versions = {str(job["job_id"]): source_hash(job) for job in jobs}
    conn.execute(
        "INSERT INTO jd_extraction_batches(package_id,jd_hashes_json) VALUES(?,?)",
        (package_id, canonical(versions))
    )


def _batch_rows(conn, limit=80):
    rows = conn.execute("""
        SELECT p.id,p.created_at,p.included_ids_json,
               b.closed_no_candidates,b.imported_at,b.import_count,b.note,b.jd_hashes_json,
               COALESCE(b.last_activity_at,
                 (SELECT MAX(r.created_at) FROM job_requirements r WHERE r.package_id=p.id),
                 p.created_at) AS activity_at,
               CASE WHEN b.package_id IS NOT NULL THEN 'job_export' ELSE 'legacy_import' END AS origin
        FROM analysis_packages p
        LEFT JOIN jd_extraction_batches b ON b.package_id=p.id
        WHERE b.package_id IS NOT NULL OR EXISTS(
            SELECT 1 FROM job_requirements r WHERE r.package_id=p.id)
        ORDER BY activity_at DESC,p.rowid DESC LIMIT ?
    """, (limit,)).fetchall()
    return [dict(row) for row in rows]


@router.get("")
def workflow_overview():
    with connect() as conn:
        raw_jobs = conn.execute(
            "SELECT id,jd_text,eligibility FROM jobs ORDER BY id"
        ).fetchall()
        versions = {job["id"]: source_hash(job) for job in raw_jobs}
        status = {
            job_id: {"job_id": job_id, "confirmed": 0,
                     "pending": 0, "rejected": 0, "stale": 0}
            for job_id in versions
        }
        by_batch = defaultdict(lambda: {"total": 0, "pending": 0, "confirmed": 0,
                                        "rejected": 0, "stale": 0})
        for row in conn.execute("""SELECT job_id,package_id,review_status,source_jd_hash
                                    FROM job_requirements"""):
            data = status.get(row["job_id"])
            if data is None:
                continue
            batch = by_batch[row["package_id"]]
            batch["total"] += 1
            if versions[row["job_id"]] != row["source_jd_hash"]:
                data["stale"] += 1
                batch["stale"] += 1
            else:
                data[row["review_status"]] += 1
                batch[row["review_status"]] += 1
        # Include ALL active export batches here, not just the 80 recent rows.
        # Otherwise older pending exports reappear as "unprocessed" candidates.
        reserved_ids = set()
        for record in conn.execute("SELECT jd_hashes_json FROM jd_extraction_batches"):
            for id_text, saved_hash in json.loads(record["jd_hashes_json"]).items():
                job_id = int(id_text)
                if versions.get(job_id) == saved_hash:
                    reserved_ids.add(job_id)
        batches = []
        for item in _batch_rows(conn):
            ids = json.loads(item["included_ids_json"])
            counts = by_batch[item["id"]]
            exported_versions = json.loads(item["jd_hashes_json"] or "{}")
            changed_job_ids = [
                int(jid) for jid, original in exported_versions.items()
                if versions.get(int(jid)) != original
            ]
            if changed_job_ids:
                stage = "stale"
            elif item["closed_no_candidates"] and counts["total"] == 0:
                stage = "no_candidates"
            elif counts["pending"] or counts["stale"]:
                stage = "needs_review" if counts["pending"] else "stale"
            elif counts["total"]:
                stage = "reviewed"
            elif item["imported_at"]:
                stage = "imported_duplicates"
            else:
                stage = "awaiting_gpt"
            batches.append({
                "package_id": item["id"],
                "job_ids": ids,
                "job_count": len(ids),
                "created_at": item["created_at"],
                "activity_at": item["activity_at"],
                "origin": item["origin"],
                "stage": stage,
                "requirements_total": counts["total"],
                "pending": counts["pending"],
                "confirmed": counts["confirmed"],
                "rejected": counts["rejected"],
                "stale": counts["stale"],
                "changed_job_ids": sorted(changed_job_ids),
                "closed_no_candidates": bool(item["closed_no_candidates"]),
                "imported_at": item["imported_at"],
                "import_count": item["import_count"],
            })
        return {
            "jobs": list(status.values()), "batches": batches,
            "reserved_job_ids": sorted(reserved_ids),
            "note": "Having one confirmed requirement does not prove full JD extraction."
        }


@router.post("/batches/{package_id}/no-candidates")
def mark_no_candidates(package_id: str):
    """User explicitly reviewed GPT's *empty output*, not JD completeness."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute(
            "SELECT package_id,jd_hashes_json FROM jd_extraction_batches WHERE package_id=?",
            (package_id,)
        ).fetchone()
        if batch is None:
            raise HTTPException(404, "无法识别该 JD 导出批次")
        previous = json.loads(batch["jd_hashes_json"])
        for jid, old_hash in previous.items():
            current = conn.execute(
                "SELECT jd_text,eligibility FROM jobs WHERE id=?", (int(jid),)
            ).fetchone()
            if current is None or source_hash(current) != old_hash:
                raise HTTPException(409, "此批次 JD 已更新或岗位已删除，请重新导出后处理")
        found = conn.execute(
            "SELECT COUNT(*) FROM job_requirements WHERE package_id=?",
            (package_id,)
        ).fetchone()[0]
        if found or conn.execute(
            "SELECT imported_at FROM jd_extraction_batches WHERE package_id=?",
            (package_id,)
        ).fetchone()["imported_at"]:
            raise HTTPException(409, "该批次已导回结果或存在提取要求，请到审核记录处理")
        conn.execute("""UPDATE jd_extraction_batches
                        SET closed_no_candidates=1,last_activity_at=datetime('now'),
                            note='用户标记本批没有候选要求'
                        WHERE package_id=?""", (package_id,))
        return {"ok": True, "package_id": package_id, "stage": "no_candidates"}
