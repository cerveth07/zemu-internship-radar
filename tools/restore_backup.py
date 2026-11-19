"""Offline safe SQLite restore; never run while the Radar server is running."""
from contextlib import closing
import argparse
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

REQUIRED = {'companies', 'jobs', 'job_sources', 'job_history', 'skills', 'job_skills',
            'applications', 'learning_tasks', 'import_reviews', 'profile_evidence'}


def check_database(path: Path):
    uri = f'file:{path.resolve().as_posix()}?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite 完整性检查失败')
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if missing := REQUIRED - names:
            raise ValueError(f'缺少必须的数据表：{sorted(missing)}')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('存在违反外键约束的数据')


def restore(backup: Path, destination: Path):
    if not backup.is_file():
        raise FileNotFoundError(f'找不到备份：{backup}')
    check_database(backup)
    destination.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ('-wal', '-shm'):
        if Path(str(destination) + suffix).exists():
            raise RuntimeError(f'发现 {suffix} 文件：请先完全关闭服务并确保已执行 SQLite 检查点，停止恢复')
    if destination.exists():
        check_database(destination)
        label = datetime.now().strftime('%Y%m%d-%H%M%S')
        safety = destination.with_name(f'{destination.stem}-pre-restore-{label}.sqlite')
        if safety.exists():
            raise FileExistsError(f'安全副本已存在：{safety}')
        shutil.copy2(destination, safety)
        check_database(safety)
        print(f'恢复前副本：{safety}')
    fd, temp_name = tempfile.mkstemp(prefix='.radar-restore-', suffix='.sqlite', dir=destination.parent)
    os.close(fd)
    staged = Path(temp_name)
    try:
        shutil.copy2(backup, staged)
        check_database(staged)
        # Process ownership cannot be restored from a database. This also
        # repairs older SQLite-only backups that have no sidecar snapshots.
        with closing(sqlite3.connect(staged)) as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(capture_search_runs)')}
            if 'platform' in columns:
                conn.execute("""UPDATE capture_search_runs SET status='failed',
                    error='备份恢复后原采集进程已中断；已同步资料保留，可重新检索',
                    finished_at=datetime('now') WHERE
                    status IN ('starting','pending','running','stopping','unknown')""")
            favorite_columns = {r[1] for r in conn.execute('PRAGMA table_info(boss_favorite_runs)')}
            if 'task_id' in favorite_columns:
                conn.execute("""UPDATE boss_favorite_runs SET status='failed',
                    error='备份恢复后原任务已中断；请重新读取状态后处理剩余岗位',finished_at=datetime('now')
                    WHERE status IN ('starting','pending','running','stopping','unknown')""")
            conn.commit()
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)
    print(f'已恢复：{destination}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='恢复择木 SQLite 备份（必须先关闭服务）')
    parser.add_argument('backup', type=Path)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[1] / 'data/radar.db')
    parser.add_argument('--confirm', choices=['RESTORE'], required=True, help='输入 RESTORE 确认将替换本地数据')
    args = parser.parse_args()
    restore(args.backup, args.database)
