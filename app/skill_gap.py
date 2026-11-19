"""Unified job-skill demand and user-assessed capability gaps.

Human confirmed and current GPT-reviewed JD claims remain separately traceable.
Personal capability status is never inferred from a JD or AI verdict.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .skill_curation import classify_candidate, canonical_personal_status
from .ai_review import current_ai_requirements
from .db import connect
from .requirement_stats import (
    STRENGTH_ORDER, StatsFilters, current_requirements, read_aliases, selected_jobs,
)
from .requirements import SKILL_TYPES, source_hash

router = APIRouter(prefix="/api/skill-gap", tags=["Personal skill gaps"])
Scope = Literal["target", "all"]
SkillStatus = Literal["unknown", "met", "partial", "missing"]

# Curated skill-cleanup V1 staging tables written by the offline handoff package.
# They live in separate shadow tables and never become confirmed requirements.
CLEANUP_TABLE = "radar_skill_cleanup_links"
CLEANUP_LIVE_CHECK = "candidate_supported_by_current_jd"
# Human-confirmed claims always outrank assisted ones, which outrank candidates.
ORIGIN_SCORE = {"confirmed": 5, "manual_skill": 4, "gpt_assisted": 3,
                "cleanup_candidate": 2, "pending_candidate": 1}


def manual_skill_claims(conn, jobs):
    """Use explicit human reviews with live evidence; imported tags alone do not count."""
    by_id = {job['id']: job for job in jobs}
    reviewed = {}
    for row in conn.execute("""SELECT job_id,payload_json FROM job_history WHERE id IN (
            SELECT MAX(id) FROM job_history WHERE event='skills_reviewed' GROUP BY job_id)"""):
        if row['job_id'] not in by_id:
            continue
        payload = json.loads(row['payload_json'])
        job = by_id[row['job_id']]
        if not payload.get('user_confirmed'):
            continue
        if payload.get('source_jd_hash') and payload['source_jd_hash'] != source_hash(job):
            continue
        reviewed[row['job_id']] = payload.get('after', [])
    claims = []
    for row in conn.execute('''SELECT js.job_id,s.name,js.necessity,js.evidence
            FROM job_skills js JOIN skills s ON s.id=js.skill_id'''):
        jid = row['job_id']
        if jid not in reviewed or not row['evidence'].strip() or row['evidence'] not in (by_id[jid]['jd_text'] or ''):
            continue
        if not any(all(item.get(key) == row[key] for key in ('name','necessity','evidence')) for item in reviewed[jid]):
            continue
        claims.append({'id':None,'job_id':jid,'name':row['name'],
                       'strength':{'must':'must','bonus':'bonus'}.get(row['necessity'],'unknown'),
                       'evidence':row['evidence'],'source_field':'jd_text',
                       'requirement_type':None,'option_logic':'single'})
    return claims


def cleanup_candidates(conn, job_ids) -> list[dict]:
    """Read staged skill-cleanup candidates limited to the selected jobs.

    The handoff vocabulary is already curated, so these rows are not re-filtered
    by JD requirement type. Missing shadow tables simply mean no candidates.
    """
    if not job_ids:
        return []
    staged = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                          (CLEANUP_TABLE,)).fetchone()
    if not staged:
        return []
    records = []
    for row in conn.execute(f"""SELECT l.job_id,l.skill_name,l.category,l.necessity,l.evidence_json,j.jd_text
            FROM {CLEANUP_TABLE} l JOIN jobs j ON j.id=l.job_id
            WHERE l.live_check=? AND l.review_status='candidate'
            ORDER BY l.batch_id,l.job_id,l.skill_name""", (CLEANUP_LIVE_CHECK,)):
        if row["job_id"] not in job_ids:
            continue
        try:
            quotes = json.loads(row["evidence_json"] or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(quotes, list) or not quotes:
            continue
        # An import-time marker is not proof that the current JD still supports
        # this candidate. Reject malformed or stale evidence, never invent it.
        if any(not isinstance(q, str) or not q.strip() or q not in (row["jd_text"] or "")
               for q in quotes):
            continue
        if not row["skill_name"].strip() or row["necessity"] not in STRENGTH_ORDER:
            continue
        text = " ｜ ".join(quotes)
        curation = classify_candidate(row["skill_name"], row["category"],
                                     row["necessity"], quotes)
        records.append({"job_id": row["job_id"], "name": row["skill_name"],
                        "strength": row["necessity"], "evidence": text,
                        **curation})
    return records


class QuickLearnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill: str = Field(min_length=1, max_length=500)

    @field_validator("skill")
    @classmethod
    def valid_skill(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("能力名称不可为空")
        return value


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill: str = Field(min_length=1, max_length=160)
    status: SkillStatus
    note: str = Field(default="", max_length=1000)

    @field_validator("skill", "note")
    @classmethod
    def strip_value(cls, value: str) -> str:
        return value.strip()


def normalize(name: str, aliases: dict) -> str:
    text = name.strip()
    return aliases.get(text.casefold(), text)


def build_gap(conn, filters: StatsFilters, scope: Scope = "target") -> dict:
    jobs = selected_jobs(conn, filters)
    if scope == "target" and not filters.priority:
        jobs = [job for job in jobs if job["priority"] in ("high", "medium")]
    # GPT-assisted records remain separate from human-confirmed JD claims.
    confirmed, pending, stale, _ = current_requirements(conn, jobs, filters.include_pending)
    assisted = current_ai_requirements(conn, jobs)
    manual = manual_skill_claims(conn, jobs)
    assisted_ids = {row["id"] for row in assisted}
    pending = [row for row in pending if row["id"] not in assisted_ids]
    _, aliases = read_aliases(conn)
    job_map = {job["id"]: job for job in jobs}

    groups: dict[str, dict] = {}
    for origin, records in (("confirmed", confirmed), ("manual_skill", manual), ("gpt_assisted", assisted),
                                ("pending_candidate", pending)):
        for item in records:
            if origin != 'manual_skill' and (item["requirement_type"] not in SKILL_TYPES or item["option_logic"] != "single"):
                continue
            name = normalize(item["name"], aliases)
            key = name.casefold()
            job = job_map[item["job_id"]]
            group = groups.setdefault(key, {"skill": name, "votes": {},
                                            "evidence": [], "origins": set(),
                                            "actionable": True, "curation_flags": set()})
            group["origins"].add(origin)
            old = group["votes"].get(job["id"])
            score = (ORIGIN_SCORE[origin], STRENGTH_ORDER[item["strength"]])
            if old is None or score > old["score"]:
                group["votes"][job["id"]] = {"strength": item["strength"],
                                             "score": score, "origin": origin}
            group["evidence"].append({
                "requirement_id": item["id"], "job_id": job["id"],
                "company": job["company"], "job_title": job["title"],
                "strength": item["strength"], "origin": origin,
                "text": item["evidence"],
            })

    cleanup = cleanup_candidates(conn, set(job_map)) if filters.include_pending else []
    for item in cleanup:
        name = normalize(item["name"], aliases)
        key = name.casefold()
        job = job_map[item["job_id"]]
        group = groups.setdefault(key, {"skill": name, "votes": {},
                                        "evidence": [], "origins": set(),
                                        "actionable": item["actionable"],
                                        "curation_flags": set()})
        group["curation_flags"].update(item["curation_flags"])
        group["origins"].add("cleanup_candidate")
        old = group["votes"].get(job["id"])
        score = (ORIGIN_SCORE["cleanup_candidate"],
                 STRENGTH_ORDER.get(item["strength"], 0))
        if old is None or score > old["score"]:
            group["votes"][job["id"]] = {"strength": item["strength"],
                                         "score": score, "origin": "cleanup_candidate"}
        group["evidence"].append({
            "requirement_id": None, "job_id": job["id"],
            "company": job["company"], "job_title": job["title"],
            "strength": item["strength"], "origin": "cleanup_candidate",
            "text": item["evidence"],
        })

    personal = canonical_personal_status(conn.execute(
        "SELECT skill_key,status,note,updated_at FROM personal_skill_status"), aliases)
    evidence = defaultdict(list)
    for row in conn.execute("SELECT id,skill,proficiency,evidence_type,description,"
                            "source_url,review_status,skills_json FROM profile_evidence ORDER BY id DESC"):
        for skill in set(value for value in (json.loads(row["skills_json"]) or [row["skill"]]) if value):
            evidence[normalize(skill, aliases).casefold()].append(dict(row))
    learning = defaultdict(list)
    for row in conn.execute("""SELECT id,title,related_skill,status,priority FROM learning_tasks
                             ORDER BY CASE WHEN status='done' THEN 1 ELSE 0 END, id DESC"""):
        if row["related_skill"].strip():
            learning[normalize(row["related_skill"], aliases).casefold()].append(dict(row))

    skills = []
    for key, group in groups.items():
        votes = group["votes"]
        counts = {label: sum(v["strength"] == label for v in votes.values())
                  for label in ("must", "bonus", "mentioned", "unknown")}
        skills.append({
            "skill": group["skill"], "key": key,
            "actionable": group["actionable"],
            "curation_flags": sorted(group["curation_flags"]),
            "job_count": len(votes),
            "company_count": len({job_map[jid]["company_id"] for jid in votes}),
            "must_jobs": counts["must"], "bonus_jobs": counts["bonus"],
            "mentioned_jobs": counts["mentioned"], "unknown_jobs": counts["unknown"],
            "confirmed_jobs": sum(v["origin"] == "confirmed" for v in votes.values()),
            "manual_jobs": sum(v["origin"] == "manual_skill" for v in votes.values()),
            "gpt_jobs": sum(v["origin"] == "gpt_assisted" for v in votes.values()),
            "candidate_jobs": sum(v["origin"] == "pending_candidate" for v in votes.values()),
            "cleanup_jobs": sum(v["origin"] == "cleanup_candidate" for v in votes.values()),
            "status": personal.get(key, {}).get("status", "unknown"),
            "note": personal.get(key, {}).get("note", ""),
            "personal_evidence": evidence.get(key, [])[:8],
            "learning_tasks": learning.get(key, [])[:8],
            "has_active_learning": any(
                task["status"] in ("todo", "doing") for task in learning.get(key, [])
            ),
            "examples": group["evidence"][:3],
            "total_evidence": len(group["evidence"]),
        })
    skills.sort(key=lambda s: (not s["actionable"], -s["job_count"],
                               -s["must_jobs"], s["skill"].casefold()))
    return {
        "scope": scope, "selected_jobs": len(jobs),
        "covered_jobs": len({item["job_id"] for item in confirmed + assisted + manual}),
        "candidate_covered_jobs": len({item["job_id"] for item in pending}),
        "cleanup_covered_jobs": len({item["job_id"] for item in cleanup}),
        "stale_requirements": stale,
        "skills": skills,
        "definitions": {
            "job_count": "Distinct selected jobs requiring this skill, not mentions",
            "priority": "Target scope defaults to high + medium when no explicit priority filter",
            "status": "User-assessed, never automatically set by GPT",
            "source": "Confirmed requirements > manually reviewed skills > GPT-reviewed > staged skill-cleanup candidates > "
                      "pending candidates; every candidate tier requires separate review",
        },
    }


@router.get("")
def list_gap(
    filters: StatsFilters = Depends(),
    scope: Scope = "target",
):
    with connect() as conn:
        return build_gap(conn, filters, scope)


@router.post("/learning")
def quick_add_learning(data: QuickLearnIn):
    """Add one task from a skill row; repeat clicks reuse unfinished tasks.

    No change to personal proficiency: learning intent is independent of mastery.
    """
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _, aliases = read_aliases(conn)
        skill = normalize(data.skill, aliases)
        key = skill.casefold()
        for existing in conn.execute("""SELECT * FROM learning_tasks
            WHERE status IN ('todo','doing') AND trim(related_skill)!=''
            ORDER BY id DESC"""):
            if normalize(existing["related_skill"], aliases).casefold() == key:
                return {"created": False, "task": dict(existing)}
        title = ("学习：" + skill)[:200]
        cursor = conn.execute("""INSERT INTO learning_tasks
            (title,related_skill,source_job_id,priority,status,notes)
            VALUES(?,?,NULL,'medium','todo','')""", (title, skill))
        task = conn.execute("SELECT * FROM learning_tasks WHERE id=?",
                            (cursor.lastrowid,)).fetchone()
        return {"created": True, "task": dict(task)}


@router.get("/evidence")
def gap_evidence(
    skill: str = Query(min_length=1, max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    scope: Scope = "target",
    filters: StatsFilters = Depends(),
):
    with connect() as conn:
        # Return the same current, scope-limited claims used by the overview.
        jobs = selected_jobs(conn, filters)
        if scope == "target" and not filters.priority:
            jobs = [job for job in jobs if job["priority"] in ("high", "medium")]
        confirmed, pending, _, _ = current_requirements(conn, jobs, filters.include_pending)
        assisted = current_ai_requirements(conn, jobs)
        manual = manual_skill_claims(conn, jobs)
        assisted_ids = {row["id"] for row in assisted}
        pending = [row for row in pending if row["id"] not in assisted_ids]
        _, aliases = read_aliases(conn)
        names = {row["id"]: row for row in jobs}
        claims = []
        for origin, records in (("confirmed", confirmed), ("manual_skill", manual), ("gpt_assisted", assisted),
                                ("pending_candidate", pending)):
            for req in records:
                if origin != 'manual_skill' and (req["requirement_type"] not in SKILL_TYPES or req["option_logic"] != "single"):
                    continue
                if normalize(req["name"], aliases).casefold() != skill.strip().casefold():
                    continue
                job = names[req["job_id"]]
                claims.append({
                    "requirement_id": req["id"], "job_id": job["id"],
                    "job_title": job["title"], "company": job["company"],
                    "origin": origin, "strength": req["strength"],
                    "evidence": req["evidence"], "source_field": req["source_field"],
                })
        cleanup = cleanup_candidates(conn, set(names)) if filters.include_pending else []
        for item in cleanup:
            if normalize(item["name"], aliases).casefold() != skill.strip().casefold():
                continue
            job = names[item["job_id"]]
            claims.append({
                "requirement_id": None, "job_id": job["id"],
                "job_title": job["title"], "company": job["company"],
                "origin": "cleanup_candidate", "strength": item["strength"],
                "evidence": item["evidence"], "source_field": "jd_text",
            })
        claims.sort(key=lambda v: (v["job_id"], v["requirement_id"] or 0))
        return {"skill": skill, "total": len(claims), "offset": offset,
                "limit": limit, "items": claims[offset:offset + limit]}


@router.put("/status")
def set_status(body: StatusUpdate):
    name = body.skill.strip()
    if not name or len(name) > 160:
        raise HTTPException(422, "能力名称必须在 1–160 字以内")
    with connect() as conn:
        _, aliases = read_aliases(conn)
        name = normalize(name, aliases)
        conn.execute("""INSERT INTO personal_skill_status(skill_key,status,note)
            VALUES(?,?,?) ON CONFLICT(skill_key) DO UPDATE SET
            status=excluded.status,note=excluded.note,updated_at=datetime('now')""",
            (name.casefold(), body.status, body.note))
        return {"ok": True, "skill": name, "status": body.status, "note": body.note}
