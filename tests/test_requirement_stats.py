"""P2: Job-level JD demand statistics and auditable, manual skill aliases."""
import pytest
from fastapi.testclient import TestClient

from app.db import SCHEMA_VERSION, connect, init_db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH", str(tmp_path / "stats.sqlite"))
    with TestClient(app) as c:
        yield c


def add_company(c, name, headcount_text):
    r = c.post("/api/companies", json={
        "name": name, "headcount_text": headcount_text, "sector": "AI 应用",
        "notes": "PRIVATE COMPANY NOTE",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add_job(c, cid, title, jd, direction="agent", priority="high", city="北京"):
    r = c.post("/api/jobs", json={
        "company_id": cid, "title": title, "jd_text": jd,
        "direction": direction, "city": city, "priority": priority,
        "notes": "PRIVATE JOB NOTE",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def make_package(c, ids):
    r = c.post("/api/analysis/packages", json={"job_ids": ids})
    assert r.status_code == 200, r.text
    return r.json()["package"]["package_id"]


def requirement(jid, name, evidence, strength="must", kind="technical_skill", **extra):
    return {
        "job_id": jid, "name": name, "evidence": evidence,
        "strength": strength, "requirement_type": kind,
        "source_field": "jd_text", "option_logic": "single",
        "alternatives": [], **extra,
    }


def submit(c, pid, requirements, confirmed=True):
    r = c.post("/api/requirements/import", json={
        "package_id": pid, "requirements": requirements, "dry_run": False,
    })
    assert r.status_code == 200, r.text
    if confirmed:
        pending = c.get("/api/requirements?status=pending&limit=100").json()["items"]
        new_ids = [item["id"] for item in pending if item["package_id"] == pid]
        for identifier in new_ids:
            response = c.post(f"/api/requirements/{identifier}/review",
                              json={"action": "confirm"})
            assert response.status_code == 200, response.text
    return r.json()


def stats(c, **filters):
    r = c.get("/api/requirements/stats", params=filters)
    assert r.status_code == 200, r.text
    return r.json()


def test_schema_v13_migration_preserves_existing_job_and_requirements(client):
    assert SCHEMA_VERSION >= 13
    cid = add_company(client, "平台甲", "10000人以上")
    jid = add_job(client, cid, "Agent 一", "熟悉 Python")
    pid = make_package(client, [jid])
    submit(client, pid, [requirement(jid, "Python", "熟悉 Python")])
    init_db()
    with connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT jd_text FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "熟悉 Python"
        assert conn.execute("SELECT COUNT(*) FROM job_requirements").fetchone()[0] == 1


def test_empty_and_unreviewed_data_have_explicit_denominator(client):
    cid = add_company(client, "平台甲", "10000人以上")
    jid = add_job(client, cid, "Agent 一", "熟悉 Python")
    result = stats(client)
    assert result["selected_jobs"] == 1
    assert result["selected_companies"] == 1
    assert result["confirmed_covered_jobs"] == 0
    assert result["skills"] == []
    assert result["pending_skills"] == []
    pid = make_package(client, [jid])
    submit(client, pid, [requirement(jid, "Python", "熟悉 Python")], confirmed=False)
    assert stats(client)["confirmed_covered_jobs"] == 0
    assert stats(client,include_pending=True)["pending_covered_jobs"] == 1
    assert stats(client,include_pending=True)["pending_skills"][0]["job_count"] == 1


def test_deduplicate_same_skill_per_job_and_separate_strength_and_company_counts(client):
    c1 = add_company(client, "平台甲", "10000人以上")
    c2 = add_company(client, "平台乙", "15-50人")
    a = add_job(client, c1, "Agent 甲", "熟悉 Python；深入使用 Python；模型评测")
    b = add_job(client, c2, "Agent 乙", "了解 Python；工具调用", priority="medium")
    c = add_job(client, c1, "Agent 丙", "熟悉 Python；工具调用")
    d = add_job(client, c1, "其他岗位", "不要求 Python", direction="research")
    submit(client, make_package(client,[a,b,c]), [
        requirement(a,"Python","熟悉 Python",strength="must"),
        requirement(a,"python","深入使用 Python",strength="bonus"),
        requirement(b,"Python","了解 Python",strength="bonus"),
        requirement(c,"PYTHON","熟悉 Python",strength="must"),
    ])
    result=stats(client)
    assert result["selected_jobs"] == 4
    assert result["confirmed_covered_jobs"] == 3
    x = result["skills"][0]
    assert x["skill"].casefold() == "python"
    assert x["job_count"] == 3
    assert x["company_count"] == 2
    assert x["must_jobs"] == 2
    assert x["bonus_jobs"] == 1
    assert x["share_of_selected_jobs_pct"] == 75.0
    assert x["share_of_coded_jobs_pct"] == 100.0


def test_filters_direction_scale_priority_city_and_jd_keyword(client):
    a = add_company(client, "甲公司", "20-99 人")
    b = add_company(client, "乙公司", "10000人以上")
    first = add_job(client, a, "Agent 产品", "Tool Calling 项目", direction="agent",priority="high",city="北京")
    second = add_job(client, b, "研究实习", "Python", direction="research",priority="medium",city="上海")
    submit(client,make_package(client,[first,second]),[
        requirement(first,"Tool Calling","Tool Calling"),
        requirement(second,"Python","Python"),
    ])
    result = stats(client,direction="agent",headcount_max=99,include_unknown_size=False,priority="high",city="北京",q="Calling")
    assert result["selected_jobs"] == 1
    assert result["skills"][0]["skill"] == "Tool Calling"
    assert result["comparisons"]["direction"][0]["group"] == "agent"
    assert result["comparisons"]["direction"][0]["top_skills"][0]["skill"] == "Tool Calling"
    assert result["comparisons"]["company_scale"][0]["group"] == "20–99人"
    assert result["comparisons"]["priority"][0]["group"] == "high"
    assert result["selected_job_ids"] == [first]
    assert stats(client,headcount_min=10000,include_unknown_size=False)["selected_jobs"] == 1
    assert stats(client,q="no match")["selected_jobs"] == 0


def test_confirmed_and_pending_are_never_merged(client):
    cid = add_company(client, "智能公司", "10000人以上")
    a = add_job(client,cid,"岗位A","熟悉 Python")
    b = add_job(client,cid,"岗位B","了解 Python")
    submit(client,make_package(client,[a]),[requirement(a,"Python","熟悉 Python")])
    submit(client,make_package(client,[b]),[requirement(b,"Python","了解 Python",strength="bonus")],confirmed=False)
    basic=stats(client)
    both=stats(client,include_pending=True)
    assert basic["skills"][0]["job_count"] == 1
    assert basic["pending_skills"] == []
    assert both["skills"][0]["job_count"] == 1
    assert both["pending_skills"][0]["job_count"] == 1
    assert both["confirmed_covered_jobs"] == 1
    assert both["pending_covered_jobs"] == 1


def test_composite_skill_conditions_do_not_inflate_frequency(client):
    cid = add_company(client, "云公司","1000-9999人")
    jid=add_job(client,cid,"后端","熟悉 Python 或 Java 中一种")
    row=requirement(jid,"Python/Java", "Python 或 Java 中一种",
                    option_logic="any_of",alternatives=["Python","Java"])
    submit(client,make_package(client,[jid]),[row])
    result=stats(client)
    assert result["confirmed_requirements"] == 1
    assert result["confirmed_covered_jobs"] == 1
    assert result["skills"] == []
    assert result["composite_requirements"][0]["alternatives"] == ["Python","Java"]


def test_manual_aliases_merge_and_delete_without_mutating_original_requirements(client):
    cid=add_company(client,"乙平台","10000人以上")
    a=add_job(client,cid,"A","工具调用与 Agent")
    b=add_job(client,cid,"B","Tool Calling 开发")
    submit(client,make_package(client,[a,b]),[
        requirement(a,"工具调用","工具调用"),
        requirement(b,"Tool Calling","Tool Calling"),
    ])
    assert len(stats(client)["skills"]) == 2
    add=client.post("/api/requirements/stats/aliases",json={
        "alias":"工具调用","canonical":"Tool Calling",
    })
    assert add.status_code == 201, add.text
    result=stats(client)
    assert result["alias_revision"] == 1
    assert len(result["skills"]) == 1
    assert result["skills"][0]["job_count"] == 2
    assert client.get("/api/requirements/stats/aliases").json()["items"][0]["alias"] == "工具调用"
    assert client.post("/api/requirements/stats/aliases",json={
        "alias":"工具调用","canonical":"别名",
    }).status_code == 409
    assert client.post("/api/requirements/stats/aliases",json={
        "alias":"Tool Calling","canonical":"Tool Calling 高级版",
    }).status_code == 422
    r=client.delete("/api/requirements/stats/aliases",params={"alias":"工具调用"})
    assert r.status_code == 200, r.text
    assert stats(client)["alias_revision"] == 2
    assert len(stats(client)["skills"]) == 2
    events=client.get("/api/requirements/stats/aliases/history").json()
    assert [e["action"] for e in events[:2]] == ["delete","add"]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_requirements").fetchone()[0] == 2


def test_stale_jd_requirements_excluded_and_old_quote_never_counted(client):
    cid=add_company(client,"公司丁","20-99人")
    jid=add_job(client,cid,"算法","熟悉 Python")
    submit(client,make_package(client,[jid]),[requirement(jid,"Python","熟悉 Python")])
    r=client.patch(f"/api/jobs/{jid}",json={"jd_text":"现在只要求 Go"})
    assert r.status_code == 200,r.text
    result=stats(client)
    assert result["selected_jobs"] == 1
    assert result["confirmed_covered_jobs"] == 0
    assert result["stale_excluded_requirements"] == 1
    assert result["stale_affected_jobs"] == 1
    assert result["skills"] == []
    ev=client.get("/api/requirements/stats/evidence",params={"skill":"Python"})
    assert ev.status_code == 200,ev.text
    assert ev.json()["total_requirements"] == 0


def test_evidence_drilldown_pagination_filters_and_no_personal_notes(client):
    cid=add_company(client,"平台甲","10000人以上")
    a=add_job(client,cid,"Agent 一","需要 Python")
    b=add_job(client,cid,"Agent 二","需要 Python")
    submit(client,make_package(client,[a,b]),[
        requirement(a,"Python","需要 Python"),requirement(b,"Python","需要 Python")
    ])
    one=client.get("/api/requirements/stats/evidence",params={
        "skill":"Python","limit":1,"offset":0,"direction":"agent",
    })
    assert one.status_code == 200,one.text
    data=one.json()
    assert data["distinct_jobs"] == 2
    assert data["total_requirements"] == 2
    assert len(data["items"]) == 1
    assert data["items"][0]["evidence"] == "需要 Python"
    assert data["items"][0]["job_id"] == a
    assert data["comparisons"]["direction"][0]["group"] == "agent"
    assert "PRIVATE" not in one.text
    two=client.get("/api/requirements/stats/evidence",params={
        "skill":"Python","limit":1,"offset":1,
    })
    assert two.json()["items"][0]["job_id"] == b


def test_stats_routes_are_read_only_and_invalid_filters_rejected(client):
    cid=add_company(client,"平台甲","10000人以上")
    jid=add_job(client,cid,"实习","熟悉 Python")
    assert client.get("/api/requirements/stats",params={"headcount_min":-1}).status_code == 422
    assert client.get("/api/requirements/stats/evidence",params={"skill":"Python","limit":101}).status_code == 422
    before=client.get(f"/api/jobs/{jid}").json()["jd_text"]
    stats(client,include_pending=True)
    assert client.get(f"/api/jobs/{jid}").json()["jd_text"] == before


def test_frontend_stat_page_is_linked_with_alias_actions(client):
    js=client.get("/static/app.js").text
    page=client.get("/").text
    for token in ("function demandPage()","refreshDemandStats()","data-demand-skill",
                  "data-demand-filter","demand-alias-form","download-demand-json"):
        assert token in js
    # Advanced JD statistics remain available, but are no longer a sidebar item.
    assert 'data-page="demand"' not in page
    assert 'data-page="skills"' in page
    assert '能力准备' in page
    assert "['skills','能力差距']" in js
    assert "function skillsPage(){return skillGapPage()}" in js
    assert client.get("/static/skill-gap.js").status_code == 200


def test_alias_unicode_casefold_and_priority_top_skills(client):
    cid = add_company(client, "大模型工具厂", "10000人以上")
    j1 = add_job(client, cid, "岗位A", "要求 Agent 和 Python", priority="high")
    j2 = add_job(client, cid, "岗位B", "要求 Agent", priority="medium")
    submit(client, make_package(client,[j1,j2]), [
        requirement(j1, "Agent", "Agent"),
        requirement(j2, "Agent", "Agent"),
        requirement(j1, "Python", "Python"),
    ])
    priorities = {g["group"]: g for g in stats(client)["comparisons"]["priority"]}
    assert priorities["high"]["top_skills"][0]["skill"] == "Agent"
    assert priorities["high"]["top_skills"][0]["jobs"] == 1
    assert priorities["medium"]["top_skills"][0]["skill"] == "Agent"
    assert priorities["medium"]["top_skills"][0]["jobs"] == 1
    aliases = client.post("/api/requirements/stats/aliases", json={
        "alias": "Ägent", "canonical": "Agent",
    })
    assert aliases.status_code == 201, aliases.text
    conflict = client.post("/api/requirements/stats/aliases", json={
        "alias": "äGENT", "canonical": "Other",
    })
    assert conflict.status_code == 409, conflict.text
    deleted = client.delete("/api/requirements/stats/aliases", params={"alias": "äGENT"})
    assert deleted.status_code == 200, deleted.text
