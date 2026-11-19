"""Owned BossHunter observer contract with temporary SQLite and no network."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
from threading import Lock
from types import SimpleNamespace

import pytest


BRIDGE_PATH = Path(__file__).resolve().parents[1] / "tools" / "boss_radar_bridge.py"
spec = importlib.util.spec_from_file_location("boss_radar_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def identity_exists(conn, platform, source_id, *, legacy_job_id=None):
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE source_platform = ? AND source_job_id = ?",
        (platform, source_id),
    ).fetchone()
    return bool(row or (platform == "boss" and conn.execute(
        "SELECT 1 FROM jobs WHERE id = ?", (legacy_job_id or source_id,),
    ).fetchone()))


class Processor:
    def __init__(self, conn, request, *, run_id, stop_event=None, config=None, emit=None):
        self.conn = conn
        self.request = request
        self.run_id = run_id
        self.config = config or {}
        self.progress = SimpleNamespace(duplicate=0, seen=0, filtered=0)
        self.new_job_ids = []

    def event(self, **kwargs):
        pass

    def inspect(self, candidate):
        if identity_exists(self.conn, candidate.platform, candidate.source_job_id,
                           legacy_job_id=candidate.storage_id):
            self.progress.duplicate += 1
            return False
        return candidate.title != "filtered"

    def save(self, candidate):
        inserted = 0 if identity_exists(self.conn, candidate.platform, candidate.source_job_id,
                                       legacy_job_id=candidate.storage_id) else self.conn.execute(
            "INSERT OR IGNORE INTO jobs (id,source_platform,source_job_id,title,company,city,url,jd,salary) "
            "VALUES (?, ?, ?, ?, 'Demo', '北京', '/job_detail/a.html', ?, '200元/天')",
            (candidate.storage_id, candidate.platform, candidate.source_job_id, candidate.title, candidate.jd),
        ).rowcount
        self.conn.commit()
        if inserted:
            self.new_job_ids.append(candidate.storage_id)
        else:
            self.progress.duplicate += 1
        return True


class Routes:
    def __init__(self):
        self.routes = {}

    def get(self, path):
        return self._register("GET", path)

    def post(self, path):
        return self._register("POST", path)

    def hook(self, name):
        return self._register('HOOK', name)

    def _register(self, method, path):
        def register(handler):
            self.routes[(method, path)] = handler
            return handler
        return register


def candidate(job_id, *, source_id=None, title="AI 产品实习", jd="新的职位描述"):
    return SimpleNamespace(storage_id=job_id, platform="boss", source_job_id=source_id or job_id,
                           title=title, jd=jd, company='Demo',
                           as_job_record=lambda: {'id': job_id, 'source_platform': 'boss', 'source_job_id': source_id or job_id,
                                                  'title': title, 'jd': jd, 'company': 'Demo', 'city': '北京', 'url': '/job_detail/a.html'})


@pytest.fixture
def runtime(tmp_path):
    conn = sqlite3.connect(tmp_path / "bosshunter.db")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, source_platform TEXT, source_job_id TEXT,
            title TEXT, company TEXT, city TEXT, url TEXT, jd TEXT, salary TEXT,
            deleted_at TEXT, ai_api_key TEXT
        );
        CREATE TABLE collection_runs (
            id TEXT PRIMARY KEY, task_id TEXT, status TEXT, platform_states_json TEXT,
            error TEXT, created_at TEXT, finished_at TEXT
        );
    """)
    config = {"platforms": {"boss": {"search": {"filters": {"job_type": ["实习"], "education": ["本科"]}}}},
              "ai": {"api_key": "private-value"}}
    state = {"active": None}
    writes = []
    starts = []
    checks = []

    def write_config(updated):
        config.clear()
        config.update(deepcopy(updated))
        writes.append(deepcopy(updated))

    def apply_preferences(saved, preferences):
        updated = deepcopy(saved)
        updated["platforms"]["boss"]["search"].update({
            "keywords": preferences["keywords"], "cities": preferences["cities"],
            "max_pages": preferences["max_pages"]["boss"],
        })
        return updated, []

    def start_task(mode, updated, *, before_start):
        assert mode == "collect"
        assert updated["_collection_options"]["auto_score"] is False
        assert updated["_agent_workflow"] is True
        assert writes == []
        before_start()
        assert config == {key: value for key, value in updated.items() if not key.startswith("_")}
        starts.append(deepcopy(updated))
        return {"id": "atomic-task", "mode": mode, "status": "running"}

    server = SimpleNamespace(
        DATA_DIR=tmp_path, CONFIG_PATH=tmp_path / "config.yaml", app=Routes(),
        request=SimpleNamespace(params={}, json=None),
        _json_response=lambda body, status_code=200: (body, status_code),
        load_config=lambda path: deepcopy(config), _write_config=write_config,
        classify_employment=lambda row: "internship" if "实习" in row["title"] else "unknown",
        job_mutation_lock=Lock(), task_runner=SimpleNamespace(status=lambda: deepcopy(state), start=start_task),
        apply_preferences=apply_preferences,
        normalize_collection_options=lambda config, options: {"platform_order": ["boss"], "auto_score": True},
        collect_preflight_checks=lambda mode, config, options: deepcopy(checks),
        _preflight_messages=lambda mode, config, options: [],
        error_messages=lambda checks: [item["message"] for item in checks if item["status"] == "error"],
        TaskAlreadyRunningError=RuntimeError, AgentRequestError=ValueError,
    )
    orchestrator = SimpleNamespace(_SharedProcessor=Processor,
                                   matching_deal_breaker=lambda value, rules: value == 'filtered',
                                   matching_blocked_company=lambda value, rules: value in rules)
    bridge.install_bridge(server, orchestrator, identity_exists)
    yield SimpleNamespace(conn=conn, server=server, orchestrator=orchestrator,
                          state=state, config=config, writes=writes, starts=starts, checks=checks)
    conn.close()


def call(runtime, method, path, *, body=None, params=None, args=()):
    runtime.server.request.json = body
    runtime.server.request.params = params or {}
    return runtime.server.app.routes[(method, path)](*args)


def run_row(runtime, run_id, task_id, status="completed"):
    runtime.conn.execute("INSERT INTO collection_runs VALUES (?, ?, ?, ?, '', ?, ?)",
                         (run_id, task_id, status, '{"boss":{"seen":1}}', "2026-10-08 01:00:00",
                          None if status == "running" else "2026-10-08 01:01:00"))
    runtime.conn.commit()


def processor(runtime, run_id="run-1"):
    return runtime.orchestrator._SharedProcessor(runtime.conn, None, run_id=run_id)


@pytest.mark.parametrize('host,origin,allowed', [
    ('127.0.0.1:8686',None,True), ('localhost:8686','http://localhost:8686',True),
    ('attacker.example:8686',None,False), ('127.0.0.1:8686','https://attacker.example',False),
])
def test_bridge_local_origin_guard(runtime, host, origin, allowed):
    runtime.server.request.path = '/api/radar/search/start'
    runtime.server.request.environ = {'REMOTE_ADDR':'127.0.0.1','HTTP_HOST':host,'wsgi.url_scheme':'http'}
    if origin:
        runtime.server.request.environ['HTTP_ORIGIN'] = origin
    runtime.server.HTTPResponse = lambda **kwargs: RuntimeError(kwargs['body'])
    guard = runtime.server.app.routes[('HOOK','before_request')]
    if allowed:
        guard()
    else:
        with pytest.raises(RuntimeError, match='local same-origin'):
            guard()


def test_known_identity_requires_fresh_detail_and_survives_reopen(runtime):
    runtime.conn.execute("INSERT INTO jobs VALUES (?, 'boss', ?, ?, 'Old company', '上海', ?, ?, ?, NULL, ?)",
                         ("canonical-id", "source-id", "AI 产品实习", "/job_detail/old.html", "缓存 JD", "150元/天", "secret"))
    runtime.conn.commit()
    run_row(runtime, "run-1", "task-1", "stopped")
    observed = processor(runtime)
    job = candidate('different-storage-id', source_id='source-id', jd='本次重新读取的 JD')
    assert observed.inspect(job) is True
    before, _ = call(runtime, "GET", "/api/radar/tasks/<task_id>/results", args=("task-1",))
    assert before['total'] == 0
    assert observed.save(job) is True
    body, status = call(runtime, "GET", "/api/radar/tasks/<task_id>/results", args=("task-1",))
    assert status == 200 and body["status"] == "stopped" and body["total"] == 1
    assert body["items"][0]["id"] == "canonical-id"
    assert body["items"][0]["jd"] == '本次重新读取的 JD'
    assert body["items"][0]["reused_snapshot"] is False
    assert body["items"][0]["observed_at"].endswith("+00:00")
    assert "secret" not in json.dumps(body)
    with sqlite3.connect(runtime.server.DATA_DIR / "radar-search-results.sqlite") as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM matched_jobs").fetchone()[0] == 1
    assert runtime.conn.execute("SELECT jd FROM jobs WHERE id='canonical-id'").fetchone()[0] == '缓存 JD'


def test_new_save_then_duplicate_retains_fresh_snapshot(runtime):
    run_row(runtime, "run-1", "task-1")
    observed = processor(runtime)
    job = candidate("new-job")
    assert observed.inspect(job) is True
    assert observed.save(job) is True
    assert observed.inspect(job) is False
    body, _ = call(runtime, "GET", "/api/radar/tasks/<task_id>/results", args=("task-1",))
    assert body["total"] == 1
    assert body["items"][0]["reused_snapshot"] is False
    assert body["items"][0]["jd"] == "新的职位描述"


def test_soft_deleted_and_filtered_candidates_are_excluded(runtime):
    runtime.conn.execute("INSERT INTO jobs (id,source_platform,source_job_id,title,deleted_at) VALUES ('deleted','boss','deleted','实习','yesterday')")
    runtime.conn.commit()
    run_row(runtime, "run-1", "task-1")
    observed = processor(runtime)
    assert observed.inspect(candidate("deleted")) is False
    assert observed.inspect(candidate("blocked", title="filtered")) is False
    body, _ = call(runtime, "GET", "/api/radar/tasks/<task_id>/results", args=("task-1",))
    assert body["total"] == 0


def test_result_task_isolation_pagination_and_pending(runtime):
    run_row(runtime, "run-1", "task-1", "completed_with_errors")
    run_row(runtime, "run-2", "task-2", "running")
    for job_id in ("a", "b", "c"):
        processor(runtime).save(candidate(job_id))
    processor(runtime, "run-2").save(candidate("other"))
    body, _ = call(runtime, "GET", "/api/radar/tasks/<task_id>/results",
                   args=("task-1",), params={"limit": "1", "offset": "1"})
    assert body["total"] == 3 and [row["id"] for row in body["items"]] == ["b"]
    assert body["status"] == "completed_with_errors" and body["run_id"] == "run-1"
    runtime.state["active"] = {"id": "not-yet-created", "status": "running"}
    body, _ = call(runtime, "GET", "/api/radar/tasks/<task_id>/results", args=("not-yet-created",))
    assert body["status"] == "running" and body["items"] == []
    body, _ = call(runtime, "GET", "/api/radar/tasks/<task_id>/results", args=("unknown-after-restart",))
    assert body["status"] == "failed" and body["error"]
    runtime.state["last_task"] = {"id": "failed-before-run", "status": "failed", "error": "browser unavailable"}
    body, _ = call(runtime, "GET", "/api/radar/tasks/<task_id>/results", args=("failed-before-run",))
    assert body["status"] == "failed" and body["error"] == "browser unavailable"
    for params in ({"limit": "0"}, {"limit": "501"}, {"offset": "-1"}, {"limit": "x"}):
        _, status = call(runtime, "GET", "/api/radar/tasks/<task_id>/results", args=("task-1",), params=params)
        assert status == 400


def test_search_options_preview_confirmation_and_preservation(runtime):
    assert call(runtime, "GET", "/api/radar/capabilities")[0] == {"version": 1, "matched_results": True, "fresh_details": True}
    assert call(runtime, "GET", "/api/radar/search-options")[0] == {"internship_only": True}
    body, status = call(runtime, "POST", "/api/radar/search-options/preview", body={"internship_only": False})
    assert status == 200 and body["requires_confirmation"] is True and not runtime.writes
    assert call(runtime, "POST", "/api/radar/search-options/apply", body={"internship_only": False})[1] == 400
    body, status = call(runtime, "POST", "/api/radar/search-options/apply", body={"internship_only": False, "confirmed": True})
    assert status == 200 and body["requires_confirmation"] is False
    filters = runtime.config["platforms"]["boss"]["search"]["filters"]
    assert filters == {"education": ["本科"]}
    assert runtime.config["ai"] == {"api_key": "private-value"}
    call(runtime, "POST", "/api/radar/search-options/apply", body={"internship_only": True, "confirmed": True})
    assert filters is not runtime.config["platforms"]["boss"]["search"]["filters"]
    assert call(runtime, "GET", "/api/radar/search-options")[0]["internship_only"] is True


@pytest.mark.parametrize("body", [{}, {"internship_only": 1}, {"internship_only": "true"},
                                   {"internship_only": True, "api_key": "injected"}])
def test_options_strict_schema(runtime, body):
    assert call(runtime, "POST", "/api/radar/search-options/preview", body=body)[1] == 400
    assert not runtime.writes


def test_options_protect_active_task(runtime):
    runtime.state["active"] = {"id": "active", "status": "stopping"}
    for suffix, body in (("preview", {"internship_only": False}),
                         ("apply", {"internship_only": False, "confirmed": True})):
        assert call(runtime, "POST", "/api/radar/search-options/" + suffix, body=body)[1] == 409
    assert not runtime.writes


def test_compatibility_error_is_explicit(runtime):
    class Incompatible:
        def inspect(self, unexpected, option):
            pass
    with pytest.raises(RuntimeError, match="Incompatible BossHunter processor"):
        bridge.install_bridge(runtime.server, SimpleNamespace(_SharedProcessor=Incompatible), identity_exists)


def search_body():
    return {"preferences": {"keywords": ["AI 产品 实习"], "cities": ["上海"],
                            "max_pages": {"boss": 2}, "platform_order": ["boss"],
                            "allow_internship": True, "recruitment_type": "both"},
            "internship_only": False, "confirmed": True}


def test_atomic_start_persists_preferences_before_registering_task(runtime):
    body, status = call(runtime, "POST", "/api/radar/search/start", body=search_body())
    assert status == 200 and body["task"]["id"] == "atomic-task"
    assert len(runtime.starts) == 1 and len(runtime.writes) == 1
    saved_search = runtime.config["platforms"]["boss"]["search"]
    assert saved_search["cities"] == ["上海"] and saved_search["max_pages"] == 2
    assert "job_type" not in saved_search["filters"]
    assert saved_search["filters"]["education"] == ["本科"]
    assert body["policy"] == {"auto_score": False, "delivery": "disabled"}


def test_atomic_start_preflight_refusal_and_active_leave_config_untouched(runtime):
    previous = deepcopy(runtime.config)
    runtime.checks.append({"status": "error", "message": "Chrome disconnected"})
    body, status = call(runtime, "POST", "/api/radar/search/start", body=search_body())
    assert status == 400 and body["messages"] == ["Chrome disconnected"]
    assert runtime.config == previous and not runtime.writes and not runtime.starts
    runtime.state["active"] = {"id": "other", "status": "running"}
    assert call(runtime, "POST", "/api/radar/search/start", body=search_body())[1] == 409
    assert runtime.config == previous and not runtime.writes and not runtime.starts


@pytest.mark.parametrize("change", [
    {"confirmed": False}, {"internship_only": 1}, {"unexpected": "private"},
    {"preferences": {"keywords": [], "cities": ["上海"], "max_pages": {"boss": 1}}},
    {"preferences": {"keywords": ["AI"], "cities": ["上海"], "max_pages": {"boss": True}}},
    {"preferences": {"keywords": ["AI"], "cities": ["上海"], "max_pages": {"boss": 4}}},
    {"preferences": {"keywords": ["AI"], "cities": ["上海"], "max_pages": {"boss": 1}, "platform_order": ["zhilian"]}},
    {"preferences": {"keywords": ["AI"], "cities": ["上海"], "max_pages": {"boss": 1}, "api_key": "inject"}},
])
def test_atomic_start_rejects_unsafe_or_invalid_inputs_before_write(runtime, change):
    assert call(runtime, "POST", "/api/radar/search/start", body={**search_body(), **change})[1] == 400
    assert not runtime.writes and not runtime.starts
