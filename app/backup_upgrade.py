"""Archive a pre-migration SQLite snapshot and its referenced evidence."""
from contextlib import closing
from pathlib import Path
import json
import re
import sqlite3
import zipfile


def archive_snapshot(snapshot: Path, data_directory: Path) -> Path:
    target = snapshot.with_suffix('.zip')
    staged = target.with_suffix('.zip.tmp')
    try:
        with closing(sqlite3.connect(snapshot)) as db, zipfile.ZipFile(staged, 'w', zipfile.ZIP_DEFLATED) as archive:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            archive.write(snapshot, 'radar.sqlite')
            if 'capture_items' in tables:
                for (name,) in db.execute("SELECT DISTINCT screenshot_file FROM capture_items WHERE screenshot_file!=''"):
                    if not re.fullmatch(r'[a-f0-9]{32}\.(png|jpg|webp)', name):
                        raise ValueError('截图引用异常，已停止升级')
                    source = data_directory / 'capture-evidence' / name
                    if not source.is_file():
                        raise ValueError('升级前备份缺少引用截图，请先修复附件')
                    archive.write(source, 'capture-evidence/' + name)
            from .local_collector import owned_snapshot_tasks
            for platform, task in owned_snapshot_tasks(db):
                source = data_directory / (platform+'-runs') / task / 'result.json'
                if source.is_file():
                    archive.write(source, platform+'-runs/' + task + '/result.json')
            archive.writestr('manifest.json', json.dumps({'schema': 'radar-capture-backup-v1', 'reason': 'before-upgrade'}))
        staged.replace(target)
        return target
    finally:
        staged.unlink(missing_ok=True)
