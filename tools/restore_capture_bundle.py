"""Offline restore of the SQLite, screenshots and search-snapshot backup ZIP.

Stop Radar before running. Preserves previous database and never deletes old evidence.
"""
from contextlib import closing
import argparse
import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
try:
    from .restore_backup import check_database, restore
except ImportError:
    from restore_backup import check_database, restore

EVIDENCE_NAME = re.compile(r'^capture-evidence/([a-f0-9]{32}\.(?:png|jpg|webp))$')
SNAPSHOT_NAME = re.compile(r'^(shixiseng|boss|boss-favorite)-runs/([a-f0-9]{32})/result\.json$')


def restore_bundle(archive: Path, database: Path):
    if not archive.is_file():
        raise FileNotFoundError(archive)
    with tempfile.TemporaryDirectory(prefix='ai-radar-restore-') as directory:
        staged = Path(directory)
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            if not any(m.filename == 'radar.sqlite' for m in members):
                raise ValueError('备份包缺少 radar.sqlite')
            if any(m.file_size > 8*1024*1024 for m in members if m.filename.startswith('capture-evidence/')):
                raise ValueError('备份包存在异常过大的截图')
            if any(m.file_size > 2*1024*1024*1024 for m in members):
                raise ValueError('备份包存在超大文件')
            db_bytes = z.read('radar.sqlite')
            db_source=staged/'radar.sqlite'
            db_source.write_bytes(db_bytes)
            check_database(db_source)
            attachments=[]
            snapshots=[]
            for m in members:
                if m.filename in ('radar.sqlite','manifest.json'):continue
                if m.is_dir():continue
                task = SNAPSHOT_NAME.fullmatch(m.filename)
                if task:
                    if m.file_size > 32 * 1024 * 1024:
                        raise ValueError('采集快照超过 32 MB，已拒绝恢复')
                    from app import boss_runtime, shixiseng_runtime, boss_favorites
                    platform, task_id = task.groups()
                    runtime = {'boss': boss_runtime, 'shixiseng': shixiseng_runtime, 'boss-favorite': boss_favorites}[platform]
                    blob = z.read(m)
                    value = json.loads(blob)
                    runtime.validate_snapshot(value, task_id)
                    snapshots.append((platform, task_id, blob, value['status'] in runtime.ACTIVE))
                    continue
                found=EVIDENCE_NAME.fullmatch(m.filename)
                if not found:
                    raise ValueError(f'备份包中存在不允许的路径：{m.filename}')
                attachments.append((found.group(1),z.read(m)))
            # A complete backup must contain every referenced screenshot, not
            # just an internally valid SQLite file and some arbitrary images.
            import sqlite3
            with closing(sqlite3.connect(db_source)) as snapshot:
                tables = {r[0] for r in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                expected = {r[0] for r in snapshot.execute(
                    "SELECT screenshot_file FROM capture_items WHERE screenshot_file != ''")} if 'capture_items' in tables else set()
                from app.local_collector import owned_snapshot_tasks
                tasks = set(owned_snapshot_tasks(snapshot))
            actual = [name for name, _ in attachments]
            if len(actual) != len(set(actual)) or set(actual) != expected:
                raise ValueError('备份截图与数据库引用不一致，已拒绝恢复')
            task_ids = [(platform, task_id) for platform, task_id, _, _ in snapshots]
            if len(task_ids) != len(set(task_ids)) or not set(task_ids).issubset(tasks):
                raise ValueError('采集快照与数据库任务归属不一致，已拒绝恢复')
            # Check collisions before mutating any live data.
            evidence_dest=database.parent/'capture-evidence'
            for name,blob in attachments:
                dest=evidence_dest/name
                if dest.exists() and dest.read_bytes()!=blob:
                    raise FileExistsError(f'截图文件重名但内容不同：{dest}')
            snapshot_files = []
            for platform, task_id, blob, active in snapshots:
                folder = database.parent/(platform+'-runs')/task_id
                dest = folder/'result.json'
                if dest.exists() and dest.read_bytes() != blob:
                    raise FileExistsError('目标已有不同版本的采集快照，请恢复到空目录：' + str(dest))
                snapshot_files.append((dest, blob))
                if active:
                    snapshot_files.append((folder/'interrupted', b''))
            evidence_dest.mkdir(parents=True,exist_ok=True)
            new_files=[]
            try:
                for name,blob in attachments:
                    dest=evidence_dest/name
                    if not dest.exists():
                        dest.write_bytes(blob)
                        new_files.append(dest)
                for dest,blob in snapshot_files:
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(blob)
                        new_files.append(dest)
                # Existing restore utility validates again and preserves a pre-restore DB.
                restore(db_source,database)
            except Exception:
                for path in new_files:path.unlink(missing_ok=True)
                raise
    print(f'已恢复采集截图 {len(attachments)} 张；旧有其他截图未被删除')


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from tools.restore_capture_bundle import restore_bundle as run
    parser=argparse.ArgumentParser(description='离线恢复择木完整备份包（关闭程序后操作）')
    parser.add_argument('backup',type=Path)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--confirm',required=True,choices=['RESTORE'])
    args=parser.parse_args()
    run(args.backup,args.database)
