"""Evidence-linked JD demand statistics; confirmed and AI-pending counts never mix.

Denominators are explicit: all selected jobs versus jobs with reviewed requirements.
Alias mappings are manual, one hop only, and recorded in an append-only audit log.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, field_validator

from .directions import Direction
from .db import connect
from .requirements import SKILL_TYPES, source_hash
from .company_profile import headcount_matches, scale_label

router = APIRouter(prefix="/api/requirements/stats", tags=["JD demand statistics"])
STRENGTH_ORDER = {"unknown": 0, "mentioned": 1, "bonus": 2, "must": 3}
DISPLAY_STRENGTH = ("must", "bonus", "mentioned", "unknown")


class StatsFilters(BaseModel):
    q: str = Field(default="", max_length=200)
    direction: Direction | Literal[""] = ""
    sector: str = Field(default='', max_length=180)
    headcount_min: int | None = Field(default=None, ge=0, le=1_000_000_000)
    headcount_max: int | None = Field(default=None, ge=0, le=1_000_000_000)
    include_unknown_size: bool = True
    city: str = Field(default="", max_length=120)
    priority: Literal["", "unrated", "high", "medium", "low", "skip"] = ""
    job_status: Literal["", "candidate", "verified", "uncertain", "closed"] = ""
    include_pending: bool = False
    include_ai_reviewed: bool = False


class AliasIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(min_length=1, max_length=120)
    canonical: str = Field(min_length=1, max_length=120)

    @field_validator("alias", "canonical")
    @classmethod
    def trim(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("技能名称不可为空")
        return value


def selected_jobs(conn, filters):
    if filters.headcount_min is not None and filters.headcount_max is not None and filters.headcount_min > filters.headcount_max:
        raise HTTPException(422, '人数下限不能大于上限')
    rows = [dict(row) for row in conn.execute("""SELECT j.id,j.company_id,
        j.title,j.direction,j.priority,j.status,j.city,j.jd_text,j.eligibility,
        c.name AS company,c.sector,c.headcount_min,c.headcount_max,c.headcount_text
        FROM jobs j JOIN companies c ON c.id=j.company_id ORDER BY j.id""")]
    query = filters.q.strip().casefold()
    result = []
    for job in rows:
        job["company_scale"] = scale_label(job)
        if filters.sector and (job['sector'] or '待录入') != filters.sector:
            continue
        if query and query not in (job["title"] + job["company"] + (job["jd_text"] or "")).casefold():
            continue
        if filters.direction and job["direction"] != filters.direction:
            continue
        if filters.priority and job["priority"] != filters.priority:
            continue
        if filters.city and job["city"] != filters.city:
            continue
        if filters.job_status and job["status"] != filters.job_status:
            continue
        if not headcount_matches(job, filters.headcount_min, filters.headcount_max, filters.include_unknown_size):
            continue
        result.append(job)
    return result


def read_aliases(conn):
    aliases = [dict(row) for row in conn.execute(
        "SELECT alias,canonical,created_at FROM skill_aliases ORDER BY alias COLLATE NOCASE"
    )]
    return aliases, {row["alias"].casefold(): row["canonical"] for row in aliases}


def current_requirements(conn, jobs, include_pending):
    """Exclude stale-version requirements, never count rejected rows."""
    if not jobs:
        return [], [], 0, 0
    from .ai_review import row_version
    by_id = {job["id"]: job for job in jobs}
    hashes = {job["id"]: source_hash(job) for job in jobs}
    valid, pending = [], []
    stale = 0
    stale_ids = set()
    ids = list(by_id)
    for start in range(0, len(ids), 400):
        part = ids[start:start + 400]
        placeholders = ",".join("?" for _ in part)
        query = f"""SELECT r.id,r.job_id,r.package_id,r.requirement_type,
            r.name,r.strength,r.evidence,r.source_field,r.option_logic,
            r.alternatives_json,r.source_jd_hash,r.review_status,
            ai.verdict AS ai_verdict,ai.source_jd_hash AS ai_source_hash,
            ai.requirement_version AS ai_requirement_version
            FROM job_requirements r
            LEFT JOIN ai_requirement_reviews ai ON ai.requirement_id=r.id
            WHERE r.review_status IN ('confirmed','pending')
            AND r.job_id IN ({placeholders}) ORDER BY r.id"""
        for row in conn.execute(query, part):
            entry = dict(row)
            job = by_id[entry["job_id"]]
            if (entry["source_jd_hash"] != hashes[job["id"]] or
                    entry["evidence"] not in (job[entry["source_field"]] or "")):
                stale += 1
                stale_ids.add(job["id"])
                continue
            if entry["review_status"] == "confirmed":
                valid.append(entry)
            elif include_pending:
                # An exclusion is valid only for this exact candidate revision.
                if (entry["ai_verdict"] == "excluded"
                        and entry["ai_source_hash"] == entry["source_jd_hash"]
                        and entry["ai_requirement_version"] == row_version(entry)):
                    continue
                pending.append(entry)
    return valid, pending, stale, len(stale_ids)


def normalized_skills(requirements, job_map, aliases):
    """One vote per skill per job, strongest explicit claim wins within a status."""
    skills = {}
    evidence = defaultdict(list)
    for req in requirements:
        if req["requirement_type"] not in SKILL_TYPES or req["option_logic"] != "single":
            continue
        display = aliases.get(req["name"].strip().casefold(), req["name"].strip())
        key = display.casefold()
        item = skills.setdefault(key, {"skill": display, "votes": {}})
        job_id = req["job_id"]
        existing = item["votes"].get(job_id)
        if existing is None or STRENGTH_ORDER[req["strength"]] > STRENGTH_ORDER[existing]:
            item["votes"][job_id] = req["strength"]
        evidence[key].append(req)
    summary = []
    for key, group in skills.items():
        votes = group["votes"]
        companies = {job_map[jid]["company_id"] for jid in votes}
        levels = {level: sum(v == level for v in votes.values()) for level in DISPLAY_STRENGTH}
        summary.append({
            "key": key, "skill": group["skill"], "job_count": len(votes),
            "company_count": len(companies), **{f"{level}_jobs": levels[level] for level in DISPLAY_STRENGTH},
        })
    summary.sort(key=lambda r: (-r["job_count"], -r["must_jobs"], r["skill"].casefold()))
    return summary, evidence


def comparison(jobs, confirmed, pending, confirmed_skills):
    """Group denominators are ALL selected jobs, never keyword occurrences."""
    cgroup = defaultdict(set)
    pgroup = defaultdict(set)
    for item in confirmed:
        cgroup[item["job_id"]].add(item["id"])
    for item in pending:
        pgroup[item["job_id"]].add(item["id"])
    groups = {}
    job_map = {job["id"]: job for job in jobs}
    for axis, getter in (("direction", lambda j: j["direction"]),
                         ("company_scale", lambda j: j["company_scale"]),
                         ("priority", lambda j: j["priority"])):
        by_group = defaultdict(list)
        for job in jobs:
            by_group[getter(job)].append(job)
        groups[axis] = []
        for key, group_jobs in sorted(by_group.items()):
            job_ids = {job["id"] for job in group_jobs}
            covered_ids = {jid for jid in job_ids if jid in cgroup}
            leaders = []
            for skill in confirmed_skills:
                overlap = set(skill["votes"]) & job_ids
                if overlap:
                    leaders.append({"skill": skill["skill"], "jobs": len(overlap),
                                    "must_jobs": sum(skill["votes"][jid] == "must" for jid in overlap)})
            leaders.sort(key=lambda item: (-item["jobs"], -item["must_jobs"], item["skill"].casefold()))
            groups[axis].append({
                "group": key, "selected_jobs": len(group_jobs),
                "companies": len({job["company_id"] for job in group_jobs}),
                "confirmed_covered_jobs": len(covered_ids),
                "pending_covered_jobs": sum(jid in pgroup for jid in job_ids),
                "top_skills": leaders[:5],
            })
    return groups


def build_stats(conn, filters, job_ids=None):
    jobs = selected_jobs(conn, filters)
    if job_ids is not None:
        chosen = set(job_ids)
        jobs = [job for job in jobs if job['id'] in chosen]
    aliases, alias_map = read_aliases(conn)
    confirmed, pending, stale, stale_jobs = current_requirements(conn, jobs, filters.include_pending)
    job_map = {job["id"]: job for job in jobs}
    skill_rows, _ = normalized_skills(confirmed, job_map, alias_map)
    # GPT results never enter human-confirmed counts. Separate opt-in stats.
    if filters.include_ai_reviewed:
        from .ai_review import current_ai_requirements
        ai_records = current_ai_requirements(conn, jobs)
    else:
        ai_records = []
    ai_skill_rows, _ = normalized_skills(ai_records, job_map, alias_map)
    ai_covered = {r["job_id"] for r in ai_records}
    for row in ai_skill_rows:
        row["share_of_selected_jobs_pct"] = round(
            100 * row["job_count"] / len(jobs), 1) if jobs else 0.0
        row["share_of_ai_reviewed_jobs_pct"] = round(
            100 * row["job_count"] / len(ai_covered), 1) if ai_covered else 0.0
    # Retain per-job normalized votes for cohort comparisons, not raw occurrences.
    cohort_votes = {}
    for req in confirmed:
        if req["requirement_type"] not in SKILL_TYPES or req["option_logic"] != "single":
            continue
        label = alias_map.get(req["name"].strip().casefold(), req["name"].strip())
        key = label.casefold()
        group = cohort_votes.setdefault(key, {"skill": label, "votes": {}})
        old = group["votes"].get(req["job_id"])
        if old is None or STRENGTH_ORDER[req["strength"]] > STRENGTH_ORDER[old]:
            group["votes"][req["job_id"]] = req["strength"]
    pending_rows, _ = normalized_skills(pending, job_map, alias_map)
    covered = {r["job_id"] for r in confirmed}
    pending_covered = {r["job_id"] for r in pending}
    for group in (skill_rows, pending_rows):
        for row in group:
            row["share_of_selected_jobs_pct"] = round(
                100 * row["job_count"] / len(jobs), 1) if jobs else 0.0
            row["share_of_coded_jobs_pct"] = round(
                100 * row["job_count"] / (len(covered) if group is skill_rows else len(pending_covered)), 1
            ) if (covered if group is skill_rows else pending_covered) else 0.0
    combo = []
    for status, records in (("confirmed", confirmed), ("pending", pending)):
        for row in records:
            if row["option_logic"] != "single":
                combo.append({
                    "requirement_id": row["id"], "job_id": row["job_id"],
                    "name": row["name"], "strength": row["strength"],
                    "status": status, "option_logic": row["option_logic"],
                    "alternatives": json.loads(row["alternatives_json"]),
                    "evidence": row["evidence"],
                })
    return {
        "schema": "ai-internship-radar-requirement-stats-v1",
        "filters": filters.model_dump(),
        "definitions": {
            "unit": "distinct_jobs",
            "denominator_selected": "All jobs matching current filters, regardless of extraction coverage",
            "denominator_coded": "Jobs with at least one current, confirmed requirement of any type",
            "scope": "Confirmed results only; pending figures are displayed separately if enabled",
            "composite_policy": "any_of/all_of are separate rows and never inflate individual skill frequency",
        },
        "selected_jobs": len(jobs),
        "selected_job_ids": sorted(job_map),
        "alias_mappings": aliases,
        "selected_companies": len({j["company_id"] for j in jobs}),
        "confirmed_covered_jobs": len(covered),
        "pending_covered_jobs": len(pending_covered),
        "confirmed_requirements": len(confirmed),
        "pending_requirements": len(pending),
        "ai_reviewed_requirements": len(ai_records),
        "ai_reviewed_covered_jobs": len(ai_covered),
        "ai_skills": ai_skill_rows,
        "ai_reviewed_scope": "Separate GPT-assisted records; not human confirmed",
        "stale_excluded_requirements": stale,
        "stale_affected_jobs": stale_jobs,
        "alias_revision": conn.execute("SELECT COUNT(*) FROM skill_alias_events").fetchone()[0],
        "skills": skill_rows,
        "pending_skills": pending_rows,
        "composite_requirements": combo,
        "comparisons": comparison(jobs, confirmed, pending, list(cohort_votes.values())),
    }


@router.get("")
def stats(filters: StatsFilters = Depends()):
    with connect() as conn:
        return build_stats(conn, filters)


@router.get("/evidence")
def skill_evidence(
    skill: str = Query(min_length=1, max_length=160),
    review_status: Literal["confirmed", "pending"] = "confirmed",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    filters: StatsFilters = Depends(),
):
    with connect() as conn:
        jobs = selected_jobs(conn, filters)
        aliases, alias_map = read_aliases(conn)
        confirmed, pending, stale, _ = current_requirements(
            conn, jobs, filters.include_pending or review_status == "pending"
        )
        source = confirmed if review_status == "confirmed" else pending
        job_map = {row["id"]: row for row in jobs}
        matched = []
        key = skill.strip().casefold()
        for req in source:
            if req["requirement_type"] not in SKILL_TYPES or req["option_logic"] != "single":
                continue
            normalized = alias_map.get(req["name"].strip().casefold(), req["name"].strip())
            if normalized.casefold() != key:
                continue
            job = job_map[req["job_id"]]
            matched.append({
                "requirement_id": req["id"], "job_id": job["id"],
                "job_title": job["title"], "company_id": job["company_id"],
                "company": job["company"], "direction": job["direction"],
                "company_scale": job["company_scale"], "priority": job["priority"],
                "source_field": req["source_field"], "evidence": req["evidence"],
                "strength": req["strength"], "review_status": req["review_status"],
                "package_id": req["package_id"],
            })
        matched.sort(key=lambda row: (row["job_id"], row["requirement_id"]))
        unique_jobs = {row["job_id"] for row in matched}
        by_axis = {}
        for axis in ("direction", "company_scale"):
            by_axis[axis] = [
                {"group": group, "jobs": len({x["job_id"] for x in matched if x[axis] == group})}
                for group in sorted({x[axis] for x in matched})
            ]
        return {
            "skill": skill, "review_status": review_status,
            "total_requirements": len(matched), "distinct_jobs": len(unique_jobs),
            "stale_excluded_requirements_in_scope": stale,
            "comparisons": by_axis, "offset": offset, "limit": limit,
            "items": matched[offset:offset + limit],
        }


@router.get("/aliases")
def list_aliases():
    with connect() as conn:
        aliases, _ = read_aliases(conn)
        revision = conn.execute("SELECT COUNT(*) FROM skill_alias_events").fetchone()[0]
        return {"revision": revision, "items": aliases}


@router.post("/aliases", status_code=201)
def add_alias(body: AliasIn):
    alias, canonical = body.alias, body.canonical
    if alias.casefold() == canonical.casefold():
        raise HTTPException(422, "别名与标准名称相同，无需映射")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing, aliases = read_aliases(conn)
        if alias.casefold() in aliases:
            raise HTTPException(409, "该别名已有映射，请先移除旧映射")
        if canonical.casefold() in aliases:
            raise HTTPException(422, "标准名称不能同时是另一映射的别名（禁止链式归一）")
        if any(row["canonical"].casefold() == alias.casefold() for row in existing):
            raise HTTPException(422, "该别名已作为其他技能的标准名称（禁止链式归一）")
        conn.execute("INSERT INTO skill_aliases(alias,canonical) VALUES(?,?)",
                     (alias, canonical))
        conn.execute("""INSERT INTO skill_alias_events(action,alias,new_canonical)
            VALUES('add',?,?)""", (alias, canonical))
        return {"alias": alias, "canonical": canonical}


@router.delete("/aliases")
def delete_alias(alias: str = Query(min_length=1, max_length=120)):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        entries, _ = read_aliases(conn)
        entry = next((r for r in entries if r["alias"].casefold() == alias.strip().casefold()), None)
        if not entry:
            raise HTTPException(404, "映射不存在")
        conn.execute("DELETE FROM skill_aliases WHERE alias=?", (entry["alias"],))
        conn.execute("""INSERT INTO skill_alias_events(action,alias,old_canonical)
            VALUES('delete',?,?)""", (entry["alias"], entry["canonical"]))
        return {"ok": True, "alias": entry["alias"]}


@router.get("/aliases/history")
def alias_history(limit: int = Query(default=50, ge=1, le=200)):
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM skill_alias_events ORDER BY id DESC LIMIT ?", (limit,)
        )]
