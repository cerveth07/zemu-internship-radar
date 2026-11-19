"""Desktop launcher behavior without requiring Windows GUI on Linux test host."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

import desktop
from app.db import init_db


def seed_legacy(path: Path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(path))
    init_db()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO companies(name) VALUES ('Historical Startup')")
        cid = conn.execute("SELECT id FROM companies WHERE name='Historical Startup'").fetchone()[0]
        conn.execute("INSERT INTO jobs(company_id,title,jd_text) VALUES(?, 'Agent Intern', 'Needs Python')", (cid,))


def test_data_directory_is_persistent_user_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    monkeypatch.delenv('RADAR_PRIVATE_RC_DB_PATH', raising=False)
    assert desktop.user_data_directory() == tmp_path / 'appdata' / 'InternshipRadarPrivateRC'
    assert desktop.bundled_root() / 'static' / 'index.html'  # only static assets may be bundled


def test_adjacent_legacy_database_migrated_once_without_destroying_original(tmp_path, monkeypatch):
    user_path = tmp_path / 'appdata' / 'InternshipRadarPrivateRC' / 'data' / 'radar.db'
    old_path = tmp_path / 'old-install' / 'data' / 'radar.db'
    old_path.parent.mkdir(parents=True)
    seed_legacy(old_path, monkeypatch)
    monkeypatch.delenv('RADAR_PRIVATE_RC_DB_PATH', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    resolved = desktop.configure_storage(executable_directory=tmp_path / 'old-install')
    assert resolved == user_path
    assert os.environ['RADAR_PRIVATE_RC_DB_PATH'] == str(user_path)
    for path in (old_path, user_path):
        with sqlite3.connect(path) as con:
            assert con.execute('SELECT title FROM jobs').fetchone()[0] == 'Agent Intern'
    with sqlite3.connect(user_path) as con:
        con.execute("INSERT INTO companies(name) VALUES ('After Migration')")
    assert not desktop.migrate_adjacent_database(user_path, tmp_path / 'old-install')
    with sqlite3.connect(user_path) as con:
        assert con.execute('SELECT COUNT(*) FROM companies').fetchone()[0] == 2
    with sqlite3.connect(old_path) as con:
        assert con.execute('SELECT COUNT(*) FROM companies').fetchone()[0] == 1


def test_environment_override_never_migrates(tmp_path, monkeypatch):
    custom = tmp_path / 'custom-db' / 'radar.db'
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(custom))
    assert desktop.configure_storage(executable_directory=tmp_path / 'irrelevant') == custom
    assert not (tmp_path / 'appdata' / 'InternshipRadarPrivateRC' / 'data' / 'radar.db').exists()


def test_invalid_adjacent_database_does_not_overwrite_destination(tmp_path):
    adjacent = tmp_path / 'binary' / 'data' / 'radar.db'
    adjacent.parent.mkdir(parents=True)
    adjacent.write_text('not-a-sqlite-database')
    destination = tmp_path / 'appdata' / 'radar.db'
    with pytest.raises(sqlite3.DatabaseError):
        desktop.migrate_adjacent_database(destination, tmp_path / 'binary')
    assert not destination.exists()
    assert adjacent.read_text() == 'not-a-sqlite-database'


def test_desktop_self_test_writes_report_and_restores_environment(tmp_path, monkeypatch):
    old = str(tmp_path / 'original.sqlite')
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', old)
    report = tmp_path / 'result.json'
    assert desktop.self_test(report) == 0
    summary = json.loads(report.read_text('utf-8'))
    assert summary['result'] == 'passed'
    assert summary['resources'] and summary['api'] and summary['database']
    assert summary['career_handoff'] is True
    assert summary['recovery'] is True
    assert summary['optional_connector'] is True
    assert os.environ['RADAR_PRIVATE_RC_DB_PATH'] == old
    assert not Path(old).exists()


def test_desktop_migration_copies_referenced_screenshots(tmp_path, monkeypatch):
    import secrets
    from PIL import Image
    from io import BytesIO

    old_database = tmp_path / 'old' / 'data' / 'radar.db'
    old_database.parent.mkdir(parents=True)
    seed_legacy(old_database, monkeypatch)
    image_name = secrets.token_hex(16) + '.png'
    with sqlite3.connect(old_database) as conn:
        batch = conn.execute("INSERT INTO capture_batches(label) VALUES('迁移附件')").lastrowid
        conn.execute('INSERT INTO capture_items(batch_id, fingerprint, screenshot_file) VALUES(?,?,?)',
                     (batch, secrets.token_hex(16), image_name))
    screenshot_dir = old_database.parent / 'capture-evidence'
    screenshot_dir.mkdir()
    buf = BytesIO()
    Image.new('RGB', (8, 8), '#224466').save(buf, 'PNG')
    (screenshot_dir / image_name).write_bytes(buf.getvalue())
    target = tmp_path / 'desktop' / 'data' / 'radar.db'
    assert desktop.migrate_adjacent_database(target, tmp_path / 'old')
    assert (target.parent / 'capture-evidence' / image_name).read_bytes() == buf.getvalue()
    assert (screenshot_dir / image_name).read_bytes() == buf.getvalue()
    with sqlite3.connect(target) as conn:
        assert conn.execute('SELECT screenshot_file FROM capture_items').fetchone()[0] == image_name


def test_desktop_migration_refuses_missing_screenshots(tmp_path, monkeypatch):
    old_database = tmp_path / 'old' / 'data' / 'radar.db'
    old_database.parent.mkdir(parents=True)
    seed_legacy(old_database, monkeypatch)
    with sqlite3.connect(old_database) as conn:
        batch = conn.execute("INSERT INTO capture_batches(label) VALUES('缺失附件')").lastrowid
        conn.execute('INSERT INTO capture_items(batch_id, fingerprint, screenshot_file) VALUES(?,?,?)',
                     (batch, 'fingerprint', 'a' * 32 + '.png'))
    target = tmp_path / 'desktop' / 'data' / 'radar.db'
    with pytest.raises(FileNotFoundError, match='截图缺失'):
        desktop.migrate_adjacent_database(target, tmp_path / 'old')
    assert not target.exists()


def test_desktop_migration_refuses_attachment_collision(tmp_path, monkeypatch):
    old_database = tmp_path / 'old' / 'data' / 'radar.db'
    old_database.parent.mkdir(parents=True)
    seed_legacy(old_database, monkeypatch)
    image_name = 'a' * 32 + '.png'
    with sqlite3.connect(old_database) as conn:
        batch = conn.execute("INSERT INTO capture_batches(label) VALUES('冲突附件')").lastrowid
        conn.execute('INSERT INTO capture_items(batch_id, fingerprint, screenshot_file) VALUES(?,?,?)',
                     (batch, 'fingerprint', image_name))
    (old_database.parent / 'capture-evidence').mkdir()
    (old_database.parent / 'capture-evidence' / image_name).write_bytes(b'first')
    target = tmp_path / 'desktop' / 'data' / 'radar.db'
    (target.parent / 'capture-evidence').mkdir(parents=True)
    existing = target.parent / 'capture-evidence' / image_name
    existing.write_bytes(b'other')
    with pytest.raises(FileExistsError, match='同名但不同内容'):
        desktop.migrate_adjacent_database(target, tmp_path / 'old')
    assert not target.exists() and existing.read_bytes() == b'other'


def test_packaged_ocr_self_test_rejects_missing_model(tmp_path, monkeypatch):
    from app import ocr_adapter
    monkeypatch.setattr(ocr_adapter, 'is_available', lambda: False)
    output = tmp_path / 'failed-ocr.json'
    assert desktop.self_test(output, require_ocr=True) == 1
    report = json.loads(output.read_text('utf-8'))
    assert report['api'] and report['database']
    assert report['result'] == 'failed' and 'RapidOCR' in report['error']


def test_packaged_ocr_self_test_accepts_recognized_chinese(tmp_path, monkeypatch):
    from app import ocr_adapter
    monkeypatch.setattr(ocr_adapter, 'is_available', lambda: True)
    monkeypatch.setattr(ocr_adapter, 'smoke_check',
                        lambda path: {'text': 'AI 产品经理实习岗位', 'line_count': 1})
    output = tmp_path / 'good-ocr.json'
    assert desktop.self_test(output, require_ocr=True) == 0
    report = json.loads(output.read_text('utf-8'))
    assert report['ocr'] == 'passed' and report['result'] == 'passed'
