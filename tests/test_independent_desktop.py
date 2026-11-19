"""Portable setup, migration snapshots and recoverable offline replacement."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect, init_db, SCHEMA_VERSION
from app import recovery
from app.support import diagnostic_report
from desktop_support import DesktopManager, read_boss_settings
from export_storage import _write_json_atomic


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / 'live' / 'radar.db'
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(path))
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO companies(name) VALUES('Original')")
    return path


def selected_backup(tmp_path, db):
    selected = tmp_path / 'chosen.sqlite'
    with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(selected)) as dest:
        source.backup(dest)
        dest.execute("UPDATE companies SET name='Restored'")
        dest.commit()
    return selected


def company(db):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute('SELECT name FROM companies').fetchone()[0]


def test_prepare_is_read_only_and_restore_preserves_complete_backup(tmp_path, db):
    source = selected_backup(tmp_path, db)
    folder, summary = recovery.prepare_backup(source, db.parent / '.restore-staging')
    assert summary['companies'] == 1 and company(db) == 'Original'
    source.write_bytes(b'changed after preview')
    safety = recovery.install_prepared(folder, db)
    assert company(db) == 'Restored'
    original, _ = recovery.prepare_backup(safety, db.parent / '.restore-staging')
    assert company(original / 'ready/radar.db') == 'Original'
    assert not (db.parent / '.restore-journal.json').exists()


def test_restore_failure_rolls_back_original_database_and_attachments(tmp_path, db, monkeypatch):
    evidence = db.parent / 'capture-evidence'
    evidence.mkdir();(evidence / 'unrelated.png').write_bytes(b'keep')
    folder, _ = recovery.prepare_backup(selected_backup(tmp_path, db), db.parent / '.restore-staging')
    replace = recovery.os.replace
    def fail(source, target):
        if Path(source) == folder / 'ready/radar.db':
            raise PermissionError('simulated file lock')
        return replace(source, target)
    monkeypatch.setattr(recovery.os, 'replace', fail)
    with pytest.raises(PermissionError):
        recovery.install_prepared(folder, db)
    assert company(db) == 'Original'
    assert (evidence / 'unrelated.png').read_bytes() == b'keep'
    assert list((db.parent / 'backups').glob('before-restore-*.zip'))


@pytest.mark.parametrize('after_move', [False, True])
def test_interrupted_restore_rolls_back_on_next_start(tmp_path, db, after_move):
    folder, _ = recovery.prepare_backup(selected_backup(tmp_path, db), db.parent / '.restore-staging')
    old = folder / 'previous';old.mkdir()
    evidence=db.parent/'capture-evidence';evidence.mkdir();(evidence/'keep.png').write_bytes(b'old')
    _write_json_atomic(db.parent / '.restore-journal.json', {'folder':str(folder),'existed':[True,True,False]})
    if after_move:
        db.replace(old / 'radar.db')
        (folder/'ready/radar.db').replace(db)
    recovery.recover_interrupted(db)
    assert company(db) == 'Original'
    assert (evidence/'keep.png').read_bytes()==b'old'


def test_restore_rejects_future_schema_missing_images_and_bad_archive(tmp_path, db):
    source=selected_backup(tmp_path,db)
    with closing(sqlite3.connect(source)) as conn:
        conn.execute(f'PRAGMA user_version={SCHEMA_VERSION+1}')
    with pytest.raises(ValueError,match='更新'):
        recovery.prepare_backup(source,db.parent/'.restore-staging')
    with closing(sqlite3.connect(source)) as conn:
        conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        batch=conn.execute("INSERT INTO capture_batches(label) VALUES('x')").lastrowid
        conn.execute('INSERT INTO capture_items(batch_id,fingerprint,screenshot_file) VALUES(?,?,?)',(batch,'x','a'*32+'.png'))
        conn.commit()
    with pytest.raises(ValueError,match='截图'):
        recovery.prepare_backup(source,db.parent/'.restore-staging')
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(bad,'w') as archive:
        archive.write(source,'radar.sqlite');archive.writestr('../escape','x')
    with pytest.raises(ValueError,match='不允许'):
        recovery.prepare_backup(bad,db.parent/'.restore-staging')
    assert company(db)=='Original' and not (tmp_path/'escape').exists()


def test_upgrade_creates_restorable_snapshot_once_and_refuses_downgrade(tmp_path,db):
    with closing(sqlite3.connect(db)) as conn:conn.execute('PRAGMA user_version=20')
    init_db()
    backups=list((db.parent/'backups').glob('before-upgrade-*.zip'))
    assert len(backups)==1
    folder,summary=recovery.prepare_backup(backups[0],db.parent/'.restore-staging')
    assert summary['schema_version']==20 and company(folder/'ready/radar.db')=='Original'
    init_db();assert len(list((db.parent/'backups').glob('before-upgrade-*.zip')))==1
    with closing(sqlite3.connect(db)) as conn:conn.execute(f'PRAGMA user_version={SCHEMA_VERSION+1}')
    with pytest.raises(RuntimeError,match='更新版本'):init_db()


def test_boss_is_unconfigured_on_clean_machine_and_never_guesses_a_path(tmp_path,monkeypatch,db):
    monkeypatch.delenv('RADAR_BOSSHUNTER_WSL_DISTRO',raising=False)
    monkeypatch.delenv('RADAR_BOSSHUNTER_WSL_SCRIPT',raising=False)
    assert read_boss_settings(tmp_path)=={'distro':'','script':''}
    manager=DesktopManager(tmp_path,'http://127.0.0.1:9',db)
    monkeypatch.setattr('desktop_support.shutil.which',lambda _:None)
    assert not manager.boss_settings()['configured']
    with pytest.raises(ValueError):manager.start_boss_connector()
    manager.save_boss_settings('My-Linux','/home/me/radar/start.sh')
    assert manager.boss_settings()['configured']
    with pytest.raises(ValueError,match='尚未安装'):manager.start_boss_connector()


def test_diagnostics_include_metadata_not_log_contents(tmp_path,db):
    log=tmp_path/'desktop.log'
    log.write_text('2026-10-26 12:00:00,001 ERROR Cookie=fixture-cookie-value JD=fixture-jd-body C:/Users/fixture-account\nfixture@example.com\n','utf-8')
    report=diagnostic_report(log)
    assert report['database_ok'] and len(report['error_log_summary'])==1
    text=json.dumps(report)
    for sensitive_value in ('fixture-cookie-value', 'fixture-jd-body', 'fixture-account', 'fixture@example.com'):
        assert sensitive_value not in text


def test_quick_add_creates_company_and_preserves_changed_source_for_review(db):
    with TestClient(app) as client:
        row={'company':'New company','title':'Intern','jd_text':'Original JD','source_url':'https://example.com/new'}
        response=client.post('/api/import/jobs',json={'rows':[row],'dry_run':False})
        assert response.status_code==200 and response.json()['counts']['new']==1
        row['jd_text']='Changed JD'
        assert client.post('/api/import/jobs',json={'rows':[row],'dry_run':False}).json()['counts']['review']==1
        job=client.get('/api/jobs').json()[0]
        assert job['jd_text']=='Original JD'
        exported=client.post('/api/exports/jobs',json={'ids':[job['id']],'intent':'jd_extraction'})
        assert 'task_instructions' in exported.json()
        assert exported.json()['package_id']


def test_native_restore_closes_server_before_install_and_restarts(tmp_path, db, monkeypatch):
    import desktop
    import sys
    import types
    source = selected_backup(tmp_path, db)
    events = []
    window = types.SimpleNamespace(create_file_dialog=lambda *a, **k: [str(source)], destroy=lambda: events.append('window_closed'))
    fake = types.ModuleType('webview')
    fake.windows = [window];fake.OPEN_DIALOG = 10
    saved = {}
    fake.create_window = lambda *a, **k: saved.update(manager=k['js_api'])
    def start(**kwargs):
        preview = saved['manager'].choose_restore_backup()
        assert preview['companies'] == 1
        saved['manager'].confirm_restore(preview['token'])
    fake.start = start
    monkeypatch.setitem(sys.modules, 'webview', fake)
    monkeypatch.setattr(sys, 'argv', ['desktop.py'])
    monkeypatch.setattr(desktop, 'configure_logging', lambda: tmp_path/'desktop.log')
    monkeypatch.setattr(desktop, 'user_data_directory', lambda: tmp_path/'user')
    monkeypatch.setattr(desktop, '_single_instance_handle', lambda: True)
    monkeypatch.setattr(desktop, '_release_single_instance', lambda _: events.append('released'))
    listener = types.SimpleNamespace(getsockname=lambda: ('127.0.0.1', 12345), close=lambda: None)
    worker = types.SimpleNamespace(is_alive=lambda: False)
    monkeypatch.setattr(desktop, 'bind_loopback', lambda: listener)
    monkeypatch.setattr(desktop, 'start_backend', lambda _: (object(), worker))
    monkeypatch.setattr(desktop, 'stop_backend', lambda *a: events.append('server_stopped'))
    monkeypatch.setattr('desktop_support.threading.Timer', lambda delay, callback: types.SimpleNamespace(start=callback))
    monkeypatch.setattr(app.state, 'restoring', False, raising=False)
    install = recovery.install_prepared
    def install_checked(*args):
        assert events[:2] == ['window_closed', 'server_stopped']
        return install(*args)
    monkeypatch.setattr(recovery, 'install_prepared', install_checked)
    monkeypatch.setattr(desktop.subprocess, 'Popen', lambda *a, **k: events.append('restarted'))
    assert desktop.main() == 0
    assert company(db) == 'Restored'
    assert events.index('released') < events.index('restarted')


def test_native_restore_requires_preview_and_refuses_running_collection(tmp_path, db):
    manager=DesktopManager(tmp_path,'http://127.0.0.1:9',db)
    with pytest.raises(ValueError):
        manager.confirm_restore('made-up')
    # Offline install also checks again, so a task started after preview cannot be orphaned.
    folder,_=recovery.prepare_backup(selected_backup(tmp_path,db),db.parent/'.restore-staging')
    with connect() as conn:
        batch=conn.execute("INSERT INTO capture_batches(label) VALUES('active')").lastrowid
        conn.execute("INSERT INTO capture_search_runs(batch_id,status,platform,preferences_json) VALUES(?,'running','boss','{}')",(batch,))
    with pytest.raises(RuntimeError,match='采集任务'):
        recovery.install_prepared(folder,db)
    assert company(db)=='Original'
