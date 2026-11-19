"""Owned snapshots and process failures; no Node or browser is launched."""
import importlib
import json
import time
from types import SimpleNamespace

import pytest

from app import shixiseng_runtime as runtime


TASK = 'a' * 32
OTHER = 'b' * 32


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'radar.db'))
    monkeypatch.setattr(runtime, '_processes', {})


def snapshot(task_id=TASK, **extra):
    value = {'task_id': task_id, 'run_id': task_id, 'status': 'running',
             'items': [{'title': f'岗位{i}', 'url': f'https://www.shixiseng.com/intern/inn_{i}'} for i in range(5)],
             'total': 999, 'error': '', 'finished_at': None, 'heartbeat': time.time(),
             'platform_state': {'shixiseng': {'seen': 5}}}
    value.update(extra)
    directory = runtime.run_directory(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'result.json').write_text(json.dumps(value), encoding='utf-8')
    return value, directory


@pytest.mark.parametrize('task_id', ['../' + TASK, TASK + '/..', '/' + TASK, 'A' * 32, 'x', '', None])
def test_task_path_traversal_and_invalid_ids_are_rejected(task_id):
    with pytest.raises(ValueError):
        runtime.run_directory(task_id)


def test_foreign_manifest_does_not_authorize_reads_or_stop():
    value, directory = snapshot()
    value['task_id'] = OTHER
    (directory / 'result.json').write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(RuntimeError, match='归属'):
        runtime.result_page(TASK)
    with pytest.raises(RuntimeError, match='归属'):
        runtime.stop(TASK)
    assert not (directory / 'stop').exists()


def test_partial_result_pagination_keeps_exact_total():
    snapshot()
    page = runtime.result_page(TASK, limit=2, offset=2)
    assert page['task_id'] == TASK and page['run_id'] == TASK
    assert page['total'] == 5
    assert [v['title'] for v in page['items']] == ['岗位2', '岗位3']
    assert runtime.result_page(TASK, offset=8)['items'] == []
    assert len(runtime.result_page(TASK)['items']) == 5


@pytest.mark.parametrize('options', [{'limit': 0}, {'limit': 501}, {'offset': -1}, {'limit': True}, {'offset': False}])
def test_invalid_pagination_does_not_read_snapshots(options):
    with pytest.raises(ValueError, match='分页'):
        runtime.result_page(TASK, **options)


def test_stop_only_marks_the_requested_owned_task():
    _, directory = snapshot()
    _, other_directory = snapshot(OTHER)
    result = runtime.stop(TASK)
    assert result['status'] == 'stopping' and len(result['items']) == 5
    assert (directory / 'stop').read_text(encoding='ascii') == 'stop'
    assert not (other_directory / 'stop').exists()
    assert runtime.result_page(OTHER)['status'] == 'running'


def test_stop_terminal_task_is_idempotent():
    _, directory = snapshot(status='completed')
    assert runtime.stop(TASK)['status'] == 'completed'
    assert not (directory / 'stop').exists()


def test_fresh_module_reads_persisted_partial_results_without_process_table():
    snapshot(status='completed_with_errors', error='详情验证中断')
    loaded = importlib.reload(runtime)
    assert not loaded._processes
    result = loaded.result_page(TASK, limit=2, offset=1)
    assert result['status'] == 'completed_with_errors'
    assert result['error'] == '详情验证中断' and result['total'] == 5
    assert [v['title'] for v in result['items']] == ['岗位1', '岗位2']


def test_dead_child_is_failed_without_losing_partial_results():
    snapshot()
    runtime._processes[TASK] = SimpleNamespace(poll=lambda: 1)
    result = runtime.result_page(TASK)
    assert result['status'] == 'failed' and result['finished_at']
    assert result['total'] == 5 and len(result['items']) == 5
    assert not runtime.task_active(TASK)


def test_stale_heartbeat_recovery_preserves_partial_results_and_frees_active_slot():
    snapshot(heartbeat=time.time() - 91)
    assert runtime.result_page(TASK)['status'] == 'failed'
    assert runtime.result_page(TASK)['total'] == 5
    assert not runtime.task_active(None)
    snapshot(OTHER)
    assert runtime.task_active(None)


def test_missing_snapshot_does_not_invent_empty_success():
    with pytest.raises(RuntimeError, match='快照不存在'):
        runtime.result_page(TASK)


def test_popen_failure_writes_terminal_owned_snapshot(monkeypatch):
    monkeypatch.setattr(runtime, 'node_path', lambda: 'fixture-node')
    monkeypatch.setattr(runtime, 'command', lambda *args: ['fixture-node', *args])
    def unavailable(*args, **kwargs):
        raise OSError('fixture process unavailable')
    monkeypatch.setattr(runtime.subprocess, 'Popen', unavailable)
    with pytest.raises(RuntimeError):
        runtime.start_search({'keywords': ['AI'], 'cities': ['北京'], 'max_pages': {'shixiseng': 1}})
    root = runtime.database_path().parent / 'shixiseng-runs'
    manifests = list(root.glob('*/result.json'))
    assert len(manifests) == 1
    value = json.loads(manifests[0].read_text(encoding='utf-8'))
    assert value['status'] == 'failed'
    assert not runtime.task_active(None)


def test_missing_node_does_not_leave_a_starting_snapshot(monkeypatch):
    def unavailable():
        raise RuntimeError('fixture runtime missing')
    monkeypatch.setattr(runtime, 'node_path', unavailable)
    with pytest.raises(RuntimeError):
        runtime.start_search({'keywords': ['AI'], 'cities': ['北京'], 'max_pages': {'shixiseng': 1}})
    assert not runtime.task_active(None)
    root = runtime.database_path().parent / 'shixiseng-runs'
    values = [json.loads(path.read_text(encoding='utf-8')) for path in root.glob('*/result.json')]
    assert not values or all(value['status'] == 'failed' for value in values)


@pytest.mark.parametrize('heartbeat', [float('nan'), float('inf'), 'not-a-number'])
def test_invalid_heartbeat_cannot_keep_a_run_active_forever(heartbeat):
    snapshot(heartbeat=heartbeat)
    try:
        result = runtime.result_page(TASK)
    except (RuntimeError, ValueError):
        assert not runtime.task_active(None)
    else:
        assert result['status'] == 'failed'
        assert result['total'] == 5
        assert not runtime.task_active(None)


@pytest.mark.parametrize('value', [[], None, {'task_id': TASK, 'items': [], 'status': 'invented', 'heartbeat': 0}])
def test_malformed_manifests_are_reported_as_connector_errors(value):
    _, directory = snapshot()
    (directory / 'result.json').write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises((RuntimeError, ValueError)):
        runtime.result_page(TASK)
    assert not runtime.task_active(None)


def test_interrupted_task_does_not_revive_after_process_table_is_lost():
    _, directory = snapshot()
    runtime._processes[TASK] = SimpleNamespace(poll=lambda: 1)
    assert runtime.result_page(TASK)['status'] == 'failed'
    assert (directory / 'interrupted').exists()
    loaded = importlib.reload(runtime)
    assert loaded.result_page(TASK)['status'] == 'failed'
    assert loaded.result_page(TASK)['total'] == 5
    assert not loaded.task_active(None)


def test_stop_pending_marker_survives_reload_until_runner_finishes():
    snapshot()
    runtime.stop(TASK)
    loaded = importlib.reload(runtime)
    assert loaded.result_page(TASK)['status'] == 'stopping'
    snapshot(status='stopped')
    assert loaded.result_page(TASK)['status'] == 'stopped'


@pytest.mark.parametrize('extra', [{'run_id': OTHER}, {'items': [{}] * 61}])
def test_foreign_run_or_excessive_results_do_not_bypass_snapshot_validation(extra):
    snapshot(**extra)
    with pytest.raises(RuntimeError):
        runtime.result_page(TASK)
    assert not runtime.task_active(None)


def candidate(url='https://www.shixiseng.com/intern/inn_fixture', **extra):
    return {'url': url, 'title': 'AI 产品实习', 'company': '样例企业', 'city': '北京',
            'hits': [{'keyword': 'AI', 'city': '北京', 'page': 1}], 'detail_state': 'pending', **extra}


def receipt(**extra):
    return {'id': f'{TASK}-1', 'kind': 'detail', 'status': 'started',
            'url': 'https://www.shixiseng.com/intern/inn_fixture',
            'started_at': '2026-10-09T10:00:00.000Z', 'finished_at': None, 'error': '', **extra}


def test_staged_payload_is_not_paginated_with_items():
    snapshot(candidates=[candidate()], receipts=[receipt()])
    result = runtime.result_page(TASK, limit=1, offset=4)
    assert len(result['items']) == 1
    assert result['candidates'] == [candidate()]
    assert result['receipts'] == [receipt()]


def test_cache_expansion_can_contain_more_than_old_sixty_item_cap():
    items = [{'title': f'岗位{i}', 'url': f'https://www.shixiseng.com/intern/inn_{i}'} for i in range(100)]
    snapshot(items=items, status='completed')
    assert runtime.result_page(TASK)['total'] == 100


@pytest.mark.parametrize('extra', [
    {'candidates': [candidate(detail_state='invented')]},
    {'candidates': [candidate(hits=[{'keyword': 'AI', 'city': '北京', 'page': True}])]},
    {'candidates': [candidate(), candidate()]},
    {'receipts': [receipt(url='https://evil.test/intern/inn_fixture')]},
    {'receipts': [receipt(kind='other')]},
    {'receipts': [receipt(started_at='invalid')]},
    {'receipts': [receipt(status='completed', finished_at=None)]},
    {'receipts': [receipt(), receipt()]},
    {'items': [{'url': 'https://evil.test/intern/inn_fixture', 'title': '岗位'}]},
    {'items': [{'url': 'https://www.shixiseng.com/intern/inn_fixture', 'title': '岗位', 'jd': 9}]},
])
def test_staged_payload_validation_rejects_malformed_data(extra):
    snapshot(**extra)
    with pytest.raises(RuntimeError):
        runtime.result_page(TASK)


def test_interrupted_started_receipt_is_unknown_without_claiming_it_failed():
    snapshot(receipts=[receipt()])
    runtime._processes[TASK] = SimpleNamespace(poll=lambda: 1)
    result = runtime.result_page(TASK)
    assert result['status'] == 'failed'
    assert result['receipts'][0]['status'] == 'unknown'
    assert runtime.result_page(TASK)['receipts'][0]['status'] == 'unknown'


@pytest.mark.parametrize('preferences', [
    {'collection_mode': 'other'}, {'collection_mode': 'hydrate', 'max_jobs': 0},
    {'collection_mode': 'discover', 'max_list_attempts': 0},
    {'collection_mode': 'hydrate', 'candidates': [{'url': 'https://evil.test'}]},
    {'collection_mode': 'hydrate', 'deadline_at': True},
    {'collection_mode': 'hydrate', 'detail_cache': [{'url': 'https://www.shixiseng.com/intern/inn_fixture', 'item': '甲' * (6 * 1024 * 1024)}]},
])
def test_invalid_staged_preferences_fail_before_process_launch(preferences, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('must not launch')
    monkeypatch.setattr(runtime.subprocess, 'Popen', unexpected)
    with pytest.raises(ValueError):
        runtime.start_search(preferences)


def test_hydrate_accepts_zero_listing_budget():
    runtime._validate_preferences({'collection_mode': 'hydrate', 'max_list_attempts': 0})
