"""Owned, persistent read-only Shixiseng jobs; Windows desktop bundles Node."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from .db import database_path
from . import local_collector
from .local_collector import ACTIVE, TERMINAL, SnapshotMissingError

_processes: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


def resource_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def node_path() -> str:
    bundled = resource_root() / 'tools' / 'shixiseng-node' / 'node.exe'
    if bundled.is_file():
        return str(bundled)
    override = os.getenv('RADAR_SHIXISENG_NODE')
    if override:
        return override
    windows_node = Path('/mnt/c/Program Files/nodejs/node.exe')
    if windows_node.is_file():
        return str(windows_node)
    found = shutil.which('node')
    if not found:
        raise RuntimeError('实习僧连接器缺少 Node.js；请重新构建完整桌面包')
    return found


def node_argument(path: Path, node: str) -> str:
    if sys.platform != 'win32' and node.lower().endswith('.exe'):
        return subprocess.check_output(['wslpath', '-w', str(path.resolve())], text=True, timeout=5).strip()
    return str(path.resolve())


def command(*args: str) -> list[str]:
    node = node_path()
    runner = resource_root() / 'tools' / 'shixiseng_runner.mjs'
    if not runner.is_file():
        raise RuntimeError('桌面包缺少实习僧连接器脚本')
    return [node, node_argument(runner, node), *args]


def capabilities() -> dict:
    return {'version': 2, 'matched_results': True, 'staged_collection': True, 'modes': ['collect', 'discover', 'hydrate']}


def readiness() -> dict:
    try:
        result = subprocess.run(command('--doctor'), capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=12,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        info = json.loads(result.stdout)
        ready = result.returncode == 0 and info.get('ready') is True
        message = info.get('message') or '实习僧连接器可用；检索时如需登录，请在专用窗口处理'
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
        ready, message = False, '实习僧连接器未准备好；源码运行请安装 tools/shixiseng-node 依赖及 Windows Node.js，桌面版请重新构建'
    return {'ok': ready, 'checks': [{'id': 'shixiseng_runtime', 'status': 'pass' if ready else 'error', 'message': message}],
            'messages': [] if ready else [message]}


def run_directory(task_id: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', task_id or ''):
        raise ValueError('实习僧任务标识无效')
    return database_path().parent / 'shixiseng-runs' / task_id


def _canonical_url(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'https://www\.shixiseng\.com/intern/inn_[A-Za-z0-9_-]+', value))


def _validate_preferences(preferences: dict) -> None:
    if not isinstance(preferences, dict) or preferences.get('collection_mode', 'collect') not in {'collect', 'discover', 'hydrate'}:
        raise ValueError('实习僧采集模式无效')
    for name in ['candidates', 'detail_cache']:
        entries = preferences.get(name, [])
        if (not isinstance(entries, list) or len(entries) > 400
                or any(not isinstance(entry, dict) or not _canonical_url(entry.get('url')) for entry in entries)):
            raise ValueError('实习僧候选或缓存配置无效')
    if len(json.dumps(preferences.get('detail_cache', []), ensure_ascii=False).encode('utf-8')) > 16 * 1024 * 1024:
        raise ValueError('实习僧缓存配置过大，请缩小补全批次')
    for entry in preferences.get('detail_cache', []):
        item = entry.get('item')
        if (not isinstance(item, dict) or item.get('url') != entry['url'] or not isinstance(item.get('jd'), str)
                or len(item['jd']) > 100000 or item.get('jd_available') is not True
                or any(not isinstance(item.get(field), str) for field in ['title', 'company', 'city'])
                or not isinstance(entry.get('last_detail_at'), str)):
            raise ValueError('实习僧缓存字段无效')
    max_jobs = preferences.get('max_jobs', 60)
    max_lists = preferences.get('max_list_attempts', 12)
    minimum_lists = 0 if preferences.get('collection_mode') == 'hydrate' else 1
    if type(max_jobs) is not int or not 1 <= max_jobs <= 60 or type(max_lists) is not int or not minimum_lists <= max_lists <= 12:
        raise ValueError('实习僧访问预算无效')
    deadline = preferences.get('deadline_at')
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline) or not 0 < deadline <= time.time() + 86400):
        raise ValueError('实习僧时间预算无效')


def validate_snapshot(value: dict, task_id: str) -> None:
    local_collector.validate_snapshot(value, task_id, canonical_url=_canonical_url,
                                     list_url=lambda url: url.startswith('https://www.shixiseng.com/interns?'),
                                     label='实习僧')


def read_snapshot(task_id: str) -> dict:
    return local_collector.read_snapshot_file(run_directory(task_id) / 'result.json', task_id,
                                             validate_snapshot, _processes.get(task_id), '实习僧')


def _read(task_id: str) -> dict:
    return local_collector.refresh_snapshot(read_snapshot(task_id), run_directory(task_id),
                                            _processes.get(task_id))


def task_active(task_id: str | None) -> bool:
    if task_id is not None:
        return _read(task_id).get('status') in ACTIVE
    root = database_path().parent / 'shixiseng-runs'
    for path in root.glob('*/result.json'):
        try:
            if _read(path.parent.name).get('status') in ACTIVE:
                return True
        except (ValueError, RuntimeError, OSError):
            continue
    return False


def start_search(preferences: dict) -> dict:
    _validate_preferences(preferences)
    with _lock:
        if task_active(None):
            raise RuntimeError('已有实习僧检索正在运行，请先停止或等待完成')
        task_id = uuid.uuid4().hex
        directory = run_directory(task_id)
        directory.mkdir(parents=True)
        _processes[task_id] = local_collector.launch_runner(
            task_id, directory, preferences, platform='shixiseng', label='实习僧',
            node_path=node_path, node_argument=node_argument, command=command)
    return {'task': {'id': task_id, 'mode': preferences.get('collection_mode', 'collect'), 'status': 'running'}}


def result_page(task_id: str, limit: int = 200, offset: int = 0) -> dict:
    if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
        raise ValueError('实习僧结果分页参数无效')
    value = _read(task_id)
    return local_collector.page_results(value, limit, offset)


def stop(task_id: str) -> dict:
    value = _read(task_id)
    if value['status'] in ACTIVE:
        (run_directory(task_id) / 'stop').write_text('stop', encoding='ascii')
        value['status'] = 'stopping'
    return value
