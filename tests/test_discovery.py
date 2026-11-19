import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import connect, init_db
from app import discovery as d

URL = 'https://www.shixiseng.com/intern/inn_abc123'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'store.db'))
    init_db()
    with connect() as conn:
        batch = conn.execute("INSERT INTO capture_batches(label,platform) VALUES('test','shixiseng')").lastrowid
        rid = conn.execute("INSERT INTO capture_search_runs(batch_id,platform,preferences_json,status) VALUES(?,'shixiseng','{}','completed')", (batch,)).lastrowid
        yield conn, {'id': rid, 'batch_id': batch, 'platform': 'shixiseng'}


def snapshot(observed=None):
    now = observed or d.stamp()
    return {'updated_at': now, 'candidates': [{'url': URL, 'title': 'Agent', 'company': '', 'city': '', 'detail_state': 'complete',
            'hits': [{'keyword': 'Agent', 'city': '北京', 'page': 1}, {'keyword': '智能体', 'city': '上海', 'page': 2}]}],
            'items': [{'url': URL, 'title': 'Agent', 'company': '', 'city': '', 'jd': '产品设计', 'jd_available': True, 'observed_at': now, 'reused_snapshot': False}],
            'receipts': [{'id': 'l1', 'kind': 'list', 'status': 'completed', 'started_at': now, 'finished_at': now,
                          'keyword': 'Agent', 'city': '北京', 'page': 1},
                         {'id': 'd1', 'kind': 'detail', 'status': 'completed', 'started_at': now, 'finished_at': now, 'url': URL}]}


def test_idempotent_exact_identity_hits_costs(store):
    conn, run = store
    data = snapshot()
    d.ingest_snapshot(conn, run, data)
    d.ingest_snapshot(conn, run, data)
    info = d.public_run_info(conn, run['id'])
    assert len(info['candidates']) == 1
    assert len(info['candidates'][0]['hits']) == 2
    assert info['costs'] == {'list_attempts': 1, 'detail_attempts': 1, 'cache_hits': 0, 'unknown_attempts': 0}
    assert conn.execute('SELECT COUNT(*) FROM discovery_details').fetchone()[0] == 1
    assert info['candidates'][0]['city'] == ''
    assert d.pending_candidates(conn, run['batch_id']) == []


def test_cache_keeps_actual_detail_time_and_versions(store):
    conn, run = store
    first = snapshot()
    d.ingest_snapshot(conn, run, first)
    prefs = d.build_preferences({}, conn=conn)
    assert prefs['detail_cache'] == []
    assert prefs['force_refresh'] is True
    assert d.build_preferences({'force_refresh': False, 'detail_cache': ['stale']}, conn=conn)['detail_cache'] == []
    cached = snapshot()
    cached['items'][0].update(reused_snapshot=True, last_detail_at=first['items'][0]['observed_at'])
    cached['receipts'] = [{'id': 'c1', 'kind': 'cache', 'status': 'completed', 'started_at': d.stamp(), 'url': URL}]
    d.ingest_snapshot(conn, run, cached)
    source = conn.execute('SELECT * FROM discovery_sources').fetchone()
    assert source['last_detail_at'] == d.stamp(first['items'][0]['observed_at'])
    assert source['last_seen_at'] >= source['last_detail_at']
    assert conn.execute('SELECT COUNT(*) FROM discovery_details').fetchone()[0] == 1
    assert d.build_preferences({'force_refresh': True}, conn=conn)['detail_cache'] == []
    new = snapshot(d.stamp())
    new['items'][0]['jd'] = '更新产品职责'
    new['receipts'] = []
    d.ingest_snapshot(conn, run, new)
    assert conn.execute('SELECT COUNT(*) FROM discovery_details').fetchone()[0] == 2


def test_stale_or_incomplete_never_cached(store):
    conn, run = store
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    d.ingest_snapshot(conn, run, snapshot(old))
    assert d.build_preferences({}, conn=conn)['detail_cache'] == []
    data = snapshot()
    data['items'][0]['jd_available'] = False
    data['receipts'] = []
    d.ingest_snapshot(conn, run, data)
    assert d.build_preferences({}, conn=conn)['detail_cache'] == []
    assert d.pending_candidates(conn, run['batch_id'])[0]['detail_state'] == 'incomplete'


def test_closed_read_is_persisted_without_reusing_or_deleting_old_jd(store):
    conn, run = store
    first = snapshot((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    d.ingest_snapshot(conn, run, first)
    closed = snapshot()
    closed['items'] = []
    closed['receipts'] = []
    closed['candidates'][0].update(detail_state='closed', last_detail_at=closed['updated_at'])
    d.ingest_snapshot(conn, run, closed)
    info = d.public_run_info(conn, run['id'])
    assert info['candidates'][0]['detail_state'] == 'closed'
    assert info['candidates'][0]['last_detail_at'] == closed['updated_at']
    assert conn.execute('SELECT detail_state FROM discovery_sources').fetchone()[0] == 'closed'
    assert conn.execute('SELECT COUNT(*) FROM discovery_details').fetchone()[0] == 1
    assert d.pending_candidates(conn, run['batch_id']) == []


def test_validate_whole_snapshot_before_any_write(store):
    conn, run = store
    data = snapshot()
    data['receipts'][1]['url'] = 'https://attacker.invalid/intern/inn_abc'
    with pytest.raises(ValueError):
        d.ingest_snapshot(conn, run, data)
    assert conn.execute('SELECT COUNT(*) FROM discovery_sources').fetchone()[0] == 0
    data = snapshot()
    data['candidates'][0]['hits'][0]['page'] = True
    with pytest.raises(ValueError):
        d.ingest_snapshot(conn, run, data)
    assert conn.execute('SELECT COUNT(*) FROM discovery_sources').fetchone()[0] == 0


def test_receipt_terminal_does_not_regress_or_change_identity(store):
    conn, run = store
    data = snapshot()
    d.ingest_snapshot(conn, run, data)
    data['receipts'][0]['status'] = 'started'
    d.ingest_snapshot(conn, run, data)
    assert d.public_run_info(conn, run['id'])['costs']['unknown_attempts'] == 0
    data['receipts'][0]['keyword'] = '不同词'
    with pytest.raises(ValueError):
        d.ingest_snapshot(conn, run, data)
    assert d.public_run_info(conn, run['id'])['costs']['list_attempts'] == 1


def test_resume_tracks_pending_and_retains_unknown_fields(store):
    conn, run = store
    data = snapshot()
    data['items'] = []
    data['receipts'] = []
    data['candidates'][0]['detail_state'] = 'pending'
    d.ingest_snapshot(conn, run, data)
    candidates = d.pending_candidates(conn, run['batch_id'])
    assert len(candidates) == 1 and len(candidates[0]['hits']) == 2
    assert candidates[0]['city'] == '' and candidates[0]['last_detail_at'] is None
    d.ingest_snapshot(conn, run, {'items': []})
    assert d.public_run_info(conn, run['id'])['pending_count'] == 1


def test_identity_rejects_wrong_host_userinfo_and_canonicalizes_query():
    assert d.identity(URL + '?from=search')[1] == URL
    for bad in ['http://www.shixiseng.com/intern/inn_abc', 'https://user@www.shixiseng.com/intern/inn_abc', 'https://www.shixiseng.com.evil/intern/inn_abc']:
        with pytest.raises(ValueError):
            d.identity(bad)


def test_filtered_other_city_does_not_erase_pending_hit(store):
    conn, run = store
    data = snapshot()
    data['items'] = data['receipts'] = []
    data['candidates'][0].update(detail_state='pending', hits=[{'keyword': 'Agent', 'city': '北京', 'page': 1}])
    d.ingest_snapshot(conn, run, data)
    next_id = conn.execute("INSERT INTO capture_search_runs(batch_id,platform,preferences_json,status) VALUES(?,'shixiseng','{}','completed')", (run['batch_id'],)).lastrowid
    data['candidates'][0].update(detail_state='filtered', hits=[{'keyword': 'Agent', 'city': '上海', 'page': 1}])
    d.ingest_snapshot(conn, {**run, 'id': next_id}, data)
    pending = d.pending_candidates(conn, run['batch_id'])
    assert len(pending) == 1 and len(pending[0]['hits']) == 2


def test_old_or_cached_items_cannot_replace_latest_detail_fields(store):
    conn, run = store
    data = snapshot()
    d.ingest_snapshot(conn, run, data)
    old = snapshot((datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    old['candidates'] = old['receipts'] = []
    old['items'][0]['title'] = '旧岗位'
    d.ingest_snapshot(conn, run, old)
    assert conn.execute('SELECT title FROM discovery_sources').fetchone()[0] == 'Agent'
    cached = snapshot()
    cached['candidates'] = cached['receipts'] = []
    cached['items'][0].update(title='缓存旧名称', reused_snapshot=True, last_detail_at=old['items'][0]['observed_at'])
    d.ingest_snapshot(conn, run, cached)
    assert conn.execute('SELECT title FROM discovery_sources').fetchone()[0] == 'Agent'


def test_refresh_preserves_actual_listing_seen_time_and_legacy_does_not_advance(store):
    conn, run = store
    listed = d.stamp((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    data = snapshot()
    data['items'] = data['receipts'] = []
    data['candidates'][0].update(detail_state='pending', observed_at=listed)
    d.ingest_snapshot(conn, run, data)
    data['updated_at'] = d.stamp()
    d.ingest_snapshot(conn, run, data)
    assert conn.execute('SELECT last_seen_at FROM discovery_sources').fetchone()[0] == listed
    data.pop('updated_at')
    data['candidates'][0].pop('observed_at')
    d.ingest_snapshot(conn, run, data)
    assert conn.execute('SELECT last_seen_at FROM discovery_sources').fetchone()[0] == listed


def test_hydrate_success_resolves_prior_pending_batch_hits(store):
    conn, run = store
    data = snapshot()
    data['items'] = data['receipts'] = []
    data['candidates'][0]['detail_state'] = 'pending'
    d.ingest_snapshot(conn, run, data)
    pending = d.pending_candidates(conn, run['batch_id'])
    next_id = conn.execute("INSERT INTO capture_search_runs(batch_id,platform,preferences_json,status) VALUES(?,'shixiseng','{}','completed')", (run['batch_id'],)).lastrowid
    hydrated = snapshot()
    hydrated['candidates'][0]['hits'] = pending[0]['hits']
    d.ingest_snapshot(conn, {**run, 'id': next_id}, hydrated)
    assert d.pending_candidates(conn, run['batch_id']) == []


def test_unchanged_detail_read_advances_timestamp_without_new_version(store):
    conn, run = store
    older = d.stamp((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    first = snapshot(older)
    first['items'][0]['last_detail_at'] = older
    d.ingest_snapshot(conn, run, first)
    newer = d.stamp()
    repeated = snapshot(newer)
    repeated['items'][0].update(last_detail_at=newer, source_flags=['new-observation'])
    repeated['receipts'] = []
    d.ingest_snapshot(conn, run, repeated)
    assert conn.execute('SELECT COUNT(*) FROM discovery_details').fetchone()[0] == 1
    assert conn.execute('SELECT last_detail_at FROM discovery_sources').fetchone()[0] == newer
    assert d.build_preferences({}, conn=conn)['detail_cache'] == []
    changed = copy.deepcopy(repeated)
    changed['items'][0]['jd'] = '不同职责'
    d.ingest_snapshot(conn, run, changed)
    assert conn.execute('SELECT COUNT(*) FROM discovery_details').fetchone()[0] == 2
    assert d.build_preferences({}, conn=conn)['detail_cache'] == []

    assert json.loads(conn.execute('SELECT item_json FROM discovery_details ORDER BY rowid DESC').fetchone()[0])['jd'] == '不同职责'


def test_runner_bounded_list_fields_fit_storage_contract(store):
    conn,run=store
    data=snapshot()
    data['items']=[]
    data['candidates'][0]['title']='x'*2000
    data['candidates'][0]['detail_state']='pending'
    d.ingest_snapshot(conn,run,data)
    assert len(d.public_run_info(conn,run['id'])['candidates'][0]['title'])==2000
    data['candidates'][0]['title']='x'*2001
    with pytest.raises(ValueError):d.ingest_snapshot(conn,run,data)
