"""Native export storage: portable local files with no silent overwrite."""
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from export_storage import ExportManager, default_export_folder, safe_filename
from fe_helpers import frontend_source


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # Keep user configuration and exported files inside the disposable temp tree.
    monkeypatch.setattr("export_storage._downloads_known_folder", lambda: tmp_path / "Downloads")
    return ExportManager(tmp_path / "LocalAppData", "http://127.0.0.1:9")


def test_native_exports_save_in_user_visible_downloads_with_suffix_for_duplicates(manager):
    first = manager.save_text("requirements.json", '{"mode":"compact"}')
    second = manager.save_text("requirements.json", '{"mode":"full"}')
    assert Path(first["path"]).read_text(encoding="utf-8") == '{"mode":"compact"}'
    assert Path(second["path"]).read_text(encoding="utf-8") == '{"mode":"full"}'
    assert first["path"] != second["path"]
    assert Path(second["path"]).name == "requirements (2).json"
    assert Path(first["path"]).parent.name == "择木"
    state = manager.get_state()
    assert len(state["recent"]) == 2
    assert state["recent"][0]["name"] == "requirements (2).json"
    assert state["recent"][0]["exists"]


def test_existing_export_folder_is_reused_after_rename(tmp_path, monkeypatch):
    monkeypatch.setattr("export_storage._downloads_known_folder", lambda: tmp_path)
    legacy = tmp_path / "AI实习雷达"
    legacy.mkdir()
    assert default_export_folder() == legacy
    previous = tmp_path / "实习雷达"
    previous.mkdir()
    assert default_export_folder() == previous
    current = tmp_path / "择木"
    current.mkdir()
    assert default_export_folder() == current


def test_history_persists_across_restart_and_deleted_files_are_detected(manager):
    item = manager.save_text("analysis.md", "中文分析说明")
    same_user = ExportManager(manager.user_dir, manager.origin)
    assert same_user.get_state()["recent"][0]["id"] == item["id"]
    Path(item["path"]).unlink()
    assert same_user.get_state()["recent"][0]["exists"] is False
    with pytest.raises(FileNotFoundError, match="移动或删除"):
        same_user.reveal_file(item["id"])
    with pytest.raises(ValueError, match="导出记录"):
        same_user.reveal_file("not-a-real-id")


@pytest.mark.parametrize("name", [
    "../evil.json", "C:\\Users\\bad.json", "sub/file.json",
    "run.exe", "bad:name.json", "nul\x00.json", "a" * 181 + ".json",
])
def test_invalid_filenames_never_write_any_files(manager, name):
    with pytest.raises(ValueError):
        manager.save_text(name, "data")
    assert manager.get_state()["recent"] == []


def test_existing_filename_must_not_be_clobbered(manager):
    folder = Path(manager.get_state()["folder"])
    folder.mkdir(parents=True)
    existing = folder / "test.zip"
    existing.write_bytes(b"unrelated existing file")
    saved = manager.save_text("test.zip", "new content")
    assert existing.read_bytes() == b"unrelated existing file"
    assert Path(saved["path"]).name == "test (2).zip"


def test_cannot_read_unapproved_endpoint_or_external_address(manager):
    for route, body in [
        ("https://attacker.example/x", None),
        ("/api/jobs", None),
        ("/api/delete", "{}"),
        ("/api/backup/sqlite?token=unsafe", None),
        ("/static/other-file.json", None),
    ]:
        with pytest.raises(ValueError):
            manager.save_endpoint(route, body)
    assert manager.get_state()["recent"] == []


def test_save_endpoint_streams_actual_file_and_rejects_bad_requests(manager):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/api/backup/sqlite":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Disposition",
                             'attachment; filename="ai-internship-radar-backup.sqlite"')
            self.end_headers()
            self.wfile.write(b"sqlite-backup-data")
        def do_POST(self):
            if self.path != "/api/exports/jobs":
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            assert json.loads(body)["ids"] == [42]
            self.send_response(200)
            self.send_header("Content-Disposition", 'attachment; filename="jobs.zip"')
            self.end_headers()
            self.wfile.write(b"PK-mock-payload")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        manager.origin = "http://127.0.0.1:" + str(server.server_port)
        saved = manager.save_endpoint("/api/backup/sqlite")
        assert Path(saved["path"]).read_bytes() == b"sqlite-backup-data"
        archived = manager.save_endpoint("/api/exports/jobs", '{"ids":[42]}')
        assert Path(archived["path"]).name == "jobs.zip"
        assert Path(archived["path"]).read_bytes() == b"PK-mock-payload"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_change_folder_uses_native_dialog_and_preserves_past_history(manager, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    alternative = tmp_path / "custom"
    alternative.mkdir()
    window = SimpleNamespace(create_file_dialog=lambda _: (str(alternative),))
    fake = SimpleNamespace(windows=[window], FOLDER_DIALOG="folder")
    monkeypatch.setitem(sys.modules, "webview", fake)
    original = manager.save_text("original.json", "{}")
    changed = manager.change_folder()
    assert changed["folder"] == str(alternative)
    new = manager.save_text("other.json", '{"new":true}')
    assert Path(new["path"]).parent == alternative
    assert Path(original["path"]).is_file()
    assert ExportManager(manager.user_dir, manager.origin).get_state()["folder"] == str(alternative)
    monkeypatch.setattr(window, "create_file_dialog", lambda _: None)
    assert manager.change_folder()["folder"] == str(alternative)


def test_frontend_wiring_includes_every_existing_download_family():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    script = frontend_source()
    manager = (root / "static" / "export-manager.js").read_text(encoding="utf-8")
    desktop = (root / "desktop.py").read_text(encoding="utf-8")
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    for part in ("exportSelected(kind)", "exportCareerPackage()",
                 "downloadDemandStats()", "downloadAnalysis(type)",
                 "downloadRequirementPrompt()", "downloadCareerPrompt()",
                 "exportManagerPanel()"):
        assert part in script
    assert "saveExportRoute(" in script and "saveExportText(" in script
    assert "a[download]" in manager
    assert "js_api=manager" in desktop
    assert "static/export-manager.js" in html
    assert "export-feedback" in html
