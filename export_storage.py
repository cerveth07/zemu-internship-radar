"""Native-only export folder management for the pywebview desktop shell.

Browser access cannot invoke these methods. Every remote export uses a fixed,
allowlisted localhost endpoint; frontend JS never supplies filesystem paths.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

GET_ROUTES = frozenset({
    "/api/backup/sqlite", "/api/export", "/api/capture/backup",
    "/api/support/diagnostics",
    "/api/company-lists/builtin.csv", "/api/company-lists/template.csv",
    "/static/templates/job-import-example.csv",
    "/static/templates/ai-keywords-v1.json",
})
POST_ROUTES = frozenset({
    "/api/exports/jobs", "/api/exports/companies",
    "/api/career-analysis/export", "/api/ai-review/export",
})
MAX_BYTES = 512 * 1024 * 1024
MAX_LOCAL_TEXT_BYTES = 24 * 1024 * 1024
FILE_EXTENSIONS = frozenset({".json", ".zip", ".md", ".csv", ".sqlite"})
HISTORY_LIMIT = 30


def _downloads_known_folder():
    """Windows known folder respects OneDrive relocation and user customization."""
    if os.name != "nt":
        return Path.home() / "Downloads"
    from ctypes import wintypes
    # FOLDERID_Downloads 374DE290-123F-4565-9164-39C4925E467B
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
        ]
    guid = GUID(0x374DE290, 0x123F, 0x4565,
                (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
    value = ctypes.c_wchar_p()
    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(value))
        if result == 0 and value.value:
            return Path(value.value)
    except (OSError, AttributeError, ValueError):
        pass
    finally:
        if value.value:
            ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))
    return Path.home() / "Downloads"


def default_export_folder():
    downloads = _downloads_known_folder()
    for name in ("择木", "实习雷达", "AI实习雷达"):
        folder = downloads / name
        if folder.is_dir():
            return folder
    return downloads / "择木"


def safe_filename(name):
    """Allow a plain download filename, never a path or executable extension."""
    if not isinstance(name, str):
        raise ValueError("导出文件名必须是文本")
    name = name.strip()
    if (not name or len(name) > 180 or name in (".", "..")
            or "/" in name or "\\" in name or
            any(ord(c) < 32 or c in '<>:"|?*' for c in name)
            or Path(name).suffix.lower() not in FILE_EXTENSIONS):
        raise ValueError("不允许的导出文件名或类型")
    base = name.split(".", 1)[0].upper()
    if base in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(COM|LPT)[1-9]", base):
        raise ValueError("Windows 系统保留文件名不可用于导出")
    if name.endswith(" ") or name.endswith("."):
        raise ValueError("导出文件名不能以空格或句点结尾")
    return name


def _filename_from_response(header, fallback):
    match = re.search(r'filename="([^"]+)"', header or "", flags=re.IGNORECASE)
    return safe_filename(match.group(1)) if match else safe_filename(fallback)


def _write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".radar-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class ExportManager:
    def __init__(self, user_dir: Path, origin: str):
        self.user_dir = Path(user_dir)
        self.origin = origin.rstrip("/")
        self.lock = threading.RLock()
        self.settings_path = self.user_dir / "export-settings.json"
        self.history_path = self.user_dir / "export-history.json"
        self._folder = self._read_folder()
        self._history = self._read_history()

    def _read_folder(self):
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            folder = Path(settings["folder"]).expanduser()
            if folder.is_absolute():
                return folder
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return default_export_folder()

    def _read_history(self):
        try:
            value = json.loads(self.history_path.read_text(encoding="utf-8"))
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)
                        and isinstance(x.get("id"), str)
                        and isinstance(x.get("path"), str)
                        and isinstance(x.get("name"), str)
                        and isinstance(x.get("saved_at"), str)
                        and isinstance(x.get("size"), int)][:HISTORY_LIMIT]
        except (OSError, ValueError, TypeError):
            pass
        return []

    def get_state(self):
        with self.lock:
            return {
                "desktop": True,
                "folder": str(self._folder),
                "recent": [{**x, "exists": Path(x["path"]).is_file()}
                           for x in self._history[:10]],
            }

    def change_folder(self):
        """User-selected native folder dialog; cancellation leaves setting unchanged."""
        import webview
        windows = getattr(webview, "windows", [])
        if not windows:
            raise RuntimeError("窗口尚未就绪")
        chosen = windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        if not chosen:
            return self.get_state()
        path = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("请选择存在的文件夹")
        with self.lock:
            _write_json_atomic(self.settings_path, {"folder": str(path)})
            self._folder = path
            return self.get_state()

    def open_folder(self):
        with self.lock:
            path = self._folder
            path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)
        return {"ok": True, "folder": str(path)}

    @staticmethod
    def _open_path(path):
        if os.name == "nt":
            os.startfile(str(path))
        else:
            raise RuntimeError("仅 Windows 桌面版支持在资源管理器中打开")

    def reveal_file(self, file_id):
        with self.lock:
            record = next((x for x in self._history if x["id"] == file_id), None)
        if record is None:
            raise ValueError("找不到此导出记录")
        path = Path(record["path"])
        if not path.is_file():
            raise FileNotFoundError("文件已移动或删除，请检查原保存目录")
        if os.name != "nt":
            raise RuntimeError("仅 Windows 桌面版支持定位文件")
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
        return {"ok": True, "path": str(path)}

    def _record(self, path, size):
        entry = {
            "id": uuid.uuid4().hex, "name": path.name, "path": str(path),
            "folder": str(path.parent), "size": size,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        self._history.insert(0, entry)
        self._history = self._history[:HISTORY_LIMIT]
        _write_json_atomic(self.history_path, self._history)
        return {**entry, "exists": True, "desktop": True}

    def _save_stream(self, filename, stream, limit):
        filename = safe_filename(filename)
        with self.lock:
            self._folder.mkdir(parents=True, exist_ok=True)
            directory = self._folder
            original = Path(filename)
            destination = None
            for number in range(1, 10001):
                choice = directory / (filename if number == 1 else
                                      f"{original.stem} ({number}){original.suffix}")
                try:
                    output = choice.open("xb")
                    destination = choice
                    break
                except FileExistsError:
                    continue
            if destination is None:
                raise FileExistsError("同名文件过多，请更改导出文件夹")
            size = 0
            try:
                with output:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > limit:
                            raise ValueError("导出文件超过安全大小限制")
                        output.write(chunk)
                return self._record(destination, size)
            except Exception:
                destination.unlink(missing_ok=True)
                raise

    def save_text(self, filename, content):
        from io import BytesIO
        if not isinstance(content, str):
            raise ValueError("不支持的文本导出内容")
        data = content.encode("utf-8")
        if len(data) > MAX_LOCAL_TEXT_BYTES:
            raise ValueError("本地文本导出超过 24 MB")
        return self._save_stream(filename, BytesIO(data), MAX_LOCAL_TEXT_BYTES)

    def save_endpoint(self, route, payload=None):
        """Only these explicit URLs can be fetched; not an SSRF/open proxy API."""
        if not isinstance(route, str) or not route.startswith("/"):
            raise ValueError("不支持的导出地址")
        if payload is None:
            if route not in GET_ROUTES:
                raise ValueError("不支持的 GET 导出路径")
            request = urllib.request.Request(self.origin + route, method="GET")
        else:
            if route not in POST_ROUTES or not isinstance(payload, str):
                raise ValueError("不支持的 POST 导出路径")
            body = payload.encode("utf-8")
            if len(body) > 2 * 1024 * 1024:
                raise ValueError("导出请求过大")
            json.loads(payload)
            request = urllib.request.Request(
                self.origin + route, data=body, method="POST",
                headers={"Content-Type": "application/json"})
        # Local backend is only reachable over loopback and origin is chosen by
        # the desktop launcher, not by JavaScript or website content.
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with client.open(request, timeout=120) as response:
                fallback = "radar-export.json"
                if route == "/api/backup/sqlite":
                    fallback = "zemu-backup.sqlite"
                elif route == "/api/capture/backup":
                    fallback = "radar-capture-backup.zip"
                elif route.endswith(".csv"):
                    fallback = "job-import-example.csv"
                elif route.endswith(".json") and route.startswith("/static/"):
                    fallback = Path(route).name
                filename = _filename_from_response(
                    response.headers.get("Content-Disposition"), fallback)
                return self._save_stream(filename, response, MAX_BYTES)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except (ValueError, AttributeError):
                pass
            raise RuntimeError(str(detail)) from exc
