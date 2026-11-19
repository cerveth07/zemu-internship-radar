"""Manual handoff of selected job requirements, demand statistics and opt-in personal evidence.

No model calls, automated candidate scoring or mutation of job/evidence records.
Every exported job receives a normal analysis package ID for legacy findings import.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .analysis import PackageRequest, canonical, digest
from .db import connect
from .exports import (
    MAX_BATCH_BYTES, MAX_SELECTION, download, json_bytes, read_jobs, take_chunks,
)
from .requirement_stats import StatsFilters, build_stats, current_requirements, selected_jobs

router = APIRouter(prefix="/api/career-analysis", tags=["Manual career analysis handoff"])
CAREER_SCHEMA = "ai-internship-radar-career-export-v1"


class CareerExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_ids: list[StrictInt] = Field(min_length=1, max_length=MAX_SELECTION)
    mode: Literal["compact", "full"] = "compact"
    evidence_ids: list[StrictInt] = Field(default_factory=list, max_length=100)
    selection_filters: dict[str, str] = Field(default_factory=dict, max_length=12)
    goal: Literal["gap", "direction", "preparation"] = "gap"
    include_ai_reviewed: bool = False

    @field_validator("job_ids", "evidence_ids")
    @classmethod
    def check_ids(cls, ids):
        if any(value <= 0 for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("ID 必须为唯一正整数")
        return sorted(ids)

    @field_validator("selection_filters")
    @classmethod
    def check_filters(cls, filters):
        if any(len(key) > 40 or len(value) > 300 for key, value in filters.items()):
            raise ValueError("筛选条件过长")
        return {key: value for key, value in filters.items() if key not in {'tier', 'company_tier', 'size_band', 'tier_reason'}}


def read_evidence(conn, ids):
    if not ids:
        return []
    rows = []
    for begin in range(0, len(ids), 400):
        batch = ids[begin:begin + 400]
        rows.extend(dict(row) for row in conn.execute(
            "SELECT id,skill,proficiency,evidence_type,description,source_url,"
            "review_status,created_at,skills_json FROM profile_evidence WHERE id IN ("
            + ",".join("?" for _ in batch) + ") ORDER BY id", batch))
    if {row["id"] for row in rows} != set(ids):
        raise HTTPException(422, "选择的我的能力与经历已被删除，请重新选择")
    rows = [project_evidence(row) for row in rows]
    return sorted(rows, key=lambda row: row["id"])


def project_evidence(row):
    item = dict(row)
    skills = json.loads(item.pop("skills_json", "[]"))
    if len(skills) > 1:
        item["skills"] = skills
    return item


def projected_requirements(requirements):
    return [
        {"requirement_id": item["id"], "package_id": item["package_id"],
         "requirement_type": item["requirement_type"], "name": item["name"],
         "strength": item["strength"], "source_field": item["source_field"],
         "evidence": item["evidence"], "option_logic": item["option_logic"],
         "alternatives": json.loads(item["alternatives_json"])}
        for item in requirements
    ]


def projected_ai_requirements(rows):
    return [{**item, "review_origin":"gpt_assisted_not_human_confirmed"}
            for item in projected_requirements(rows)]


def slim_job(job, requirements, mode, ai_requirements=()):
    profile = job["company_profile"]
    row = {
        "job_id": job["job_id"], "company_id": job["company_id"],
        "company": job["company"],
        "company_scale": {key: profile.get(key) for key in ("headcount_min", "headcount_max", "headcount_text", "headcount_source_url", "headcount_observed_on")},
        "title": job["title"], "direction": job["direction"],
        "company_sector": profile.get("sector"), "company_website": profile.get("website"),
        "salary": job["salary"],
        "status": job["status"], "priority": job["priority"],
        "city": job["city"], "duration": job["duration"],
        "onsite_days": job["onsite_days"], "team": job["team"],
        "posted_at": job["posted_at"], "deadline": job["deadline"],
        "sources": job["sources"],
        "requirements_coverage": (
            "has_confirmed_requirements" if requirements else "no_confirmed_requirements"
        ),
        "confirmed_requirements": projected_requirements(requirements),
        "gpt_reviewed_requirements": projected_ai_requirements(ai_requirements),
    }
    if mode == "full":
        row["jd_text"] = job["jd_text"]
        row["eligibility"] = job["eligibility"]
    return row


def stats_context(stats):
    """Keep denominators, taxonomy and cohort comparisons; no JD or personal notes."""
    return {
        "schema": stats["schema"],
        "definitions": stats["definitions"],
        "selected_jobs": stats["selected_jobs"],
        "selected_companies": stats["selected_companies"],
        "confirmed_covered_jobs": stats["confirmed_covered_jobs"],
        "confirmed_requirements": stats["confirmed_requirements"],
        "stale_excluded_requirements": stats["stale_excluded_requirements"],
        "stale_affected_jobs": stats["stale_affected_jobs"],
        "alias_revision": stats["alias_revision"],
        "alias_mappings": stats["alias_mappings"],
        "skills": stats["skills"],
        "gpt_reviewed_skills": stats["ai_skills"],
        "gpt_reviewed_requirements": stats["ai_reviewed_requirements"],
        "gpt_reviewed_covered_jobs": stats["ai_reviewed_covered_jobs"],
        "comparisons": stats["comparisons"],
        "composite_requirements": [
            item for item in stats["composite_requirements"] if item["status"] == "confirmed"
        ],
    }


def build_payload(
    *, export_id, package_id, index, total_batches, total_jobs,
    chunk, evidence, stats, request, covered_ids, generated_at,
):
    batch_ids = [job["job_id"] for job in chunk]
    unreviewed = [job["job_id"] for job in chunk
                  if job["requirements_coverage"] == "no_confirmed_requirements"]
    return {
        "schema": CAREER_SCHEMA,
        "package_id": package_id,
        "export_metadata": {
            "export_id": export_id,
            "batch": index, "batch_count": total_batches,
            "generated_at_utc": generated_at,
            "selection_total": total_jobs,
            "selection_filters": request.selection_filters,
            "analysis_goal": request.goal,
            "mode": request.mode,
            "include_ai_reviewed": request.include_ai_reviewed,
            "source": "local-job-library",
        },
        "coverage": {
            "selection_total_jobs": total_jobs,
            "selection_confirmed_covered_jobs": len(covered_ids),
            "selection_uncovered_jobs": total_jobs - len(covered_ids),
            "batch_jobs": len(chunk),
            "batch_uncovered_job_ids": unreviewed,
            "warning": (
                "No confirmed requirements means not yet verified, NOT absence of employer requirements. "
                "Even covered jobs may have incompletely extracted requirements."
            ),
        },
        "demand_statistics": stats,
        "jobs": chunk,
        "personal_evidence": evidence,
        "privacy": {
            "personal_evidence_is_opt_in": True,
            "selected_personal_evidence_ids": [row["id"] for row in evidence],
            "private_job_notes_included": False,
            "recruiter_and_application_data_included": False,
            "unselected_personal_evidence_included": False,
        },
    }




def compare_career_snapshot(conn, package_id):
    """Compare selected, exported requirements/evidence with CURRENT records.

    Return only IDs and a version-bound confirmation token; never expose private
    evidence text to the preview response. Legacy packages have no P3 snapshot.
    """
    snap = conn.execute("""SELECT requirement_hashes_json,evidence_hashes_json,selection_json
        FROM career_export_snapshots WHERE package_id=?""", (package_id,)).fetchone()
    empty = {
        "career_stale_job_ids": [],
        "career_missing_job_ids": [],
        "career_stale_requirement_job_ids": [],
        "career_stale_evidence_ids": [],
        "career_missing_evidence_ids": [],
        "career_stale_token": "",
    }
    if snap is None:
        return empty
    expected_reqs = json.loads(snap["requirement_hashes_json"])
    include_ai = bool(json.loads(snap["selection_json"]).get("include_ai_reviewed",False))
    expected_evidence = json.loads(snap["evidence_hashes_json"])
    ids = sorted(int(job_id) for job_id in expected_reqs)
    saved = conn.execute("SELECT snapshot_json FROM analysis_packages WHERE id=?",
                         (package_id,)).fetchone()
    expected_job_hashes = json.loads(saved["snapshot_json"]) if saved else {}
    jobs = selected_jobs(conn, StatsFilters())
    jobs = [job for job in jobs if job["id"] in set(ids)]
    present_ids = [job["id"] for job in jobs]
    original_jobs = {row["job_id"]: row for row in read_jobs(conn, present_ids)} if present_ids else {}
    job_versions = {str(job_id): digest(row) for job_id, row in original_jobs.items()}
    missing_jobs = sorted(int(i) for i in expected_job_hashes if i not in job_versions)
    changed_jobs = sorted(int(i) for i, old in expected_job_hashes.items()
                          if i in job_versions and job_versions[i] != old)
    # If a job was deleted, ordinary analysis/findings validation catches it.
    confirmed, _, _, _ = current_requirements(conn, jobs, False)
    by_job = defaultdict(list)
    for requirement in confirmed:
        by_job[requirement["job_id"]].append(requirement)
    ai_by_job = defaultdict(list)
    if include_ai:
        from .ai_review import current_ai_requirements
        for item in current_ai_requirements(conn,jobs):
            ai_by_job[item["job_id"]].append(item)
    versions = {
        str(job["id"]): digest({
            "confirmed": projected_requirements(by_job.get(job["id"], [])),
            "gpt_reviewed": projected_ai_requirements(ai_by_job.get(job["id"], []))
        } if include_ai else projected_requirements(by_job.get(job["id"], [])))
        for job in jobs
    }
    outdated_jobs = sorted(int(job_id) for job_id, old in expected_reqs.items()
                           if versions.get(job_id) != old)
    current_evidence = {}
    if expected_evidence:
        selected = sorted(int(item_id) for item_id in expected_evidence)
        for start in range(0, len(selected), 400):
            batch = selected[start:start + 400]
            sql = """SELECT id,skill,proficiency,evidence_type,description,source_url,
                review_status,created_at,skills_json FROM profile_evidence WHERE id IN (""" + ",".join("?" for _ in batch) + ")"
            current_evidence.update({
                str(row["id"]): digest(project_evidence(row))
                for row in conn.execute(sql, batch)
            })
    missing_evidence = sorted(int(item_id) for item_id in expected_evidence
                              if item_id not in current_evidence)
    outdated_evidence = sorted(int(item_id) for item_id, old in expected_evidence.items()
                               if item_id in current_evidence and current_evidence[item_id] != old)
    if not (changed_jobs or missing_jobs or outdated_jobs or outdated_evidence or missing_evidence):
        return empty
    token = digest({
        "package_id": package_id,
        "job_versions": {str(i): job_versions.get(str(i), "deleted")
                         for i in changed_jobs + missing_jobs},
        "requirement_versions": {str(i): versions.get(str(i)) for i in outdated_jobs},
        "evidence_versions": {
            str(i): current_evidence.get(str(i), "deleted")
            for i in outdated_evidence + missing_evidence
        },
    })
    return {
        "career_stale_job_ids": changed_jobs,
        "career_missing_job_ids": missing_jobs,
        "career_stale_requirement_job_ids": outdated_jobs,
        "career_stale_evidence_ids": outdated_evidence,
        "career_missing_evidence_ids": missing_evidence,
        "career_stale_token": token,
    }

@router.post("/export")
def export_career_package(request: CareerExportRequest):
    export_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        # Hold the same database snapshot throughout reading, splitting and saving
        # packages. Failures roll back ALL package rows; no partial snapshots.
        conn.execute("BEGIN IMMEDIATE")
        raw = read_jobs(conn, request.job_ids)
        by_id = {job["job_id"]: job for job in raw}
        scope_jobs = selected_jobs(conn, StatsFilters())
        scope_jobs = [job for job in scope_jobs if job["id"] in by_id]
        if len(scope_jobs) != len(raw):
            raise HTTPException(422, "部分岗位无法进行需求统计，已取消整个导出")
        confirmed, _, _, _ = current_requirements(conn, scope_jobs, False)
        by_job = defaultdict(list)
        for req in confirmed:
            by_job[req["job_id"]].append(req)
        covered_ids = set(by_job)
        ai_by_job = defaultdict(list)
        if request.include_ai_reviewed:
            from .ai_review import current_ai_requirements
            for item in current_ai_requirements(conn, scope_jobs):
                ai_by_job[item["job_id"]].append(item)
        entries = [slim_job(job, by_job.get(job["job_id"], []), request.mode,
                            ai_by_job.get(job["job_id"], [])) for job in raw]
        personal = read_evidence(conn, request.evidence_ids)
        stats = stats_context(build_stats(conn, StatsFilters(include_ai_reviewed=request.include_ai_reviewed), job_ids=request.job_ids))
        # Requirement rows and personal evidence are hashed, not copied to the DB.
        requirement_hashes = {
            str(job["job_id"]): digest({
                "confirmed": entries[i]["confirmed_requirements"],
                "gpt_reviewed": entries[i]["gpt_reviewed_requirements"]
            } if request.include_ai_reviewed else entries[i]["confirmed_requirements"])
            for i, job in enumerate(raw)
        }
        evidence_hashes = {str(item["id"]): digest(item) for item in personal}
        chunks = take_chunks(entries)
        batches = []
        for index, chunk in enumerate(chunks, start=1):
            package_id = str(uuid.uuid4())
            batch_ids = [job["job_id"] for job in chunk]
            payload = build_payload(
                export_id=export_id, package_id=package_id, index=index,
                total_batches=len(chunks), total_jobs=len(raw),
                chunk=chunk, evidence=personal, stats=stats,
                request=request, covered_ids=covered_ids, generated_at=generated_at,
            )
            data = json_bytes(payload)
            if len(data) > MAX_BATCH_BYTES:
                raise HTTPException(
                    413, "单个求职分析包超过 3 MB，请减少选择的个人经历或岗位数量；不会截断 JD"
                )
            # Snapshots use the legacy analysis representation (not the compact
            # career representation) so existing findings-import stale checks work.
            scope = PackageRequest(job_ids=batch_ids).model_dump(
                exclude={"baseline_id", "save_snapshot"}
            )
            job_snapshots = {str(job_id): digest(by_id[job_id]) for job_id in batch_ids}
            conn.execute("""INSERT INTO analysis_packages(
                id,filters_json,snapshot_json,included_ids_json,item_count
            ) VALUES(?,?,?,?,?)""", (
                package_id, canonical(scope), canonical(job_snapshots),
                canonical(batch_ids), len(batch_ids),
            ))
            conn.execute("""INSERT INTO career_export_snapshots(
                package_id,export_id,mode,selection_json,
                requirement_hashes_json,evidence_hashes_json,included_requirement_ids_json
            ) VALUES(?,?,?,?,?,?,?)""", (
                package_id, export_id, request.mode,
                canonical({"ids": request.job_ids, "filters": request.selection_filters,
                           "goal": request.goal, "include_ai_reviewed": request.include_ai_reviewed}),
                canonical({str(job_id): requirement_hashes[str(job_id)]
                           for job_id in batch_ids}),
                canonical(evidence_hashes),
                canonical(sorted(req["requirement_id"] for item in chunk
                                 for field in ("confirmed_requirements","gpt_reviewed_requirements")
                                 for req in item[field])),
            ))
            batches.append(
                (f"career-{index:02d}of{len(chunks):02d}.json", data, package_id)
            )
        return download("career", export_id, batches, request.selection_filters, len(raw))
