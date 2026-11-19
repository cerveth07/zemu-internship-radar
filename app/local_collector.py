"""Shared local runner lifecycle and snapshot ownership for desktop collectors.

Only the recorded connector selects a local snapshot. Historical BOSS runs
without that marker continue to belong to the external BossHunter service.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import re
import subprocess
import time
from .company_profile import valid_company_source

ACTIVE = {'starting', 'pending', 'running', 'stopping', 'unknown'}
TERMINAL = {'completed', 'completed_with_shortage', 'completed_with_errors', 'stopped', 'failed'}


class SnapshotMissingError(RuntimeError):
    """A durable local run has no snapshot and no known live writer."""


def page_results(value, limit, offset):
    # A JD is durable before its optional company lookup. Publish it once that
    # lookup settles so a polling sync cannot permanently miss the new fields.
    # Interrupted/terminal tasks expose all saved JDs, even after a hard exit.
    items = value['items']
    if value['status'] in ACTIVE:
        end = next((i for i, item in enumerate(items) if item.get('company_lookup_pending') is True), len(items))
        items = items[:end]
    value['total'] = len(items)
    value['items'] = items[offset:offset + limit]
    return value


def local_platform(run: dict) -> str | None:
    if run.get('platform') == 'shixiseng':
        return 'shixiseng'
    if run.get('platform', 'boss') == 'boss' and run.get('preferences', {}).get('connector') == 'native':
        return 'boss'
    return None


def owned_snapshot_tasks(connection) -> list[tuple[str, str]]:
    """Enumerate database-owned local snapshots, including older DB versions."""
    columns = {r[1] for r in connection.execute('PRAGMA table_info(capture_search_runs)')}
    if not {'platform', 'preferences_json', 'task_id'}.issubset(columns):
        return []
    tasks = set()
    for platform, task_id, preferences_json in connection.execute(
            'SELECT platform,task_id,preferences_json FROM capture_search_runs WHERE task_id IS NOT NULL'):
        preferences = json.loads(preferences_json)
        if not isinstance(preferences, dict):
            raise ValueError('采集任务配置无效，无法确认快照归属')
        selected = local_platform({'platform': platform, 'preferences': preferences})
        if selected:
            if not re.fullmatch(r'[a-f0-9]{32}', task_id or ''):
                raise ValueError('本地采集任务标识无效，无法备份或恢复')
            tasks.add((selected, task_id))
    favorite_columns = {r[1] for r in connection.execute('PRAGMA table_info(boss_favorite_runs)')}
    if 'task_id' in favorite_columns:
        for (task_id,) in connection.execute('SELECT task_id FROM boss_favorite_runs'):
            if not re.fullmatch(r'[a-f0-9]{32}', task_id or ''):
                raise ValueError('收藏任务标识无效，无法备份或恢复')
            tasks.add(('boss-favorite', task_id))
    return sorted(tasks)


def validate_snapshot(value, task_id, *, canonical_url, list_url, label):
    if (not isinstance(value, dict) or value.get('task_id') != task_id or value.get('run_id') != task_id
            or not isinstance(value.get('items'), list) or len(value['items']) > 400
            or value.get('status') not in ACTIVE | TERMINAL):
        raise RuntimeError(f'{label}任务快照归属不匹配')
    heartbeat = value.get('heartbeat')
    if type(heartbeat) not in (int, float) or not math.isfinite(heartbeat) or heartbeat <= 0:
        raise RuntimeError(f'{label}任务心跳无效，已停止读取该快照')
    urls = set()
    for item in value['items']:
        if (not isinstance(item, dict) or not canonical_url(item.get('url'))
                or not isinstance(item.get('title'), str)
                or any(key in item and not isinstance(item[key], str) for key in ('company', 'city', 'jd'))
                or len(item.get('jd', '')) > 100000 or item['url'] in urls):
            raise RuntimeError(f'{label}岗位结果无效或含重复身份')
        urls.add(item['url'])
    candidates, receipts = value.get('candidates', []), value.get('receipts', [])
    if not isinstance(candidates, list) or len(candidates) > 400 or not isinstance(receipts, list) or len(receipts) > 1000:
        raise RuntimeError(f'{label}候选或访问回执超出范围')
    urls = set()
    for candidate in candidates:
        if (not isinstance(candidate, dict) or not canonical_url(candidate.get('url'))
                or candidate['url'] in urls or candidate.get('detail_state') not in {'pending', 'complete', 'incomplete', 'filtered', 'failed', 'closed'}
                or any(not isinstance(candidate.get(key), str) for key in ('title', 'company', 'city'))
                or not isinstance(candidate.get('hits'), list) or len(candidate['hits']) > 108):
            raise RuntimeError(f'{label}候选记录无效')
        urls.add(candidate['url'])
        for hit in candidate['hits']:
            if (not isinstance(hit, dict)
                    or any(not isinstance(hit.get(key), str) or not hit[key].strip() or len(hit[key]) > 120 for key in ('keyword', 'city'))
                    or type(hit.get('page')) is not int or not 1 <= hit['page'] <= 3):
                raise RuntimeError(f'{label}命中记录无效')
    ids = set()
    for receipt in receipts:
        if (not isinstance(receipt, dict) or not isinstance(receipt.get('id'), str) or not receipt['id'] or receipt['id'] in ids
                or receipt.get('kind') not in {'list', 'detail', 'cache', 'company'}
                or receipt.get('status') not in {'started', 'completed', 'failed', 'unknown'}
                or not isinstance(receipt.get('started_at'), str)
                or not isinstance(receipt.get('url'), str)
                or not (list_url(receipt['url']) if receipt['kind'] == 'list' else
                        valid_company_source(receipt['url'], 'boss' if 'BOSS' in label else 'shixiseng', company_only=True)
                        if receipt['kind'] == 'company' else canonical_url(receipt['url']))):
            raise RuntimeError(f'{label}访问回执无效')
        try:
            datetime.fromisoformat(receipt['started_at'].replace('Z', '+00:00'))
            if receipt['status'] in {'completed', 'failed'}:
                datetime.fromisoformat(receipt['finished_at'].replace('Z', '+00:00'))
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise RuntimeError(f'{label}访问回执时间无效') from exc
        ids.add(receipt['id'])


def read_snapshot_file(target: Path, task_id: str, validator, process, label: str) -> dict:
    if not target.is_file():
        if process is not None and process.poll() is None:
            raise RuntimeError('采集进程仍在运行，但快照暂不可读，请稍后重试')
        raise SnapshotMissingError(f'{label}任务快照不存在；已同步的收件箱资料仍保留，可重新检索')
    if target.stat().st_size > 32 * 1024 * 1024:
        raise RuntimeError(f'{label}任务结果过大，已停止读取')
    value = json.loads(target.read_text(encoding='utf-8'))
    validator(value, task_id)
    return value


def refresh_snapshot(value: dict, directory: Path, process) -> dict:
    interrupted = directory / 'interrupted'
    if value['status'] in ACTIVE and (
            (process is not None and process.poll() is not None)
            or time.time() - value['heartbeat'] > 90 or interrupted.exists()):
        # Keep interruption terminal without racing the runner's atomic writer.
        interrupted.touch(exist_ok=True)
        (directory / 'stop').write_text('stop', encoding='ascii')
        for receipt in value.get('receipts', []):
            if receipt['status'] == 'started':
                receipt['status'] = 'unknown'
        value.update(status='failed', error='采集进程已退出或失去响应，已有结果仍可同步',
                     finished_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    elif value['status'] in ACTIVE and (directory / 'stop').exists():
        value['status'] = 'stopping'
    value['total'] = len(value['items'])
    return value


def launch_runner(task_id, directory, preferences, *, platform, label,
                  node_path, node_argument, command, browser_profile=None):
    initial = {'task_id': task_id, 'run_id': task_id, 'status': 'starting', 'items': [],
               'total': 0, 'error': '', 'finished_at': None, 'heartbeat': time.time(),
               'candidates': [], 'receipts': [], 'collection_mode': preferences.get('collection_mode', 'collect'),
               'platform_state': {platform: {'phase': 'starting', 'message': '正在打开采集浏览器'}}}
    if platform == 'boss':
        initial['platform_state'][platform]['browser_mode'] = preferences.get('browser_mode', 'managed')
        if preferences.get('browser_mode') == 'chrome_use':
            initial['platform_state'][platform]['message'] = '正在连接现有 Chrome'
    target = directory / 'result.json'
    target.write_text(json.dumps(initial, ensure_ascii=False), encoding='utf-8')
    try:
        node = node_path()
        config = {'task_id': task_id, 'preferences': preferences,
                  'outputPath': node_argument(target, node),
                  'stopPath': node_argument(directory / 'stop', node)}
        if browser_profile is not None:
            config['browserProfilePath'] = node_argument(browser_profile, node)
        if platform == 'boss':
            config['resumePath'] = node_argument(directory / 'resume', node)
        config_path = directory / 'config.json'
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        with (directory / 'runner.log').open('ab') as log:
            return subprocess.Popen(command('--config', node_argument(config_path, node)),
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        initial.update(status='failed', error=f'无法启动{label}采集进程',
                       finished_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        target.write_text(json.dumps(initial, ensure_ascii=False), encoding='utf-8')
        raise RuntimeError(initial['error']) from exc
