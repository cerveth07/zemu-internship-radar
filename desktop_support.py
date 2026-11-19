"""Native dialogs and explicit optional-connector setup; no developer-machine defaults."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import threading
import uuid

from export_storage import ExportManager, _write_json_atomic


def read_boss_settings(user_dir):
    try:
        data = json.loads((Path(user_dir) / 'boss-settings.json').read_text('utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Invalid connector settings')
        return {'distro': str(data.get('distro', '')), 'script': str(data.get('script', ''))}
    except (OSError, ValueError, TypeError):
        return {'distro': os.getenv('RADAR_BOSSHUNTER_WSL_DISTRO', ''),
                'script': os.getenv('RADAR_BOSSHUNTER_WSL_SCRIPT', '')}


def validate_boss_settings(distro, script):
    if not re.fullmatch(r'[\w.-]{1,80}', distro) or not script.startswith('/') or any(c in script for c in '\r\n\0') or len(script) > 500:
        raise ValueError('请填写 WSL 发行版名称和以 / 开头的启动脚本绝对路径')


class DesktopManager(ExportManager):
    def __init__(self, user_dir, origin, database):
        super().__init__(user_dir, origin)
        self._database = Path(database)
        self._restore_folder = None
        self._restore_token = None
        self._pending_restore = False

    def boss_settings(self):
        settings = read_boss_settings(self.user_dir)
        wsl = shutil.which('wsl.exe')
        return {**settings, 'wsl_available': bool(wsl),
                'configured': bool(settings['distro'] and settings['script'])}

    def open_backup_folder(self):
        folder = self._database.parent / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        self._open_path(folder)
        return {'ok': True}

    def save_boss_settings(self, distro, script):
        validate_boss_settings(distro, script)
        _write_json_atomic(self.user_dir / 'boss-settings.json', {'distro': distro, 'script': script})
        return {'ok': True}

    def start_boss_connector(self):
        settings = read_boss_settings(self.user_dir)
        validate_boss_settings(**settings)
        wsl = shutil.which('wsl.exe')
        if not wsl:
            raise ValueError('尚未安装 WSL。可先使用粘贴 JD 或实习僧；BOSS 连接器需要另行安装。')
        check = subprocess.run([wsl, '-d', settings['distro'], '--', 'test', '-f', settings['script']],
                               capture_output=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if check.returncode:
            raise ValueError('指定发行版或脚本不存在。请核对安装的发行版名称与 BossHunter 启动脚本路径。')
        log = self.user_dir / 'logs' / 'bosshunter-start.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('ab') as stream:
            subprocess.Popen([wsl, '-d', settings['distro'], '--', 'bash', settings['script'], '--desktop-backend'],
                             stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return {'ok': True, 'message': '已请求启动。返回采集收件箱检查连接；登录或验证码需在专用浏览器完成。'}

    def choose_restore_backup(self):
        import webview
        with self.lock:
            if self._pending_restore:
                raise ValueError('正在准备重启恢复，请稍候')
            files = webview.windows[0].create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                         file_types=('择木备份 (*.zip;*.sqlite;*.db)',))
            if not files:
                return {'cancelled': True}
            from app.recovery import prepare_backup
            folder, summary = prepare_backup(Path(files[0]), self._database.parent / '.restore-staging')
            if self._restore_folder:
                shutil.rmtree(self._restore_folder)
            self._restore_folder = folder
            self._restore_token = uuid.uuid4().hex
            return {**summary, 'token': self._restore_token}

    def confirm_restore(self, token):
        from app.db import connect
        with self.lock:
            if self._pending_restore or not self._restore_token or token != self._restore_token:
                raise ValueError('恢复预览已失效，请重新选择备份')
            with connect() as db:
                if db.execute("SELECT 1 FROM capture_search_runs WHERE status IN ('starting','pending','running','stopping','unknown') LIMIT 1").fetchone():
                    raise ValueError('请先停止正在运行或等待中的采集任务，再恢复数据')
            import webview
            self._pending_restore = True
            from app.main import app
            app.state.restoring = True
            # Let the bridge response finish before closing the native window.
            threading.Timer(0.6, webview.windows[0].destroy).start()
            return {'ok': True}
