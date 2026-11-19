"""Prepare untrusted backups separately; install only after the server has stopped."""
from contextlib import closing
from pathlib import Path
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile

from .db import SCHEMA_VERSION
from tools.restore_backup import check_database, restore
from tools.restore_capture_bundle import restore_bundle
from export_storage import _write_json_atomic

MAX_BACKUP = 512 * 1024 * 1024


def recover_interrupted(database: Path):
    journal = database.parent / '.restore-journal.json'
    if not journal.exists():
        return
    record = json.loads(journal.read_text('utf-8'))
    folder = Path(record['folder']).resolve()
    if folder.parent != (database.parent / '.restore-staging').resolve():
        raise RuntimeError('恢复记录路径异常，已停止启动以保护数据')
    ready, previous = folder / 'ready', folder / 'previous'
    pairs = _paths(database, ready, previous)
    # A journal written by v0.11.0 predates native BOSS snapshots and lists
    # only database/screenshots/Shixiseng. Replay exactly the paths it owned.
    if len(record['existed']) in (3, 4):
        pairs = pairs[:len(record['existed'])]
    for (live, new, old), existed in reversed(list(zip(pairs, record['existed'], strict=True))):
        if old.exists():
            if live.exists():
                os.replace(live, new)
            os.replace(old, live)
        elif not existed and not new.exists() and live.exists():
            os.replace(live, new)
    journal.unlink()


def _paths(database, ready, previous):
    return [(database, ready / 'radar.db', previous / 'radar.db')] + [
        (database.parent / name, ready / name, previous / name)
        for name in ('capture-evidence', 'shixiseng-runs', 'boss-runs', 'boss-favorite-runs')]


def prepare_backup(source: Path, parent: Path) -> tuple[Path, dict]:
    if not source.is_file() or source.stat().st_size > MAX_BACKUP:
        raise ValueError('备份不存在或超过 512 MB，请选择完整备份 ZIP 或 SQLite 文件')
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='restore-', dir=parent))
    try:
        # Snapshot the chosen file: later edits to the original cannot change the preview.
        copied = folder / 'selected-backup'
        shutil.copy2(source, copied)
        target = folder / 'ready' / 'radar.db'
        target.parent.mkdir()
        if zipfile.is_zipfile(copied):
            with zipfile.ZipFile(copied) as archive:
                members = archive.infolist()
                names = [entry.filename for entry in members]
                if len(names) != len(set(names)) or len(names) > 20000:
                    raise ValueError('备份包含重复文件或文件数量异常')
                if sum(entry.file_size for entry in members) > MAX_BACKUP:
                    raise ValueError('备份解压后超过 512 MB，请使用离线恢复工具')
            restore_bundle(copied, target)
        else:
            check_database(copied)
            with closing(sqlite3.connect(copied)) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'capture_items' in tables and db.execute("SELECT 1 FROM capture_items WHERE screenshot_file!='' LIMIT 1").fetchone():
                    raise ValueError('此 SQLite 备份引用了截图，请选择含截图的完整 ZIP 备份')
            restore(copied, target)
        with closing(sqlite3.connect(target)) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError('备份来自更新的应用版本，请先升级应用再恢复')
            summary = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                       for name in ('jobs', 'companies', 'applications')}
        return folder, {**summary, 'name': source.name, 'schema_version': version}
    except Exception:
        shutil.rmtree(folder)
        raise


def install_prepared(folder: Path, database: Path) -> Path:
    """Caller owns the desktop instance lock and has drained all backend requests."""
    ready = folder / 'ready'
    check_database(ready / 'radar.db')
    with closing(sqlite3.connect(database)) as current:
        if current.execute("SELECT 1 FROM capture_search_runs WHERE status IN ('starting','pending','running','stopping','unknown') LIMIT 1").fetchone():
            raise RuntimeError('仍有采集任务运行或等待，恢复已取消；请先停止任务')
    for suffix in ('-wal', '-shm'):
        if Path(str(database) + suffix).exists():
            raise RuntimeError('数据库仍在使用，请关闭其他择木窗口后重试')
    # Preserve a complete user-restorable copy before touching any current files.
    from .capture import download_capture_backup
    response = download_capture_backup()
    backups = database.parent / 'backups'
    backups.mkdir(exist_ok=True)
    safety = backups / ('before-restore-' + uuid.uuid4().hex + '.zip')
    try:
        shutil.copy2(response.path, safety)
    finally:
        shutil.rmtree(Path(response.path).parent)
    previous = folder / 'previous'
    previous.mkdir()
    moved, installed = [], []
    pairs = _paths(database, ready, previous)
    journal = database.parent / '.restore-journal.json'
    _write_json_atomic(journal, {'folder': str(folder.resolve()), 'existed': [live.exists() for live, _, _ in pairs]})
    try:
        for live, new, old in pairs:
            if live.exists():
                os.replace(live, old)
                moved.append((live, old))
            if new.exists():
                os.replace(new, live)
                installed.append((live, new))
    except Exception:
        for live, new in reversed(installed):
            os.replace(live, new)
        for live, old in reversed(moved):
            os.replace(old, live)
        journal.unlink()
        raise
    journal.unlink()
    return safety
