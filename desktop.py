"""Native Windows launcher for Zemu.

The packaged application serves the existing FastAPI UI only on localhost and
hosts it in WebView2. The SQLite database lives outside the executable so it
survives one-file extraction, upgrades, and moving the EXE.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

from app import VERSION

PRODUCT = 'Zemu'
APP_TITLE = f'择木 · 实习工作台 v{VERSION}'
DATA_DIRECTORY_NAME = 'InternshipRadarPrivateRC'


def user_data_directory() -> Path:
    """Return an actual persistent user directory, never PyInstaller's _MEIPASS."""
    base = os.getenv('LOCALAPPDATA') or os.getenv('APPDATA')
    return (Path(base) if base else Path.home() / '.local' / 'share') / DATA_DIRECTORY_NAME


def bundled_root() -> Path:
    # sys._MEIPASS is used only for read-only bundled static assets.
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def _check_sqlite(path: Path) -> None:
    """Refuse a corrupt/incompatible adjacent database before importing it."""
    with closing(sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('旧数据库完整性检查失败，已保留原文件')
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'companies', 'jobs', 'job_sources'}.issubset(names):
            raise RuntimeError('旧数据库缺少必需数据表，已保留原文件')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('旧数据库外键检查失败，已保留原文件')


_EVIDENCE_NAME = re.compile(r'[a-f0-9]{32}\.(?:png|jpg|webp)\Z')


def _snapshot_evidence_names(database: Path) -> set[str]:
    """List screenshot dependencies from an already verified SQLite snapshot."""
    with closing(sqlite3.connect(database)) as conn:
        has_capture = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_items'"
        ).fetchone()
        if not has_capture:
            return set()  # pre-v0.6 databases do not have screenshots
        names = {row[0] for row in conn.execute(
            "SELECT screenshot_file FROM capture_items WHERE screenshot_file != ''"
        )}
    if any(not _EVIDENCE_NAME.fullmatch(name) for name in names):
        raise RuntimeError('旧数据库引用了非法截图文件名，迁移已取消')
    return names


def migrate_adjacent_database(destination: Path, executable_directory: Path) -> bool:
    """Copy an adjacent legacy SQLite database AND its referenced screenshots.

    Stage a consistent SQLite snapshot first, validate every evidence reference,
    and copy verified attachments before installing the database. Any error
    leaves the old installation intact; an existing destination is never touched.
    """
    source = executable_directory / 'data' / 'radar.db'
    if destination.exists() or not source.is_file() or source.resolve() == destination.resolve():
        return False
    _check_sqlite(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix='.radar-import-', suffix='.sqlite', dir=destination.parent)
    os.close(fd)
    staged = Path(tmp_name)
    new_files: list[Path] = []
    try:
        with closing(sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True)) as old:
            with closing(sqlite3.connect(staged)) as new:
                old.backup(new)
        _check_sqlite(staged)
        screenshots = _snapshot_evidence_names(staged)
        old_evidence = source.parent / 'capture-evidence'
        new_evidence = destination.parent / 'capture-evidence'
        # Check every source first, before any mutation of destination.
        for name in screenshots:
            image = old_evidence / name
            if not image.is_file():
                raise FileNotFoundError(f'旧数据库引用的招聘截图缺失，拒绝不完整迁移：{name}')
            if image.stat().st_size > 8 * 1024 * 1024:
                raise ValueError(f'招聘截图异常过大，迁移已取消：{name}')
            other = new_evidence / name
            if other.exists() and (not other.is_file() or other.read_bytes() != image.read_bytes()):
                raise FileExistsError(f'目标目录中有同名但不同内容的截图，迁移已取消：{name}')
        if screenshots:
            new_evidence.mkdir(parents=True, exist_ok=True)
        for name in sorted(screenshots):
            source_image = old_evidence / name
            destination_image = new_evidence / name
            if destination_image.exists():
                continue
            # Copy to a temp file in the destination directory, then atomic rename.
            fd_image, temp_image = tempfile.mkstemp(prefix='.capture-import-', dir=new_evidence)
            try:
                with os.fdopen(fd_image, 'wb') as stream, source_image.open('rb') as input_stream:
                    shutil.copyfileobj(input_stream, stream)
                temp_path = Path(temp_image)
                if temp_path.read_bytes() != source_image.read_bytes():
                    raise OSError(f'截图迁移时内容发生变化：{name}')
                os.replace(temp_path, destination_image)
                new_files.append(destination_image)
            finally:
                Path(temp_image).unlink(missing_ok=True)
        if destination.exists():
            for image in new_files:
                image.unlink(missing_ok=True)
            return False
        os.replace(staged, destination)
        return True
    except Exception:
        for image in new_files:
            image.unlink(missing_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True)


def configure_storage(*, executable_directory: Path | None = None) -> Path:
    """Set the path before importing app.main (which imports app.db)."""
    if os.getenv('RADAR_PRIVATE_RC_DB_PATH'):
        return Path(os.environ['RADAR_PRIVATE_RC_DB_PATH']).expanduser()
    dest = user_data_directory() / 'data' / 'radar.db'
    if executable_directory is not None:
        migrate_adjacent_database(dest, executable_directory)
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(dest)
    return dest


def configure_logging() -> Path:
    path = user_data_directory() / 'logs' / 'desktop.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, filename=path, format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    return path


def bind_loopback() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    return listener


def start_backend(listener: socket.socket):
    import uvicorn
    from app.main import app

    # Windowed PyInstaller builds have no stdout/stderr; Uvicorn's default
    # color formatter can fail while configuring its console handler there.
    config = uvicorn.Config(app, host='127.0.0.1', port=listener.getsockname()[1], lifespan='on', log_level='warning', access_log=False, log_config=None)
    server = uvicorn.Server(config)
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, name='radar-local-server', daemon=True)
    worker.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if server.started:
            return server, worker
        if not worker.is_alive():
            raise RuntimeError('本地应用服务启动失败，请查看日志')
        time.sleep(0.05)
    server.should_exit = True
    raise TimeoutError('本地应用服务启动超时')


def stop_backend(server, worker, listener: socket.socket) -> None:
    server.should_exit = True
    worker.join(timeout=8)
    listener.close()


def _message(title: str, body: str) -> None:
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, body, title, 0x10)
    else:
        print(f'{title}: {body}', file=sys.stderr)


def _single_instance_handle():
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    # Old and new builds share one database and one single-instance lock.
    handle = kernel32.CreateMutexW(None, False, r'Local\InternshipRadarPrivateRCDesktop')
    if not handle:
        raise OSError(ctypes.get_last_error(), 'Windows mutex 创建失败')
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        _message(APP_TITLE, '择木已经在运行，请切换到现有窗口。')
        return False
    return handle


def _release_single_instance(handle):
    if handle and sys.platform == 'win32':
        import ctypes
        ctypes.windll.kernel32.CloseHandle(handle)


def self_test(output: Path, *, require_ocr: bool = False) -> int:
    """CI smoke-test of a frozen EXE without opening a native GUI or writing user data."""
    import urllib.request
    report = {'app': PRODUCT, 'result': 'failed', 'resources': False, 'api': False,
              'database': False, 'career_handoff': False, 'ocr': 'not_requested'}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    old_database_override = os.environ.get('RADAR_PRIVATE_RC_DB_PATH')
    try:
        assert (bundled_root() / 'static' / 'index.html').is_file()
        assert (bundled_root() / 'static' / 'app.js').is_file()
        assert (bundled_root() / 'static' / 'export-manager.js').is_file()
        assert (bundled_root() / 'static' / 'analysis-workflows.js').is_file()
        assert (bundled_root() / 'static' / 'ai-review.js').is_file()
        assert (bundled_root() / 'static' / 'analysis-workflows.css').is_file()
        assert (bundled_root() / 'static' / 'export-manager.css').is_file()
        assert (bundled_root() / 'static' / 'career-prompts.js').is_file()
        assert (bundled_root() / 'static' / 'planner.js').is_file()
        assert (bundled_root() / 'static' / 'boss-favorites.js').is_file()
        assert (bundled_root() / 'static' / 'boss-favorites.css').is_file()
        assert (bundled_root() / 'static' / 'templates' / 'ai-keywords-v1.json').is_file()
        assert (bundled_root() / 'static' / 'templates' / 'job-import-example.csv').is_file()
        report['resources'] = True
        if getattr(sys, 'frozen', False):
            assert (bundled_root() / 'assets' / 'radar.ico').is_file()
            report['window_icon'] = True
            from app import shixiseng_runtime
            result = subprocess.run(shixiseng_runtime.command('--self-test'), capture_output=True,
                                    text=True, encoding='utf-8', timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0 or json.loads(result.stdout).get('result') != 'passed':
                raise RuntimeError('打包后的实习僧运行时自检失败')
            report['shixiseng'] = 'passed'
            from app import boss_runtime
            result = subprocess.run(boss_runtime.command('--self-test'), capture_output=True,
                                    text=True, encoding='utf-8', timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0 or json.loads(result.stdout).get('result') != 'passed':
                raise RuntimeError('打包后的 BOSS 采集组件自检失败')
            report['boss_native'] = 'passed'
            from app import boss_favorites
            result = subprocess.run(boss_favorites.runner_command('--self-test'), capture_output=True,
                                    text=True, encoding='utf-8', timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0 or json.loads(result.stdout).get('result') != 'passed':
                raise RuntimeError('打包后的 BOSS 收藏组件自检失败')
            report['boss_favorites_runner'] = 'passed'
        with tempfile.TemporaryDirectory(prefix='radar-selftest-') as temp:
            os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(Path(temp) / 'test.sqlite')
            listener = bind_loopback()
            server, worker = start_backend(listener)
            try:
                base = f'http://127.0.0.1:{listener.getsockname()[1]}'
                with opener.open(base + '/api/health', timeout=10) as resp:
                    report['health'] = json.load(resp)
                with opener.open(base + '/', timeout=10) as resp:
                    assert '<title>择木 · 实习工作台</title>'.encode() in resp.read()
                with opener.open(base + '/static/app.js', timeout=10) as resp:
                    assert len(resp.read()) > 500
                # Verify the actual packaged offline list and native CSV download routes.
                with opener.open(base + '/api/company-lists', timeout=10) as resp:
                    company_lists = json.load(resp)
                    assert len(company_lists['lists']) == 1
                    assert company_lists['lists'][0]['entry_count'] == 100
                    assert company_lists['lists'][0]['job_count'] == 0
                with opener.open(base + '/api/company-lists/' + str(company_lists['builtin_id']) + '?page_size=100', timeout=10) as resp:
                    assert len(json.load(resp)['entries']) == 100
                import csv
                import io
                for kind, count in (('builtin', 101), ('template', 1)):
                    with opener.open(base + '/api/company-lists/' + kind + '.csv', timeout=10) as resp:
                        content = resp.read()
                        assert content.startswith(b'\xef\xbb\xbf')
                        assert len(list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))) == count
                report['company_lists'] = True
                from export_storage import ExportManager
                export_settings = Path(temp) / 'list-export-settings'
                export_settings.mkdir()
                export_folder = Path(temp) / 'list-exports'
                (export_settings / 'export-settings.json').write_text(json.dumps({'folder': str(export_folder)}), encoding='utf-8')
                list_exporter = ExportManager(export_settings, base)
                for kind in ('builtin', 'template'):
                    saved = list_exporter.save_endpoint('/api/company-lists/' + kind + '.csv')
                    assert Path(saved['path']).read_bytes().startswith(b'\xef\xbb\xbf')
                    assert Path(saved['path']).parent == export_folder
                report['company_lists_native_download'] = True
                from app.agent_api import agent_token
                prompt_request = urllib.request.Request(
                    base + '/api/agent/v1/prompts/career-gap',
                    headers={'Authorization': 'Bearer ' + agent_token(create=False)},
                )
                with opener.open(prompt_request, timeout=10) as resp:
                    assert json.load(resp)['text']
                report['agent_api'] = True
                with opener.open(base + '/api/planner/library', timeout=10) as resp:
                    assert json.load(resp)['families'] == []
                report['planner'] = 'passed'
                from app import shixiseng_runtime
                assert shixiseng_runtime.capabilities()['staged_collection'] is True
                report['staged_collection'] = 'passed'
                with opener.open(base + '/api/boss-favorites/template', timeout=10) as response:
                    assert response.read().startswith(b'\xef\xbb\xbf')
                with opener.open(base + '/api/boss-favorites/batches', timeout=10) as response:
                    assert json.load(response) == []
                report['boss_favorites'] = True
                # Cross-feature HTTP smoke on a disposable database: P0 job JSON,
                # P2 statistics, P3 career data and P3.3 findings round-trip.
                def post_json(path, payload):
                    request = urllib.request.Request(
                        base + path,
                        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                        headers={'Content-Type': 'application/json'},
                        method='POST',
                    )
                    with opener.open(request, timeout=15) as response:
                        return json.load(response)

                favorite_preview = post_json('/api/boss-favorites/preview', {
                    'text':'产品运营实习\n财务分析实习', 'format':'text', 'city':'北京',
                })
                favorite_list = post_json('/api/boss-favorites/batches', {
                    'name':'EXE smoke favorites', 'text':'产品运营实习\n财务分析实习', 'format':'text',
                    'city':'北京', 'preview_hash':favorite_preview['preview_hash'],
                })
                assert len(favorite_list['targets']) == 2 and favorite_list['run'] is None
                company = post_json('/api/companies', {'name':'EXE smoke company'})
                job = post_json('/api/jobs', {
                    'company_id': company['id'], 'title':'EXE smoke Agent role',
                    'jd_text':'熟悉 Python 和工具调用', 'direction':'agent',
                })
                exported = post_json('/api/exports/jobs', {'ids':[job['id']]})
                assert exported['jobs'][0]['jd_text'] == '熟悉 Python 和工具调用'
                with opener.open(base + '/api/requirements/stats', timeout=10) as response:
                    demand = json.load(response)
                    assert demand['selected_jobs'] == 1
                career = post_json('/api/career-analysis/export', {
                    'job_ids':[job['id']], 'mode':'compact',
                    'goal':'gap', 'evidence_ids':[],
                })
                assert career['jobs'][0]['job_id'] == job['id']
                assert career['personal_evidence'] == []
                assert career['coverage']['selection_uncovered_jobs'] == 1
                finding = post_json('/api/analysis/import', {
                    'package_id':career['package_id'], 'dry_run':False,
                    'findings':[{'kind':'learning_task',
                                'title':'学习 Python', 'content':'EXE smoke',
                                'job_ids':[job['id']], 'requirement_ids':[],
                                'personal_evidence_ids':[]}],
                })
                assert finding['new_count'] == 1
                assert finding['career_stale_token'] == ''
                extraction = post_json('/api/exports/jobs', {
                    'ids':[job['id']], 'filters':{}, 'intent':'jd_extraction'
                })
                with opener.open(base + '/api/workflow', timeout=10) as response:
                    workflow = json.load(response)
                assert extraction['package_id'] in {
                    b['package_id'] for b in workflow['batches']
                    if b['stage'] == 'awaiting_gpt'
                }
                assert job['id'] in workflow['reserved_job_ids']
                with opener.open(base + '/api/ai-review/diagnosis',timeout=10) as response:
                    assisted = json.load(response)
                assert assisted['pending'] == 0  # no JD candidate has been imported
                with opener.open(base + '/api/companies/'+str(company['id'])+'/jobs',timeout=10) as response:
                    company_positions = json.load(response)
                assert company_positions[0]['id'] == job['id']
                report['workflow_batch'] = True
                report['career_handoff'] = True
                report['api'] = True
                from app.mcp_runtime import check as check_mcp
                mcp = check_mcp(base, Path(os.environ['RADAR_PRIVATE_RC_DB_PATH']).parent)
                assert mcp['ok'], mcp['message']
                report['mcp_stdio'] = True
                with closing(sqlite3.connect(os.environ['RADAR_PRIVATE_RC_DB_PATH'])) as db:
                    from app.db import SCHEMA_VERSION
                    assert db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
                    assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                report['database'] = True
            finally:
                stop_backend(server, worker, listener)
            if worker.is_alive():
                raise RuntimeError('Self-test backend did not stop before recovery check')
            # Exercise the shipped recovery code in an isolated data directory, without WSL or a GUI.
            from app.capture import download_capture_backup
            from app.recovery import prepare_backup, install_prepared
            from desktop_support import DesktopManager
            database = Path(os.environ['RADAR_PRIVATE_RC_DB_PATH'])
            manager = DesktopManager(database.parent / 'settings', 'http://127.0.0.1:9', database)
            assert isinstance(manager.boss_settings()['wsl_available'], bool)
            response = download_capture_backup()
            try:
                folder, summary = prepare_backup(Path(response.path), database.parent / '.restore-staging')
                safety = install_prepared(folder, database)
                assert safety.is_file() and summary['jobs'] > 0
                report['recovery'] = True
                report['optional_connector'] = True
            finally:
                shutil.rmtree(Path(response.path).parent)
        from app import ocr_adapter
        on_demand = bool(ocr_adapter.ocr_component.manifest())
        if on_demand:
            report['ocr'] = 'installed' if ocr_adapter.is_available() else 'on-demand'
        if require_ocr or (getattr(sys, 'frozen', False) and not on_demand):
            # The packaged app must prove it contains both the OCR runtime and
            # the Chinese models; importability alone does not prove this.
            sample = bundled_root() / 'assets' / 'ocr-smoke.b64'
            if not sample.is_file():
                raise FileNotFoundError('打包后缺少中文 OCR 检查图')
            if not on_demand and not ocr_adapter.is_available():
                raise RuntimeError('RapidOCR 或 ONNX Runtime 未包含在 EXE 中')
            recognized = ocr_adapter.smoke_check(sample)
            report['ocr'] = 'passed'
            report['ocr_lines'] = recognized.get('line_count', 0)
        report['result'] = 'passed'
        return_code = 0
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        return_code = 1
    if old_database_override is None:
        os.environ.pop('RADAR_PRIVATE_RC_DB_PATH', None)
    else:
        os.environ['RADAR_PRIVATE_RC_DB_PATH'] = old_database_override
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return return_code


def main() -> int:
    if '--mcp' in sys.argv[1:]:
        from app.mcp_runtime import serve
        return serve()
    # Keep self-test fully noninteractive for CI, including a --windowed binary.
    if len(sys.argv) >= 3 and sys.argv[1] == '--self-test':
        return self_test(Path(sys.argv[2]), require_ocr='--require-ocr' in sys.argv[3:])
    instance = _single_instance_handle()
    if instance is False:
        return 0
    listener = server = worker = manager = None
    try:
        log_path = configure_logging()
        db_path = configure_storage()
        logging.info('Starting Zemu; DB=%s', db_path)
        from app.recovery import recover_interrupted
        recover_interrupted(db_path)
        listener = bind_loopback()
        server, worker = start_backend(listener)
        import webview
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        try:
            # Local agents (MCP, scripts) find this run's random port and token here.
            from app.agent_api import write_endpoint
            write_endpoint(url)
        except OSError:
            logging.exception('Could not write the local agent endpoint file')
        from desktop_support import DesktopManager
        manager = DesktopManager(user_data_directory(), url, db_path)
        webview.create_window(APP_TITLE, url, js_api=manager,
                              width=1320, height=840, min_size=(980, 650),
                              background_color='#f3f3ef')
        # Force Chromium: old IE/MSHTML cannot support this frontend safely.
        webview.start(gui='edgechromium', debug=False, private_mode=False,
                      icon=str(bundled_root() / 'assets' / 'radar.ico'),
                      storage_path=str(user_data_directory() / 'webview-profile'))
        if manager._pending_restore:
            stop_backend(server, worker, listener)
            if worker.is_alive():
                raise RuntimeError('本地服务尚未完全停止，已取消恢复；原数据未替换')
            server = worker = listener = None
            from app.recovery import install_prepared
            safety = install_prepared(manager._restore_folder, db_path)
            logging.info('Restore complete; recovery backup=%s', safety)
            _release_single_instance(instance)
            instance = None
            command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
            subprocess.Popen(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return 0
    except Exception as exc:
        logging.exception('Desktop launch failed')
        _message(APP_TITLE + ' - 启动失败', f'{exc}\n\n若提示 WebView2 缺失，请安装 Microsoft Edge WebView2 Runtime。\n日志位置：{user_data_directory() / "logs" / "desktop.log"}')
        return 1
    finally:
        try:
            from app.agent_api import remove_endpoint
            remove_endpoint()
        except Exception:
            logging.exception('Could not remove the local agent endpoint file')
        if server is not None and worker is not None and listener is not None:
            stop_backend(server, worker, listener)
        elif listener is not None:
            listener.close()
        if manager and manager._restore_folder and not (manager._database.parent / '.restore-journal.json').exists():
            shutil.rmtree(manager._restore_folder, ignore_errors=True)
        _release_single_instance(instance)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
