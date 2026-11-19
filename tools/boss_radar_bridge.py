#!/usr/bin/env python3
"""Run BossHunter with an owned, read-only record of exact search matches.

Run using BossHunter's virtual environment. No upstream source or job records
are changed. Every run reads details again, including already saved identities.
"""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import sqlite3
from ipaddress import ip_address
from urllib.parse import urlsplit


@contextmanager
def _sidecar(server):
    conn = sqlite3.connect(server.DATA_DIR / "radar-search-results.sqlite", timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def install_bridge(server, orchestrator, job_identity_exists) -> None:
    """Install the narrowly scoped extension after resolving the runtime folder."""
    original = orchestrator._SharedProcessor
    @server.app.hook('before_request')
    def local_requests_only():
        if not server.request.path.startswith('/api/radar/'):
            return
        environment = server.request.environ
        host = environment.get('HTTP_HOST', '')
        try:
            peer = ip_address(environment.get('REMOTE_ADDR', ''))
            authority = urlsplit('http://' + host)
            hostname = authority.hostname or ''
            local_host = hostname == 'localhost' or ip_address(hostname).is_loopback
            valid = peer.is_loopback and local_host and not any((authority.username, authority.password, authority.path, authority.query, authority.fragment))
            _ = authority.port
            origin = environment.get('HTTP_ORIGIN')
            valid = valid and (origin is None or origin == f"{environment.get('wsgi.url_scheme', 'http')}://{host}")
        except ValueError:
            valid = False
        if not valid:
            raise server.HTTPResponse(status=403, body='Radar bridge accepts local same-origin requests only')
    for name in ("inspect", "save"):
        if list(inspect.signature(getattr(original, name)).parameters) != ["self", "candidate"]:
            raise RuntimeError(f"Incompatible BossHunter processor: {name} signature changed")
    required_init = {"conn", "request", "run_id", "stop_event", "config", "emit"}
    if not required_init <= set(inspect.signature(original.__init__).parameters):
        raise RuntimeError("Incompatible BossHunter processor: initialization contract changed")
    if not all(callable(getattr(orchestrator, name, None)) for name in
               ('matching_deal_breaker', 'matching_blocked_company')):
        raise RuntimeError("Incompatible BossHunter processor: collection filters are missing")
    if not all(callable(getattr(server, name, None)) for name in (
        "_json_response", "load_config", "_write_config", "classify_employment",
        "apply_preferences", "normalize_collection_options", "collect_preflight_checks",
        "_preflight_messages", "error_messages",
    )):
        raise RuntimeError("Incompatible BossHunter web API; required helpers are missing")
    with _sidecar(server) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS matched_jobs (
            run_id TEXT NOT NULL, job_id TEXT NOT NULL,
            snapshot_json TEXT NOT NULL, observed_at TEXT NOT NULL,
            PRIMARY KEY (run_id, job_id)
        )""")

    def active_row(processor, candidate):
        if not job_identity_exists(
            processor.conn, candidate.platform, candidate.source_job_id,
            legacy_job_id=candidate.storage_id,
        ):
            return None
        # Identity lookup must match upstream's canonical pair and legacy fallback.
        row = processor.conn.execute(
            "SELECT * FROM jobs WHERE source_platform = ? AND source_job_id = ? LIMIT 1",
            (candidate.platform, candidate.source_job_id),
        ).fetchone()
        if row is None and candidate.platform == "boss":
            row = processor.conn.execute(
                "SELECT * FROM jobs WHERE id = ? LIMIT 1", (candidate.storage_id,),
            ).fetchone()
        return dict(row) if row is not None and not dict(row).get("deleted_at") else None

    def record(processor, row, reused):
        fields = (
            "id", "title", "company", "city", "url", "jd", "salary",
            "source_platform", "source_job_id", "experience", "education",
            "duration", "onsite_days", "internship_duration", "weekly_days",
        )
        snapshot = {key: row[key] for key in fields if key in row}
        snapshot["employment_type"] = server.classify_employment(row)
        snapshot["reused_snapshot"] = bool(reused)
        observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snapshot["observed_at"] = observed_at
        with _sidecar(server) as conn:
            # A new save followed by another keyword's duplicate retains fresh-JD status.
            conn.execute(
                "INSERT OR IGNORE INTO matched_jobs VALUES (?, ?, ?, ?)",
                (processor.run_id, str(row["id"]), json.dumps(snapshot, ensure_ascii=False), observed_at),
            )

    class MatchedProcessor(original):
        def inspect(self, candidate):
            # Deduplicate only inside this run. A saved identity from a previous
            # run is not evidence that its source page is still recruiting.
            seen = self.__dict__.setdefault('_radar_seen', set())
            key = (candidate.platform, candidate.source_job_id)
            if key in seen:
                self.progress.seen += 1
                self.progress.duplicate += 1
                self.event()
                return False
            seen.add(key)
            row = active_row(self, candidate)
            if row is None:
                return super().inspect(candidate)
            # Retain the upstream title/company filters while bypassing only
            # the persisted-identity shortcut that prevents a new detail visit.
            self.progress.seen += 1
            profile = self.config.get('profile', {}) if isinstance(self.config.get('profile'), dict) else {}
            if (orchestrator.matching_deal_breaker(candidate.title, profile.get('deal_breakers') or [])
                    or orchestrator.matching_blocked_company(candidate.company, profile.get('blocked_companies') or [])):
                self.progress.filtered += 1
                self.event(message='职位名或公司命中过滤规则')
                return False
            self.event()
            return True

        def save(self, candidate):
            previous_ids = len(self.new_job_ids)
            previous_duplicates = self.progress.duplicate
            keep_collecting = super().save(candidate)
            inserted = len(self.new_job_ids) > previous_ids
            if inserted or self.progress.duplicate > previous_duplicates:
                row = active_row(self, candidate)
                if row is not None:
                    # save() follows a successful live detail read. Upstream's
                    # insert-if-new leaves its stored JD unchanged for a known
                    # identity, so export this candidate rather than that JD.
                    fresh = {**row, **candidate.as_job_record(), 'id': row['id']}
                    record(self, fresh, False)
            return keep_collecting

    orchestrator._SharedProcessor = MatchedProcessor

    @server.app.get("/api/radar/capabilities")
    def capabilities():
        return server._json_response({"version": 1, "matched_results": True, "fresh_details": True})

    @server.app.get("/api/radar/tasks/<task_id>/results")
    def results(task_id):
        try:
            limit = int(server.request.params.get("limit", 100))
            offset = int(server.request.params.get("offset", 0))
            if not 1 <= limit <= 500 or offset < 0:
                raise ValueError("Invalid result pagination")
        except (ValueError, TypeError) as exc:
            return server._json_response({"error": str(exc)}, 400)
        with closing(sqlite3.connect(server.DATA_DIR / "bosshunter.db", timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            run = conn.execute(
                "SELECT * FROM collection_runs WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if run is None:
            state = server.task_runner.status()
            candidates = [state.get("active"), state.get("last_task"), *state.get("tasks", [])]
            task = next((item for item in candidates if isinstance(item, dict) and item.get("id") == task_id), None)
            status = task.get("status", "failed") if task else "failed"
            error = task.get("error") if task else "采集任务记录不存在，可能服务已重启。请重新检索；不会自动重试启动任务。"
            return server._json_response({
                "task_id": task_id, "run_id": None, "status": status,
                "total": 0, "items": [], "platform_state": {}, "error": error,
                "started_at": task.get("created_at") if task else None,
                "finished_at": task.get("updated_at") if task and status in {"failed", "stopped", "completed"} else None,
            })
        with _sidecar(server) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM matched_jobs WHERE run_id = ?", (run["id"],),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT snapshot_json FROM matched_jobs WHERE run_id = ? "
                "ORDER BY observed_at, rowid LIMIT ? OFFSET ?",
                (run["id"], limit, offset),
            ).fetchall()
        return server._json_response({
            "task_id": task_id, "run_id": run["id"], "status": run["status"],
            "total": total, "items": [json.loads(row[0]) for row in rows],
            "platform_state": json.loads(run["platform_states_json"] or "{}"),
            "error": run["error"] or None, "started_at": run["created_at"],
            "finished_at": run["finished_at"],
        })

    def internship_options(config):
        filters = config.get("platforms", {}).get("boss", {}).get("search", {}).get("filters", {})
        return {"internship_only": filters.get("job_type") == ["实习"]}

    @server.app.get("/api/radar/search-options")
    def search_options():
        return server._json_response(internship_options(server.load_config(server.CONFIG_PATH)))

    def change_options(apply):
        body = server.request.json
        allowed = {"internship_only", "confirmed"} if apply else {"internship_only"}
        if not isinstance(body, dict) or set(body) - allowed or type(body.get("internship_only")) is not bool:
            return server._json_response({"error": "Only internship_only boolean is accepted"}, 400)
        if apply and body.get("confirmed") is not True:
            return server._json_response({"error": "Confirmation is required", "requires_confirmation": True}, 400)
        with server.job_mutation_lock:
            if server.task_runner.status().get("active") is not None:
                return server._json_response({"error": "Stop or finish the active task before changing search filters"}, 409)
            config = server.load_config(server.CONFIG_PATH)
            previous = internship_options(config)
            updated = deepcopy(config)
            filters = updated.setdefault("platforms", {}).setdefault("boss", {}).setdefault("search", {}).setdefault("filters", {})
            if body["internship_only"]:
                filters["job_type"] = ["实习"]
            else:
                filters.pop("job_type", None)
            if apply:
                server._write_config(updated)
            return server._json_response({
                "internship_only": body["internship_only"], "previous": previous,
                "changes": [] if previous["internship_only"] == body["internship_only"] else [{
                    "field": "internship_only", "before": previous["internship_only"],
                    "after": body["internship_only"],
                }], "requires_confirmation": not apply,
            })

    @server.app.post("/api/radar/search-options/preview")
    def preview_options():
        return change_options(False)

    @server.app.post("/api/radar/search-options/apply")
    def apply_options():
        return change_options(True)

    @server.app.post("/api/radar/search/start")
    def start_search():
        body = server.request.json
        if not isinstance(body, dict) or set(body) - {"preferences", "internship_only", "confirmed"}:
            return server._json_response({"error": "Only preferences, internship_only and confirmed are accepted"}, 400)
        if body.get("confirmed") is not True:
            return server._json_response({"error": "Confirmation is required", "requires_confirmation": True}, 400)
        preferences = body.get("preferences")
        allowed = {"keywords", "cities", "platform_order", "allow_internship", "recruitment_type", "max_pages"}
        if not isinstance(preferences, dict) or set(preferences) - allowed or type(body.get("internship_only")) is not bool:
            return server._json_response({"error": "Invalid narrow search preferences"}, 400)
        for key in ("keywords", "cities"):
            values = preferences.get(key)
            if not isinstance(values, list) or not 1 <= len(values) <= 30 or any(
                not isinstance(value, str) or not value.strip() or len(value) > 300 for value in values
            ):
                return server._json_response({"error": f"{key} must be a nonempty string list"}, 400)
        pages = preferences.get("max_pages")
        if not isinstance(pages, dict) or set(pages) != {"boss"} or type(pages["boss"]) is not int or not 1 <= pages["boss"] <= 3:
            return server._json_response({"error": "BOSS max_pages must be an integer from 1 to 3"}, 400)
        if (preferences.get("platform_order", ["boss"]) != ["boss"]
                or preferences.get("allow_internship", True) is not True
                or preferences.get("recruitment_type", "both") != "both"):
            return server._json_response({"error": "Only BOSS collection with internship support is accepted"}, 400)
        preferences = {**preferences, "platform_order": ["boss"], "allow_internship": True, "recruitment_type": "both"}
        try:
            with server.job_mutation_lock:
                if server.task_runner.status().get("active") is not None:
                    return server._json_response({"error": "Another task is active"}, 409)
                updated, changes = server.apply_preferences(server.load_config(server.CONFIG_PATH), preferences)
                filters = updated.setdefault("platforms", {}).setdefault("boss", {}).setdefault("search", {}).setdefault("filters", {})
                if body["internship_only"]:
                    filters["job_type"] = ["实习"]
                else:
                    filters.pop("job_type", None)
                options = server.normalize_collection_options(updated, None)
                options["auto_score"] = False
                checks = server.collect_preflight_checks("collect", updated, options)
                messages = [*server._preflight_messages("collect", updated, options), *server.error_messages(checks)]
                if messages:
                    return server._json_response({"error": "请先处理启动前检查", "messages": messages, "checks": checks}, 400)
                task = server.task_runner.start(
                    "collect", {**updated, "_collection_options": options, "_agent_workflow": True},
                    before_start=lambda: server._write_config(updated),
                )
            return server._json_response({
                "task": task, "checks": checks, "changes": changes,
                "preferences": preferences, "internship_only": body["internship_only"],
                "policy": {"auto_score": False, "delivery": "disabled"},
            })
        except server.TaskAlreadyRunningError as exc:
            return server._json_response({"error": str(exc)}, 409)
        except (server.AgentRequestError, ValueError) as exc:
            return server._json_response({"error": str(exc)}, 400)

    # BossHunter's SPA registers a broad ``<filepath:path>`` GET route before
    # this bridge is installed. Bottle evaluates dynamic routes in registration
    # order, so that fallback otherwise captures /api/radar/tasks/{id}/results
    # and returns a misleading 404. Keep narrow bridge routes ahead of it.
    router = getattr(server.app, "router", None)
    if router is not None and hasattr(router, "dyna_routes") and hasattr(router, "_compile"):
        for method, routes in router.dyna_routes.items():
            routes.sort(key=lambda route: len(route[1]), reverse=True)
            router._groups = {
                (flatpat, route_method): index
                for route_method, method_routes in router.dyna_routes.items()
                for index, (_, flatpat, _, _) in enumerate(method_routes)
            }
            router._compile(method)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", default=8686, type=int)
    args = parser.parse_args()
    config_path = args.config.resolve()
    if not config_path.is_file():
        parser.error("BossHunter config file does not exist")
    try:
        from bosshunter.web import server
        from bosshunter.collection import orchestrator
        from bosshunter.db import job_identity_exists
        from bosshunter.browser import configure
    except (ImportError, AttributeError) as exc:
        parser.error(f"BossHunter runtime is incompatible; use its installed virtual environment: {exc}")
    os.chdir(config_path.parent)
    server.set_base_dir(config_path.parent)
    server.CONFIG_PATH = config_path
    configure(server.load_config(config_path))
    try:
        install_bridge(server, orchestrator, job_identity_exists)
    except (AttributeError, TypeError, RuntimeError) as exc:
        parser.error(f"BossHunter runtime is incompatible: {exc}")
    server.run_server(host="127.0.0.1", port=args.port, open_browser=False)


if __name__ == "__main__":
    main()
