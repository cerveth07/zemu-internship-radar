"""Reviewable extraction of JD requirements from saved ChatGPT analysis packages.

Model-proposed text never becomes confirmed data without a human review action.
Evidence must be an exact substring of the source JD or eligibility field.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .analysis import PackageRequest, canonical, digest, fetch_records
from .db import connect

router = APIRouter(prefix="/api/requirements", tags=["Reviewed JD requirements"])
TYPES = Literal[
    "technical_skill", "product_skill", "research_skill", "education",
    "graduation_year", "internship_schedule", "experience",
    "responsibility", "other"
]
STRENGTH = Literal["must", "bonus", "mentioned", "unknown"]
SOURCE_FIELD = Literal["jd_text", "eligibility"]
OPTION_LOGIC = Literal["single", "any_of", "all_of"]
REVIEW_STATUS = Literal["pending", "confirmed", "rejected"]
SKILL_TYPES = {"technical_skill", "product_skill", "research_skill"}


class RequirementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: int = Field(gt=0)
    requirement_type: TYPES
    name: str = Field(min_length=1, max_length=160)
    strength: STRENGTH
    evidence: str = Field(min_length=1, max_length=700)
    source_field: SOURCE_FIELD
    option_logic: OPTION_LOGIC = "single"
    alternatives: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("name", "evidence")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("名称和参考资料不能只有空白")
        return value

    @field_validator("alternatives")
    @classmethod
    def validate_alternatives(cls, values):
        values = [value.strip() for value in values]
        if any(not value or len(value) > 120 for value in values):
            raise ValueError("可替代条件不能留空且不超过 120 字")
        if len(set(values)) != len(values):
            raise ValueError("可替代条件不能重复")
        return values

    @model_validator(mode="after")
    def check_group(self):
        if self.option_logic == "single" and self.alternatives:
            raise ValueError("单项要求不得填写 alternatives")
        if self.option_logic != "single" and len(self.alternatives) < 2:
            raise ValueError("any_of / all_of 要求至少两个 alternatives")
        if self.option_logic != "single" and any(alt not in self.evidence for alt in self.alternatives):
            raise ValueError("组合要求的每个选项都必须出现在参考资料原文中")
        return self


class RequirementImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(min_length=36, max_length=36)
    requirements: list[RequirementIn] = Field(min_length=1, max_length=800)
    dry_run: bool = True


class ReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "reject", "reopen"]
    sync_skill: bool = False
    notes: str = Field(default="", max_length=1000)


def source_hash(job):
    return hashlib.sha256(canonical({
        "jd_text": job["jd_text"], "eligibility": job["eligibility"],
    }).encode("utf-8")).hexdigest()


def requirement_fingerprint(item, jd_hash):
    # Deduplicate across packages for the same source version, never across JD edits.
    return digest({"requirement": item.model_dump(exclude={"job_id"}),
                   "source_jd_hash": jd_hash})


def package_jobs(conn, package_id, requested_ids):
    pkg = conn.execute("""SELECT snapshot_json,included_ids_json,filters_json,jd_hashes_json
        FROM analysis_packages WHERE id=?""", (package_id,)).fetchone()
    if not pkg:
        raise HTTPException(404, "分析包不存在：请先从岗位库导出并保存快照")
    included = set(json.loads(pkg["included_ids_json"]))
    if not set(requested_ids).issubset(included):
        raise HTTPException(422, "有岗位 ID 不属于该分析包提供的 JD")
    private = bool(json.loads(pkg["filters_json"]).get("include_private", False))
    jobs = fetch_records(conn, PackageRequest(
        job_ids=sorted(set(requested_ids)), include_private=private,
    ))
    original = json.loads(pkg["snapshot_json"])
    source_versions = json.loads(pkg['jd_hashes_json'])
    if not source_versions:
        # Existing extraction exports already stored narrow JD hashes. Older
        # generic packages have only a whole-record hash: retain the safe check.
        batch = conn.execute('SELECT jd_hashes_json FROM jd_extraction_batches WHERE package_id=?', (package_id,)).fetchone()
        source_versions = json.loads(batch['jd_hashes_json']) if batch else {}
    stale = [j["job_id"] for j in jobs
             if (source_versions.get(str(j['job_id'])) != source_hash(j)
                 if str(j['job_id']) in source_versions else
                 original.get(str(j["job_id"])) != digest(j))]
    return {j["job_id"]: j for j in jobs}, sorted(stale)


@router.post("/import")
def import_requirements(request: RequirementImport):
    ids = sorted({item.job_id for item in request.requirements})
    with connect() as conn:
        conn.execute("BEGIN" if request.dry_run else "BEGIN IMMEDIATE")
        jobs, stale = package_jobs(conn, request.package_id, ids)
        proposed = {}
        for item in request.requirements:
            job = jobs[item.job_id]
            evidence_source = job[item.source_field] or ""
            if item.evidence not in evidence_source:
                raise HTTPException(422, f"岗位 #{item.job_id} 的参考资料不是 {item.source_field} 的逐字原文：{item.evidence[:50]}")
            key = (item.job_id, requirement_fingerprint(item, source_hash(job)))
            proposed[key] = item
        results = []
        for (job_id, fingerprint), item in proposed.items():
            existing = conn.execute("""SELECT id,review_status FROM job_requirements
                WHERE job_id=? AND fingerprint=?""", (job_id, fingerprint)).fetchone()
            results.append({
                "job_id": job_id, "name": item.name, "fingerprint": fingerprint,
                "existing_id": existing["id"] if existing else None,
                "review_status": existing["review_status"] if existing else "pending",
                "new": existing is None,
            })
        if stale and not request.dry_run:
            raise HTTPException(409, f"岗位资料在导出后已变化，禁止写入旧要求：{stale}")
        new_count = sum(item["new"] for item in results)
        if not request.dry_run:
            for (job_id, fingerprint), item in proposed.items():
                if not next(row["new"] for row in results
                            if row["job_id"] == job_id and row["fingerprint"] == fingerprint):
                    continue
                conn.execute("""INSERT INTO job_requirements(
                    job_id,package_id,requirement_type,name,strength,evidence,
                    source_field,option_logic,alternatives_json,source_jd_hash,fingerprint)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                        job_id, request.package_id, item.requirement_type, item.name,
                        item.strength, item.evidence, item.source_field,
                        item.option_logic, canonical(item.alternatives),
                        source_hash(jobs[job_id]), fingerprint,
                    ))
        if not request.dry_run:
            conn.execute("""UPDATE jd_extraction_batches
                SET imported_at=datetime('now'),last_activity_at=datetime('now'),
                    import_count=import_count+?,closed_no_candidates=0
                WHERE package_id=?""", (len(request.requirements),request.package_id))
        return {
            "dry_run": request.dry_run, "total": len(request.requirements),
            "unique": len(proposed), "new_count": new_count,
            "duplicate_count": len(request.requirements) - new_count,
            "stale_job_ids": stale, "items": results,
        }


@router.get("")
def list_requirements(
    status: REVIEW_STATUS = "pending",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    package_id: str = Query(default='', max_length=36),
):
    with connect() as conn:
        where = "WHERE r.review_status=?"
        params = [status]
        if package_id:
            where += " AND r.package_id=?"
            params.append(package_id)
        total = conn.execute(
            "SELECT COUNT(*) FROM job_requirements r " + where, params,
        ).fetchone()[0]
        records = conn.execute("""SELECT r.*,j.title AS job_title,c.name AS company
            FROM job_requirements r
            JOIN jobs j ON j.id=r.job_id
            JOIN companies c ON c.id=j.company_id """ + where + """
            ORDER BY r.id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        items = []
        for row in records:
            item = dict(row)
            item["alternatives"] = json.loads(item.pop("alternatives_json"))
            item["can_sync_skill"] = (
                item["requirement_type"] in SKILL_TYPES and
                item["option_logic"] == "single" and
                item["strength"] in {"must", "bonus"}
            )
            items.append(item)
        return {"status": status, "total": total, "offset": offset,
                "limit": limit, "package_id": package_id, "items": items}


class BatchReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(min_length=1, max_length=100)
    action: Literal["confirm", "reject"]

    @field_validator("ids")
    @classmethod
    def check_ids(cls, value):
        if len(set(value)) != len(value) or any(i < 1 for i in value):
            raise ValueError("要求 ID 必须唯一且为正整数")
        return value


@router.put("/{requirement_id}")
def edit_requirement(requirement_id: int, revised: RequirementIn):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM job_requirements WHERE id=?",
                               (requirement_id,)).fetchone()
        if current is None:
            raise HTTPException(404, "要求记录不存在")
        if current["review_status"] != "pending":
            raise HTTPException(409, "只能编辑待审核要求，请先重新打开")
        if revised.job_id != current["job_id"]:
            raise HTTPException(422, "不能将现有要求移到另一个岗位")
        job = conn.execute("SELECT jd_text,eligibility FROM jobs WHERE id=?",
                           (current["job_id"],)).fetchone()
        if job is None or source_hash(job) != current["source_jd_hash"]:
            raise HTTPException(409, "JD 已更新，请从新版资料重新提取")
        if revised.evidence not in (job[revised.source_field] or ""):
            raise HTTPException(422, "新参考资料必须是对应 JD/资格字段的逐字原文")
        fingerprint = requirement_fingerprint(revised, current["source_jd_hash"])
        conflict = conn.execute("""SELECT id FROM job_requirements
            WHERE job_id=? AND fingerprint=? AND id!=?""",
            (current["job_id"], fingerprint, requirement_id)).fetchone()
        if conflict:
            raise HTTPException(409, f"该要求与已有记录 #{conflict['id']} 重复")
        conn.execute("""UPDATE job_requirements SET
            requirement_type=?,name=?,strength=?,evidence=?,source_field=?,
            option_logic=?,alternatives_json=?,fingerprint=?
            WHERE id=?""",
            (revised.requirement_type, revised.name, revised.strength,
             revised.evidence, revised.source_field, revised.option_logic,
             canonical(revised.alternatives), fingerprint, requirement_id))
        conn.execute("UPDATE jd_extraction_batches SET last_activity_at=datetime('now') WHERE package_id=?",
                     (current["package_id"],))
        return {"ok": True, "id": requirement_id, "review_status": "pending"}


def apply_review(conn, requirement_id, action, sync_skill=False, notes=""):
    row = conn.execute("SELECT * FROM job_requirements WHERE id=?",
                       (requirement_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"要求记录 #{requirement_id} 不存在")
    if action == "reopen":
        if row["review_status"] == "pending":
            return {"ok": True, "review_status": "pending", "changed": False}
        conn.execute("""UPDATE job_requirements SET review_status='pending',
            reviewed_at=NULL,review_notes=? WHERE id=?""", (notes, requirement_id))
        conn.execute("UPDATE jd_extraction_batches SET last_activity_at=datetime('now') WHERE package_id=?",
                     (row["package_id"],))
        return {"ok": True, "review_status": "pending", "changed": True,
                "skill_was_synced": bool(row["skill_synced"])}
    if row["review_status"] != "pending":
        raise HTTPException(409, f"要求 #{requirement_id} 已审核；需先重新打开")
    if action == "confirm":
        job = conn.execute("SELECT jd_text,eligibility FROM jobs WHERE id=?",
                           (row["job_id"],)).fetchone()
        if job is None:
            raise HTTPException(404, "关联岗位已不存在")
        if source_hash(job) != row["source_jd_hash"]:
            raise HTTPException(409, f"岗位 #{row['job_id']} 的 JD 已发生变化，须重新提取")
        if row["evidence"] not in (job[row["source_field"]] or ""):
            raise HTTPException(409, "参考资料已不在岗位原文中")
    skill_synced = bool(row["skill_synced"])
    skill_existing = False
    if sync_skill:
        if (row["requirement_type"] not in SKILL_TYPES or
                row["option_logic"] != "single" or
                row["strength"] not in {"must", "bonus"}):
            raise HTTPException(422, "只能同步必需或加分的单项技能")
        conn.execute("INSERT OR IGNORE INTO skills(name,category) VALUES(?,?)",
                     (row["name"], row["requirement_type"]))
        skill = conn.execute("SELECT id FROM skills WHERE name=? COLLATE NOCASE",
                             (row["name"],)).fetchone()
        exists = conn.execute("""SELECT 1 FROM job_skills
            WHERE job_id=? AND skill_id=?""",
            (row["job_id"], skill["id"])).fetchone()
        if exists:
            skill_existing = True
        else:
            conn.execute("""INSERT INTO job_skills(job_id,skill_id,necessity,evidence)
                VALUES(?,?,?,?)""", (
                row["job_id"], skill["id"], row["strength"], row["evidence"],
            ))
            skill_synced = True
    status = "confirmed" if action == "confirm" else "rejected"
    conn.execute("""UPDATE job_requirements SET review_status=?,review_notes=?,
        skill_synced=?,reviewed_at=datetime('now') WHERE id=?""",
        (status, notes, int(skill_synced), requirement_id))
    conn.execute("UPDATE jd_extraction_batches SET last_activity_at=datetime('now') WHERE package_id=?",
                 (row["package_id"],))
    return {"ok": True, "review_status": status, "skill_synced": skill_synced,
            "skill_already_present": skill_existing}


@router.post("/review-batch")
def review_batch(batch: BatchReviewAction):
    # All-or-nothing: if any item is stale or already reviewed, none are changed.
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for requirement_id in batch.ids:
            apply_review(conn, requirement_id, batch.action)
        return {"ok": True, "reviewed_count": len(batch.ids),
                "review_status": "confirmed" if batch.action == "confirm" else "rejected"}


@router.post("/{requirement_id}/review")
def review_requirement(requirement_id: int, action: ReviewAction):
    if action.action != "confirm" and action.sync_skill:
        raise HTTPException(422, "只有人工确认时才能同步技能")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return apply_review(conn, requirement_id, action.action,
                            sync_skill=action.sync_skill, notes=action.notes)
