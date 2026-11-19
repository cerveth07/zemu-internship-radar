"""Native BOSS ownership, human continuation and restorable partial results."""
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect
from app import boss_runtime as runtime, external_collectors, search_runs

TASK = 'a' * 32
URL = 'https://www.zhipin.com/job_detail/fixture~1.html'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'live' / 'radar.db'))
    monkeypatch.setattr(runtime, '_processes', {})
    with TestClient(app) as client:
        yield client


def preferences(**extra):
    return {'keywords': ['AI 产品', 'Agent'], 'cities': ['北京'],
            'max_pages': {'boss': 1}, 'max_jobs': 20, 'internship_only': True, **extra}


def snapshot(task_id=TASK, **extra):
    value = {'task_id': task_id, 'run_id': task_id, 'status': 'running',
             'heartbeat': time.time(), 'total': 1, 'error': '', 'finished_at': None,
             'items': [{'title': 'AI 产品实习生', 'company': '样例公司', 'city': '北京',
                        'url': URL, 'jd': '负责产品分析，熟悉 Python', 'jd_available': True}],
             'candidates': [], 'receipts': [], 'platform_state': {'boss': {'phase': 'login_required'}}}
    value.update(extra)
    folder = runtime.run_directory(task_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'result.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return value, folder


def batch(client):
    result = client.post('/api/capture/batches', json={
        'platform': 'boss', 'keyword': 'AI 产品,Agent', 'city': '北京'})
    assert result.status_code == 201, result.text
    return result.json()['id']


@pytest.fixture
def native_service(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, 'readiness', lambda browser_mode='managed': {'ok': True, 'checks': [], 'messages': []})
    monkeypatch.setattr(search_runs, 'uuid4', lambda: SimpleNamespace(hex=('a' if not calls else 'b') * 32))
    def start(config, task_id=None):
        task = task_id or ('a' if not calls else 'b') * 32
        calls.append(config)
        snapshot(task)
        return {'task': {'id': task, 'mode': config.get('collection_mode', 'collect'), 'status': 'running'}}
    monkeypatch.setattr(runtime, 'start_search', start)
    def forbidden(*args, **kwargs):
        raise AssertionError('A native task must not access BossHunter')
    monkeypatch.setattr(external_collectors, 'request_bosshunter', forbidden)
    return calls


def start_native(client, batch_id):
    result = client.post('/api/capture/connectors/boss-native/start-collect', json={
        'batch_id': batch_id, 'confirmed': True, 'max_jobs': 20})
    assert result.status_code == 200, result.text
    return result.json()['search_run']


def test_start_records_native_owner_and_keeps_home_request_metadata(client, native_service):
    bid = batch(client)
    result = search_runs.start_search(bid, preferences(entrypoint='home', request_id='request-1'),
                                      True, platform='boss', connector='native')
    run = result['search_run']
    assert run['platform'] == 'boss' and run['preferences']['connector'] == 'native'
    assert run['preferences']['entrypoint'] == 'home' and run['preferences']['request_id'] == 'request-1'
    assert native_service[0]['keywords'] == ['AI 产品', 'Agent']
    assert not result['settings_persist']
    assert client.get(f'/api/capture/search-runs/{run["id"]}').json()['platform_state']['phase'] == 'login_required'


def test_human_resume_sync_stop_and_native_batch_retry(client, native_service):
    bid = batch(client)
    run = start_native(client, bid)
    resumed = client.post(f'/api/capture/search-runs/{run["id"]}/resume', json={'confirmed': True})
    assert resumed.status_code == 200, resumed.text
    assert (runtime.run_directory(TASK) / 'resume').read_text() == 'resume'
    assert len(native_service) == 1  # continuation never launches a second task
    sync = f'/api/capture/search-runs/{run["id"]}/sync'
    first = client.post(sync, json={'confirmed': True}).json()
    assert first['created'] == 1 and first['search_run']['synced_count'] == 1
    second = client.post(sync, json={'confirmed': True}).json()
    assert second['created'] == 0 and second['scanned'] == 0
    items = client.get(f'/api/capture/batches/{bid}/items?run_id={run["id"]}').json()
    assert len(items) == 1 and items[0]['status'] == 'pending'
    assert items[0]['source_url'] == URL and items[0]['raw_text'] == '负责产品分析，熟悉 Python'
    status = client.get('/api/capture/connectors/bosshunter/status', params={'batch_id': bid}).json()
    assert status['connector'] == 'native' and status['connected']
    preview = client.post('/api/capture/connectors/bosshunter/search-preview', json={'batch_id': bid}).json()
    assert preview['connector'] == 'native' and preview['preferences']['max_jobs'] == 20
    stopped = client.post(f'/api/capture/search-runs/{run["id"]}/stop', json={'confirmed': True})
    assert stopped.json()['search_run']['status'] == 'stopping'
    assert client.post(f'/api/capture/search-runs/{run["id"]}/resume', json={'confirmed': True}).status_code == 409
    snapshot(status='stopped')
    again = client.post('/api/capture/connectors/bosshunter/start-collect', json={'batch_id': bid, 'confirmed': True})
    assert again.status_code == 200, again.text
    assert again.json()['search_run']['preferences']['connector'] == 'native'
    assert len(native_service) == 2


def test_legacy_boss_tasks_do_not_switch_to_native(client, monkeypatch):
    bid = batch(client)
    with connect() as db:
        rid = db.execute("""INSERT INTO capture_search_runs
            (batch_id,task_id,run_id,preferences_json,platform,status)
            VALUES(?,'legacy-task','legacy-run','{}','boss','running')""", (bid,)).lastrowid
    called = []
    def legacy(path, **kwargs):
        called.append(path)
        return {'task_id': 'legacy-task', 'run_id': 'legacy-run', 'items': [], 'total': 0,
                'status': 'completed', 'platform_state': {}, 'error': '', 'finished_at': None}
    monkeypatch.setattr(external_collectors, 'request_bosshunter', legacy)
    monkeypatch.setattr(runtime, 'result_page', lambda *a, **k: pytest.fail('must keep historical owner'))
    assert client.get(f'/api/capture/search-runs/{rid}').json()['status'] == 'completed'
    assert called == ['/api/radar/tasks/legacy-task/results?limit=1&offset=0']
    assert client.post(f'/api/capture/search-runs/{rid}/resume', json={'confirmed': True}).status_code == 409


def test_incomplete_native_jd_and_eligibility_survive_sync(client, native_service):
    bid = batch(client)
    run = start_native(client, bid)
    value, folder = snapshot()
    value['items'][0].update(jd='原文含未识别字符\ue123', jd_available=False, eligibility='本科在读')
    (folder / 'result.json').write_text(json.dumps(value), encoding='utf-8')
    synced = client.post(f'/api/capture/search-runs/{run["id"]}/sync', json={'confirmed': True}).json()
    assert synced['created'] == 1 and synced['missing_jd'] == 1
    draft = client.get(f'/api/capture/batches/{bid}/items').json()[0]['draft']
    assert draft['jd_available'] is False and draft['eligibility'] == '本科在读'


def test_missing_native_snapshot_releases_slot_but_keeps_synced_data(client, native_service):
    bid = batch(client)
    run = start_native(client, bid)
    client.post(f'/api/capture/search-runs/{run["id"]}/sync', json={'confirmed': True})
    (runtime.run_directory(TASK) / 'result.json').unlink()
    current = client.get(f'/api/capture/search-runs/{run["id"]}').json()
    assert current['status'] == 'failed' and current['synced_count'] == 1
    assert len(client.get(f'/api/capture/batches/{bid}/items').json()) == 1
    assert start_native(client, bid)['task_id'] == 'b' * 32


def test_reserved_native_id_waits_for_initial_snapshot_then_recovers_if_abandoned(client):
    bid = batch(client)
    with connect() as db:
        rid = db.execute("""INSERT INTO capture_search_runs
            (batch_id,task_id,preferences_json,platform,status)
            VALUES(?, ?, ?, 'boss', 'starting')""",
            (bid, TASK, json.dumps(preferences(connector='native')))).lastrowid
    initial = client.get(f'/api/capture/search-runs/{rid}').json()
    assert initial['status'] == 'starting'
    with connect() as db:
        db.execute("UPDATE capture_search_runs SET created_at=datetime('now','-10 minutes') WHERE id=?", (rid,))
    recovered = client.get(f'/api/capture/search-runs/{rid}').json()
    assert recovered['status'] == 'failed' and recovered['task_id'] == TASK


def test_owned_native_backup_restores_results_without_browser_credentials(client, native_service, tmp_path, monkeypatch):
    bid = batch(client)
    run = start_native(client, bid)
    root = runtime.run_directory(TASK).parent.parent
    private = root / 'BossRadarChrome' / 'Default'
    private.mkdir(parents=True)
    (private / 'Cookies').write_text('private cookie data')
    (runtime.run_directory(TASK) / 'config.json').write_text('{"private":"configuration"}')
    response = client.get('/api/capture/backup')
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert f'boss-runs/{TASK}/result.json' in archive.namelist()
        assert all('Chrome' not in name and 'config.json' not in name and 'runner.log' not in name for name in archive.namelist())
    saved = tmp_path / 'backup.zip';saved.write_bytes(response.content)
    from tools.restore_capture_bundle import restore_bundle
    destination = tmp_path / 'restored' / 'radar.db'
    restore_bundle(saved, destination)
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(destination))
    restored = runtime.result_page(TASK)
    assert restored['status'] == 'failed' and restored['total'] == 1
    assert not (destination.parent / 'BossRadarChrome').exists()
    assert client.post(f'/api/capture/search-runs/{run["id"]}/sync', json={'confirmed': True}).json()['created'] == 1


def test_backup_rejects_native_snapshot_claimed_by_external_run(client, tmp_path):
    bid = batch(client)
    value, _ = snapshot(status='completed')
    with connect() as db:
        db.execute("""INSERT INTO capture_search_runs
            (batch_id,task_id,run_id,preferences_json,platform,status)
            VALUES(?,?,?,'{}','boss','completed')""", (bid, TASK, TASK))
    database = client.get('/api/backup/sqlite').content
    archive = tmp_path / 'unowned.zip'
    with zipfile.ZipFile(archive, 'w') as target:
        target.writestr('radar.sqlite', database)
        target.writestr(f'boss-runs/{TASK}/result.json', json.dumps(value))
    from tools.restore_capture_bundle import restore_bundle
    destination = tmp_path / 'wrong' / 'radar.db'
    with pytest.raises(ValueError, match='归属'):
        restore_bundle(archive, destination)
    assert not destination.exists()


@pytest.mark.parametrize('extra', [
    {'cities': ['不存在的城市']}, {'keywords': ['x'] * 2},
    {'max_pages': {'boss': True}}, {'max_jobs': 0},
    {'keywords': [str(i) for i in range(9)], 'cities': ['北京', '上海']},
    {'collection_mode': 'hydrate'}, {'internship_only': 'true'},
    {'browser_mode': 'arbitrary'}, {'browser_mode': ['chrome_use']},
])
def test_invalid_preferences_are_rejected_before_launch(client, monkeypatch, extra):
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch invalid request'))
    with pytest.raises(ValueError):
        runtime.start_search(preferences(**extra))
    assert not list(runtime.database_path().parent.glob('boss-runs/*/result.json'))


def test_native_launch_uses_owned_profile_and_resume_file(client, monkeypatch):
    monkeypatch.setattr(runtime, 'node_path', lambda: 'fixture-node')
    monkeypatch.setattr(runtime, 'command', lambda *args: ['fixture-node', 'boss_runner.mjs', *args])
    launched = []
    def popen(command, **kwargs):
        launched.append(command)
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(runtime.subprocess, 'Popen', popen)
    result = runtime.start_search(preferences())
    task = result['task']['id']
    folder = runtime.run_directory(task)
    config = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
    assert Path(config['browserProfilePath']) == runtime.database_path().parent / 'BossRadarChrome'
    assert Path(config['resumePath']) == folder / 'resume'
    assert Path(config['outputPath']) == folder / 'result.json'
    assert launched[0][:3] == ['fixture-node', 'boss_runner.mjs', '--config']
    assert runtime.read_snapshot(task)['task_id'] == task


def test_chrome_use_launch_keeps_resume_channel_without_creating_managed_profile(client, monkeypatch):
    monkeypatch.setattr(runtime, 'node_path', lambda: 'fixture-node')
    monkeypatch.setattr(runtime, 'command', lambda *args: ['fixture-node', 'boss_runner.mjs', *args])
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a, **kw: SimpleNamespace(poll=lambda: None))
    run = runtime.start_search(preferences(browser_mode='chrome_use'))
    folder = runtime.run_directory(run['task']['id'])
    config = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
    assert config['preferences']['browser_mode'] == 'chrome_use'
    assert 'browserProfilePath' not in config
    assert Path(config['resumePath']) == folder / 'resume'
    assert not (runtime.database_path().parent / 'BossRadarChrome').exists()
    snapshot(run['task']['id'])
    runtime.resume(run['task']['id'])
    assert (folder / 'resume').read_text() == 'resume'


@pytest.mark.parametrize('mode', ['chrome_use', 'managed'])
def test_native_batch_preview_status_and_retry_keep_browser_mode(client, native_service, monkeypatch, mode):
    bid = batch(client)
    first = client.post('/api/capture/connectors/boss-native/start-collect', json={
        'batch_id': bid, 'confirmed': True, 'browser_mode': mode}).json()['search_run']
    assert first['preferences']['browser_mode'] == mode
    snapshot(first['task_id'], status='completed')
    checked = []
    def ready(browser_mode='managed'):
        checked.append(browser_mode)
        return {'ok': True, 'checks': [], 'messages': []}
    monkeypatch.setattr(runtime, 'readiness', ready)
    status = client.get('/api/capture/connectors/bosshunter/status', params={'batch_id': bid, 'preflight': True})
    assert status.json()['browser_mode'] == mode
    native_status = client.get('/api/capture/connectors/boss-native/status', params={
        'batch_id': bid, 'preflight': True, 'browser_mode': 'managed' if mode == 'chrome_use' else 'chrome_use'})
    assert native_status.json()['browser_mode'] == mode  # A status request cannot reassign an existing task.
    for connector in ('boss-native', 'bosshunter'):
        preview = client.post(f'/api/capture/connectors/{connector}/search-preview', json={'batch_id': bid})
        assert preview.json()['preferences']['browser_mode'] == mode
    retry = client.post('/api/capture/connectors/bosshunter/start-collect', json={'batch_id': bid, 'confirmed': True})
    assert retry.status_code == 200, retry.text
    assert retry.json()['search_run']['preferences']['browser_mode'] == mode
    assert checked == [mode, mode, mode]


def test_older_native_run_without_browser_mode_keeps_managed_on_retry(client, native_service):
    bid = batch(client)
    run = start_native(client, bid)
    with connect() as db:
        saved = run['preferences'];saved.pop('browser_mode', None)
        db.execute('UPDATE capture_search_runs SET preferences_json=? WHERE id=?', (json.dumps(saved), run['id']))
    snapshot(run['task_id'], status='completed')
    preview = client.post('/api/capture/connectors/boss-native/search-preview', json={'batch_id': bid})
    assert preview.json()['preferences']['browser_mode'] == 'managed'
    retry = client.post('/api/capture/connectors/bosshunter/start-collect', json={'batch_id': bid, 'confirmed': True})
    assert retry.json()['search_run']['preferences']['browser_mode'] == 'managed'


def test_readiness_queries_selected_mode_once_without_browser_launch(monkeypatch):
    commands = []
    monkeypatch.setattr(runtime, 'command', lambda *args: ['fixture-node', 'boss_runner.mjs', *args])
    def status(command, **kwargs):
        commands.append(command)
        assert kwargs['timeout'] <= 20
        return SimpleNamespace(returncode=1, stdout=json.dumps({'ready': False, 'message': '请连接 Chrome 扩展'}))
    monkeypatch.setattr(runtime.subprocess, 'run', status)
    checked = runtime.readiness('chrome_use')
    assert not checked['ok'] and checked['browser_mode'] == 'chrome_use'
    assert commands == [['fixture-node', 'boss_runner.mjs', '--doctor', '--browser-mode', 'chrome_use']]
    runtime.readiness()
    assert commands[-1] == ['fixture-node', 'boss_runner.mjs', '--doctor']


def test_browser_cleanup_state_is_validated_and_preserved_on_refresh(client, native_service):
    run = client.post('/api/capture/connectors/boss-native/start-collect', json={
        'batch_id': batch(client), 'confirmed': True, 'browser_mode': 'chrome_use'}).json()['search_run']
    snapshot(run['task_id'], status='completed', platform_state={'boss': {
        'phase': '检索结束', 'browser_mode': 'chrome_use', 'tabs_opened': 2, 'tabs_closed': 2,
        'cleanup_status': 'complete', 'cleanup_message': '已关闭本次检索标签', 'untrusted': 'discard'}})
    state = client.get(f"/api/capture/search-runs/{run['id']}").json()['platform_state']
    assert state == {'phase': '检索结束', 'browser_mode': 'chrome_use', 'tabs_opened': 2, 'tabs_closed': 2,
                     'cleanup_status': 'complete', 'cleanup_message': '已关闭本次检索标签'}
    assert search_runs.safe_platform_state({'boss': {'browser_mode': {}, 'tabs_opened': True,
        'tabs_closed': -1, 'cleanup_status': 'unexpected', 'cleanup_message': {}}}) == {}
    message = search_runs.safe_platform_state({'boss': {'cleanup_message': 'cookie=secret'}})['cleanup_message']
    assert 'secret' not in message


@pytest.mark.parametrize('changes', [
    {'task_id': 'b' * 32}, {'run_id': 'b' * 32}, {'heartbeat': float('nan')},
    {'items': [{'title': '坏来源', 'url': 'https://evil.test/job_detail/a.html'}]},
    {'items': [{'title': '未规范URL', 'url': URL + '?securityId=secret'}]},
])
def test_native_snapshot_validation_guards_continuation(client, changes):
    value, folder = snapshot()
    value.update(changes)
    (folder / 'result.json').write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises((RuntimeError, ValueError)):
        runtime.resume(TASK)
    assert not (folder / 'resume').exists()
