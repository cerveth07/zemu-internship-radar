"""BOSS collection using the desktop's bundled Node and a visible local browser."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import urlsplit
import uuid

from .db import database_path
from . import local_collector
from .local_collector import ACTIVE, TERMINAL, SnapshotMissingError
from .shixiseng_runtime import resource_root, node_path, node_argument

_processes: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


def command(*args: str) -> list[str]:
    node = node_path()
    runner = resource_root() / 'tools' / 'boss_runner.mjs'
    if not runner.is_file():
        raise RuntimeError('桌面包缺少 BOSS 采集组件，请更新完整应用')
    return [node, node_argument(runner, node), *args]


def supported_cities() -> dict[str, str]:
    path = resource_root() / 'tools' / 'boss-cities.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    cities = value.get('cities') if isinstance(value, dict) else None
    if (not isinstance(cities, dict) or not cities
            or any(not isinstance(name, str) or not isinstance(code, str) or not re.fullmatch(r'\d{9}', code)
                   for name, code in cities.items())):
        raise RuntimeError('BOSS 城市资料不完整，请更新应用')
    return cities


def capabilities() -> dict:
    return {'version': 1, 'matched_results': True, 'native': True,
            'modes': ['collect', 'discover'], 'human_resume': True,
            'browser_modes': ['chrome_use', 'managed']}


def readiness(browser_mode: str = 'managed') -> dict:
    if browser_mode not in ('chrome_use', 'managed'):
        raise ValueError('BOSS 浏览器模式无效')
    default_message = ('已连接现有 Chrome；如需登录或验证，请在检索标签中完成'
                       if browser_mode == 'chrome_use' else 'BOSS 采集组件已就绪；首次检索时在打开的窗口登录')
    try:
        supported_cities()
        arguments = ('--doctor', '--browser-mode', browser_mode) if browser_mode == 'chrome_use' else ('--doctor',)
        result = subprocess.run(command(*arguments), capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        info = json.loads(result.stdout)
        if not isinstance(info, dict):
            raise ValueError('BOSS 浏览器状态格式无效')
        ready = result.returncode == 0 and info.get('ready') is True
        message = info.get('message') if isinstance(info.get('message'), str) and info['message'] else default_message
        if not ready and message == default_message:
            message = '当前浏览器连接尚未就绪，请重新检查'
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
        ready = False
        message = ('Chrome 连接未就绪；请打开 Chrome 并连接浏览器扩展后重试'
                   if browser_mode == 'chrome_use' else 'BOSS 采集组件未就绪；请使用完整 Windows 桌面包，并安装 Chrome 或 Edge')
    return {'ok': ready, 'browser_mode': browser_mode,
            'checks': [{'id': 'boss_native', 'status': 'pass' if ready else 'error', 'message': message}],
            'messages': [] if ready else [message]}


def run_directory(task_id: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', task_id or ''):
        raise ValueError('BOSS 任务标识无效')
    return database_path().parent / 'boss-runs' / task_id


def _canonical_url(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(
        r'https://www\.zhipin\.com/job_detail/[A-Za-z0-9_~\-]{1,128}\.html', value))


def _list_url(value) -> bool:
    if not isinstance(value, str):
        return False
    url = urlsplit(value)
    return (url.scheme == 'https' and url.netloc == 'www.zhipin.com'
            and url.path in ('/web/geek/job', '/web/geek/jobs') and not url.fragment)


def validate_preferences(preferences: dict) -> None:
    if not isinstance(preferences, dict) or preferences.get('collection_mode', 'collect') not in {'collect', 'discover'}:
        raise ValueError('BOSS 采集方式无效')
    if preferences.get('browser_mode', 'managed') not in ('chrome_use', 'managed'):
        raise ValueError('BOSS 浏览器模式无效')
    for key in ('keywords', 'cities'):
        values = preferences.get(key)
        if (not isinstance(values, list) or not 1 <= len(values) <= 9
                or any(not isinstance(value, str) or not value.strip() or len(value) > 120 for value in values)
                or len(set(values)) != len(values)):
            raise ValueError('请填写 1–9 个不重复的关键词和城市，每项最多 120 字')
    cities = supported_cities()
    unsupported = [city for city in preferences['cities'] if city not in cities]
    if unsupported:
        raise ValueError('BOSS 暂不支持这些城市：' + '、'.join(unsupported))
    pages = preferences.get('max_pages', {})
    pages = pages.get('boss', 1) if isinstance(pages, dict) else None
    combinations = len(preferences['keywords']) * len(preferences['cities'])
    if type(pages) is not int or not 1 <= pages <= 3 or combinations > 9 or combinations * pages > 12:
        raise ValueError('单次最多 9 个关键词与城市组合、12 页列表；每个组合 1–3 页')
    max_jobs = preferences.get('max_jobs', 60)
    if type(max_jobs) is not int or not 1 <= max_jobs <= 60:
        raise ValueError('本次读取详情数量须为 1–60')
    if type(preferences.get('internship_only', True)) is not bool:
        raise ValueError('实习岗位筛选条件无效')


def validate_snapshot(value: dict, task_id: str) -> None:
    local_collector.validate_snapshot(value, task_id, canonical_url=_canonical_url,
                                     list_url=_list_url, label='BOSS')


def read_snapshot(task_id: str) -> dict:
    return local_collector.read_snapshot_file(run_directory(task_id) / 'result.json', task_id,
                                             validate_snapshot, _processes.get(task_id), 'BOSS')


def _read(task_id: str) -> dict:
    return local_collector.refresh_snapshot(read_snapshot(task_id), run_directory(task_id),
                                            _processes.get(task_id))


def task_active(task_id: str | None) -> bool:
    if task_id is not None:
        return _read(task_id)['status'] in ACTIVE
    for path in (database_path().parent / 'boss-runs').glob('*/result.json'):
        try:
            if _read(path.parent.name)['status'] in ACTIVE:
                return True
        except (ValueError, RuntimeError, OSError):
            continue
    return False


def start_search(preferences: dict, task_id: str | None = None) -> dict:
    validate_preferences(preferences)
    with _lock:
        if task_active(None):
            raise RuntimeError('已有 BOSS 检索正在运行，请先停止或等待完成')
        task_id = task_id or uuid.uuid4().hex
        directory = run_directory(task_id)
        directory.mkdir(parents=True)
        _processes[task_id] = local_collector.launch_runner(
            task_id, directory, preferences, platform='boss', label='BOSS', node_path=node_path,
            node_argument=node_argument, command=command,
            browser_profile=(database_path().parent / 'BossRadarChrome'
                             if preferences.get('browser_mode', 'managed') == 'managed' else None))
    return {'task': {'id': task_id, 'mode': preferences.get('collection_mode', 'collect'), 'status': 'running'}}


def result_page(task_id: str, limit: int = 200, offset: int = 0) -> dict:
    if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
        raise ValueError('BOSS 结果分页参数无效')
    value = _read(task_id)
    return local_collector.page_results(value, limit, offset)


def stop(task_id: str) -> dict:
    value = _read(task_id)
    if value['status'] in ACTIVE:
        (run_directory(task_id) / 'stop').write_text('stop', encoding='ascii')
        value['status'] = 'stopping'
    return value


def resume(task_id: str) -> dict:
    value = _read(task_id)
    phase = value.get('platform_state', {}).get('boss', {}).get('phase')
    if value['status'] not in {'pending', 'running'} or phase not in {'login_required', 'verification_required'}:
        raise ValueError('当前任务没有等待登录或验证；请刷新任务状态')
    (run_directory(task_id) / 'resume').write_text('resume', encoding='ascii')
    return value
