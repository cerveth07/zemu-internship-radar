import os
import sqlite3
from contextlib import contextmanager, closing
from datetime import datetime
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'radar.db'
SCHEMA_VERSION = 28

SCHEMA = '''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  sector TEXT DEFAULT '', city TEXT DEFAULT '',
  website TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'watching',
  notes TEXT DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  title TEXT NOT NULL, direction TEXT NOT NULL DEFAULT 'other',
  city TEXT DEFAULT '', salary TEXT DEFAULT '', duration TEXT DEFAULT '',
  team TEXT NOT NULL DEFAULT '', eligibility TEXT NOT NULL DEFAULT '',
  posted_at TEXT NOT NULL DEFAULT '', deadline TEXT NOT NULL DEFAULT '',
  onsite_days TEXT DEFAULT '', jd_text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'candidate', priority TEXT NOT NULL DEFAULT 'unrated',
  notes TEXT DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS job_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  platform TEXT NOT NULL DEFAULT 'manual', url TEXT NOT NULL DEFAULT '',
  external_id TEXT DEFAULT '', verification TEXT NOT NULL DEFAULT 'unverified',
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_checked_at TEXT,
  UNIQUE(job_id, platform, url)
);
CREATE TABLE IF NOT EXISTS job_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  event TEXT NOT NULL, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS skills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  category TEXT NOT NULL DEFAULT 'uncategorized'
);
CREATE TABLE IF NOT EXISTS job_skills (
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  skill_id INTEGER NOT NULL REFERENCES skills(id),
  necessity TEXT NOT NULL DEFAULT 'unspecified', evidence TEXT DEFAULT '',
  PRIMARY KEY (job_id, skill_id)
);
CREATE TABLE IF NOT EXISTS applications (
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  stage TEXT NOT NULL DEFAULT 'not_applied', resume_version TEXT DEFAULT '',
  recruiter TEXT DEFAULT '', last_contact TEXT DEFAULT '',
  next_action TEXT DEFAULT '', notes TEXT DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS learning_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  related_skill TEXT DEFAULT '', source_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  priority TEXT NOT NULL DEFAULT 'medium', status TEXT NOT NULL DEFAULT 'todo',
  notes TEXT DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS import_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL UNIQUE,
  input_json TEXT NOT NULL,
  match_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  resolved_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS profile_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill TEXT NOT NULL,
  proficiency TEXT NOT NULL DEFAULT 'learning',
  evidence_type TEXT NOT NULL DEFAULT 'project',
  description TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'needs_check',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS company_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL DEFAULT 'note',
  observed_on TEXT NOT NULL,
  description TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS analysis_packages (
  id TEXT PRIMARY KEY,
  filters_json TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  included_ids_json TEXT NOT NULL DEFAULT '[]',
  jd_hashes_json TEXT NOT NULL DEFAULT '{}',
  item_count INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS jd_extraction_batches (
  package_id TEXT PRIMARY KEY REFERENCES analysis_packages(id) ON DELETE RESTRICT,
  closed_no_candidates INTEGER NOT NULL DEFAULT 0,
  imported_at TEXT,
  last_activity_at TEXT NOT NULL DEFAULT (datetime('now')),
  import_count INTEGER NOT NULL DEFAULT 0,
  jd_hashes_json TEXT NOT NULL DEFAULT '{}',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS analysis_findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  package_id TEXT NOT NULL REFERENCES analysis_packages(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  related_skill TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL DEFAULT 'medium',
  job_ids_json TEXT NOT NULL,
  requirement_ids_json TEXT NOT NULL DEFAULT '[]',
  personal_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  fingerprint TEXT NOT NULL UNIQUE,
  adopted_task_id INTEGER REFERENCES learning_tasks(id) ON DELETE SET NULL,
  career_stale_ack_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS job_requirements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  package_id TEXT NOT NULL REFERENCES analysis_packages(id) ON DELETE RESTRICT,
  requirement_type TEXT NOT NULL,
  name TEXT NOT NULL,
  strength TEXT NOT NULL,
  evidence TEXT NOT NULL,
  source_field TEXT NOT NULL,
  option_logic TEXT NOT NULL DEFAULT 'single',
  alternatives_json TEXT NOT NULL DEFAULT '[]',
  source_jd_hash TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'pending',
  review_notes TEXT NOT NULL DEFAULT '',
  skill_synced INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_at TEXT,
  UNIQUE(job_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS ai_review_batches (
  id TEXT PRIMARY KEY,
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ai_requirement_reviews (
  requirement_id INTEGER PRIMARY KEY REFERENCES job_requirements(id) ON DELETE CASCADE,
  batch_id TEXT REFERENCES ai_review_batches(id),
  verdict TEXT NOT NULL CHECK(verdict IN ('verified','excluded','uncertain')),
  normalized_name TEXT NOT NULL DEFAULT '',
  source_jd_hash TEXT NOT NULL,
  requirement_version TEXT NOT NULL DEFAULT '',
  explanation TEXT NOT NULL DEFAULT '',
  reviewer TEXT NOT NULL DEFAULT 'gpt',
  event_id INTEGER,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ai_review_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT,
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  undone_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_ai_reviews_verdict ON ai_requirement_reviews(verdict);
CREATE TABLE IF NOT EXISTS personal_skill_status (
  skill_key TEXT PRIMARY KEY COLLATE NOCASE,
  status TEXT NOT NULL DEFAULT 'unknown' CHECK(status IN ('unknown','met','partial','missing')),
  note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS skill_aliases (
  alias TEXT PRIMARY KEY COLLATE NOCASE,
  canonical TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS skill_alias_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  alias TEXT NOT NULL,
  old_canonical TEXT NOT NULL DEFAULT '',
  new_canonical TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS career_export_snapshots (
  package_id TEXT PRIMARY KEY REFERENCES analysis_packages(id) ON DELETE RESTRICT,
  export_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  selection_json TEXT NOT NULL,
  requirement_hashes_json TEXT NOT NULL,
  evidence_hashes_json TEXT NOT NULL,
  included_requirement_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS capture_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'manual',
  keyword TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  collector_offset INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS capture_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL REFERENCES capture_batches(id),
  fingerprint TEXT NOT NULL,
  raw_text TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
  screenshot_file TEXT NOT NULL DEFAULT '',
  draft_json TEXT NOT NULL DEFAULT '{}',
  confirmed_json TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  review_id INTEGER REFERENCES import_reviews(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(batch_id,fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_capture_items_batch_status ON capture_items(batch_id,status);
CREATE INDEX IF NOT EXISTS ix_capture_items_pending_review ON capture_items(review_id) WHERE status='review';
CREATE TABLE IF NOT EXISTS capture_search_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL REFERENCES capture_batches(id),
  task_id TEXT UNIQUE, run_id TEXT,
  platform TEXT NOT NULL DEFAULT 'boss',
  preferences_json TEXT NOT NULL, internship_only INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'starting', error TEXT NOT NULL DEFAULT '',
  platform_state_json TEXT NOT NULL DEFAULT '{}',
  total INTEGER NOT NULL DEFAULT 0, sync_offset INTEGER NOT NULL DEFAULT 0,
  synced_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_one_active_search ON capture_search_runs((1))
  WHERE status IN ('starting','pending','running','stopping','unknown');
CREATE INDEX IF NOT EXISTS ix_search_batch ON capture_search_runs(batch_id,id);
CREATE TABLE IF NOT EXISTS capture_search_results (
  search_run_id INTEGER NOT NULL REFERENCES capture_search_runs(id),
  item_id INTEGER NOT NULL REFERENCES capture_items(id),
  observed_at TEXT NOT NULL DEFAULT '', reused_snapshot INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(search_run_id,item_id)
);
CREATE TABLE IF NOT EXISTS discovery_sources (
  id INTEGER PRIMARY KEY, platform TEXT NOT NULL, source_id TEXT NOT NULL,
  url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '', first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  last_detail_at TEXT, detail_state TEXT NOT NULL DEFAULT 'pending',
  UNIQUE(platform,source_id)
);
CREATE TABLE IF NOT EXISTS discovery_details (
  source_id INTEGER NOT NULL REFERENCES discovery_sources(id), fingerprint TEXT NOT NULL,
  item_json TEXT NOT NULL, observed_at TEXT NOT NULL, parser_version INTEGER NOT NULL,
  complete INTEGER NOT NULL, PRIMARY KEY(source_id,fingerprint,parser_version)
);
CREATE TABLE IF NOT EXISTS discovery_hits (
  run_id INTEGER NOT NULL REFERENCES capture_search_runs(id),
  source_id INTEGER NOT NULL REFERENCES discovery_sources(id),
  keyword TEXT NOT NULL, city TEXT NOT NULL, page INTEGER NOT NULL,
  detail_state TEXT NOT NULL DEFAULT 'pending',
  PRIMARY KEY(run_id,source_id,keyword,city,page)
);
CREATE TABLE IF NOT EXISTS discovery_receipts (
  run_id INTEGER NOT NULL REFERENCES capture_search_runs(id), receipt_id TEXT NOT NULL,
  kind TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,receipt_id)
);
CREATE TABLE IF NOT EXISTS planner_library (
  id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL DEFAULT 0,
  rules_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO planner_library(id) VALUES (1);
CREATE TABLE IF NOT EXISTS planner_families (
  id TEXT PRIMARY KEY, family_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS planner_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS planner_actions (
  plan_id INTEGER NOT NULL REFERENCES planner_plans(id) ON DELETE CASCADE,
  action_id TEXT NOT NULL, action_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  batch_id INTEGER REFERENCES capture_batches(id),
  PRIMARY KEY(plan_id, action_id)
);

CREATE TABLE IF NOT EXISTS personal_materials (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS example_records (
 table_name TEXT NOT NULL, record_id TEXT NOT NULL, PRIMARY KEY(table_name,record_id)
);
CREATE TABLE IF NOT EXISTS material_versions (
 id INTEGER PRIMARY KEY, material_id INTEGER NOT NULL REFERENCES personal_materials(id), title TEXT NOT NULL,
 text_snapshot TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL DEFAULT '', media_type TEXT NOT NULL DEFAULT 'text/plain',
 content BLOB NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evidence_revisions (
 id INTEGER PRIMARY KEY, evidence_id INTEGER NOT NULL REFERENCES profile_evidence(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')),
 UNIQUE(evidence_id,revision)
);
CREATE TABLE IF NOT EXISTS finding_revisions (
 id INTEGER PRIMARY KEY, finding_id INTEGER NOT NULL REFERENCES analysis_findings(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', assessments_json TEXT NOT NULL,
 basis_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE(finding_id,revision)
);
CREATE TABLE IF NOT EXISTS finding_action_tasks (
 finding_id INTEGER NOT NULL REFERENCES analysis_findings(id) ON DELETE CASCADE,
 requirement_id INTEGER NOT NULL REFERENCES job_requirements(id) ON DELETE CASCADE,
 task_id INTEGER NOT NULL REFERENCES learning_tasks(id) ON DELETE CASCADE, PRIMARY KEY(finding_id,requirement_id)
);
CREATE TABLE IF NOT EXISTS learning_outcomes (
 id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES learning_tasks(id) ON DELETE CASCADE,
 evidence_id INTEGER NOT NULL REFERENCES profile_evidence(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS learning_completions (
 completion_id TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES learning_tasks(id) ON DELETE CASCADE,
 response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS material_imports (import_id TEXT PRIMARY KEY,response_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_material_quotes (
 evidence_id INTEGER PRIMARY KEY REFERENCES profile_evidence(id) ON DELETE CASCADE,
 material_version_id INTEGER NOT NULL REFERENCES material_versions(id), source_quote TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_lists (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, builtin_key TEXT UNIQUE,
 publisher TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
 report_url TEXT NOT NULL DEFAULT '', published_on TEXT NOT NULL DEFAULT '',
 description TEXT NOT NULL DEFAULT '', customized INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS company_list_entries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, list_id INTEGER NOT NULL REFERENCES company_lists(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, company_name TEXT NOT NULL, name_key TEXT NOT NULL,
 aliases_json TEXT NOT NULL DEFAULT '[]', business_brands TEXT NOT NULL DEFAULT '',
 region TEXT NOT NULL DEFAULT '', publisher TEXT NOT NULL DEFAULT '',
 source_url TEXT NOT NULL DEFAULT '', report_url TEXT NOT NULL DEFAULT '', published_on TEXT NOT NULL DEFAULT '',
 origin TEXT NOT NULL DEFAULT 'user', UNIQUE(list_id,name_key), UNIQUE(list_id,position)
);
CREATE TABLE IF NOT EXISTS company_list_defaults (key TEXT PRIMARY KEY, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS company_name_mappings (name_key TEXT PRIMARY KEY, display_name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS company_name_links (
 name_key TEXT NOT NULL REFERENCES company_name_mappings(name_key) ON DELETE CASCADE,
 company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
 PRIMARY KEY(name_key,company_id)
);
CREATE TABLE IF NOT EXISTS boss_favorite_batches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, city TEXT NOT NULL,
 browser_mode TEXT NOT NULL DEFAULT 'managed', internship_only INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS boss_favorite_targets (
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL REFERENCES boss_favorite_batches(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
 city TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS boss_favorite_candidates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER NOT NULL REFERENCES boss_favorite_targets(id) ON DELETE CASCADE,
 url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
 salary TEXT NOT NULL DEFAULT '', jd TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL DEFAULT '',
 availability TEXT NOT NULL DEFAULT 'unread', selected INTEGER NOT NULL DEFAULT 0, match_reason TEXT NOT NULL DEFAULT '',
 favorite_status TEXT NOT NULL DEFAULT 'not_checked', favorite_at TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
 UNIQUE(target_id,url)
);
CREATE TABLE IF NOT EXISTS boss_favorite_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL UNIQUE,
 batch_id INTEGER NOT NULL REFERENCES boss_favorite_batches(id) ON DELETE CASCADE,
 action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'starting', request_json TEXT NOT NULL,
 preferences_json TEXT NOT NULL, output_json TEXT NOT NULL DEFAULT '{}', output_hash TEXT NOT NULL DEFAULT '',
 error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')), finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_boss_favorite_active ON boss_favorite_runs((1))
 WHERE status IN ('starting','pending','running','stopping','unknown');
CREATE INDEX IF NOT EXISTS ix_boss_favorite_targets_batch ON boss_favorite_targets(batch_id,position);
CREATE INDEX IF NOT EXISTS ix_boss_favorite_candidates_target ON boss_favorite_candidates(target_id);
CREATE INDEX IF NOT EXISTS ix_company_list_entries_list ON company_list_entries(list_id,position);
CREATE INDEX IF NOT EXISTS ix_extraction_batches_created ON jd_extraction_batches(created_at);
CREATE INDEX IF NOT EXISTS ix_analysis_findings_package ON analysis_findings(package_id);
CREATE INDEX IF NOT EXISTS ix_job_requirements_review ON job_requirements(review_status,id);
CREATE INDEX IF NOT EXISTS ix_job_requirements_job ON job_requirements(job_id);
CREATE INDEX IF NOT EXISTS ix_job_requirements_package ON job_requirements(package_id,created_at);
CREATE INDEX IF NOT EXISTS ix_reviews_status ON import_reviews(status);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_direction ON jobs(direction);
CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS ix_company_observations ON company_observations(company_id,observed_on);
'''


def database_path() -> Path:
    return Path(os.environ.get('RADAR_PRIVATE_RC_DB_PATH', str(DEFAULT_DB))).expanduser()


@contextmanager
def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=10000')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_capture_review_outcomes(connection, review_id=None):
    """Apply resolved review outcomes to every capture item that shares the review."""
    statement = """UPDATE capture_items
        SET (status, job_id, updated_at) = (
            SELECT CASE WHEN review.status='dismissed' THEN 'dismissed' ELSE 'imported' END,
                   review.resolved_job_id,
                   MAX(capture_items.updated_at, COALESCE(review.resolved_at, datetime('now')))
            FROM import_reviews AS review WHERE review.id=capture_items.review_id
        )
        WHERE status='review' AND review_id IN (
            SELECT id FROM import_reviews WHERE status IN ('linked', 'independent', 'dismissed')
        )"""
    parameters = ()
    if review_id is not None:
        statement += ' AND review_id=?'
        parameters = (review_id,)
    return connection.execute(statement, parameters).rowcount


def init_db():
    path = database_path()
    if path.is_file() and path.stat().st_size:
        with closing(sqlite3.connect(path)) as old:
            version = old.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError('此数据库由更新版本创建，请升级应用；本次未修改数据')
            has_jobs = old.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
            if has_jobs and version < SCHEMA_VERSION:
                directory = path.parent / 'backups'
                directory.mkdir(exist_ok=True)
                target = directory / ('before-upgrade-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.sqlite')
                with closing(sqlite3.connect(target)) as snapshot:
                    old.backup(snapshot)
                    if snapshot.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise RuntimeError('升级前备份校验失败，已停止升级')
                from .backup_upgrade import archive_snapshot
                archive_snapshot(target, path.parent)
                target.unlink()  # Verified SQLite is retained inside the complete ZIP.
    with connect() as connection:
        connection.executescript(SCHEMA)
        # v0.3 users already have a jobs table; CREATE TABLE IF NOT EXISTS
        # does not add columns to it. Add only missing columns and keep all data.
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(jobs)')}
        for field in ('team', 'eligibility', 'posted_at', 'deadline'):
            if field not in columns:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        ai_review_columns = {row['name'] for row in connection.execute('PRAGMA table_info(ai_requirement_reviews)')}
        if 'requirement_version' not in ai_review_columns:
            connection.execute("ALTER TABLE ai_requirement_reviews ADD COLUMN requirement_version TEXT NOT NULL DEFAULT ''")
        company_columns = {row['name'] for row in connection.execute('PRAGMA table_info(companies)')}
        for field in ('sector_source_url', 'sector_observed_on',
                      'headcount_source_url', 'headcount_observed_on', 'headcount_text'):
            if field not in company_columns:
                connection.execute(f"ALTER TABLE companies ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        for field in ('headcount_min', 'headcount_max'):
            if field not in company_columns:
                connection.execute(f"ALTER TABLE companies ADD COLUMN {field} INTEGER")
        # Preserve genuine numeric ranges from older free-text values. Category
        # labels never become invented employee counts. The pre-upgrade ZIP
        # retains the retired columns and preferences.
        if 'size_band' in company_columns:
            from .company_profile import parse_headcount
            for row in connection.execute('SELECT id,size_band,headcount_min,headcount_max FROM companies').fetchall():
                if row['headcount_min'] is None and row['headcount_max'] is None:
                    parsed = parse_headcount(row['size_band'])
                    if parsed:
                        connection.execute('UPDATE companies SET headcount_min=?,headcount_max=?,headcount_text=? WHERE id=?', (*parsed, row['id']))
        for field in ('size_band', 'tier_reason'):
            if field in company_columns:
                connection.execute(f'ALTER TABLE companies DROP COLUMN {field}')
        connection.execute('DROP TABLE IF EXISTS company_size_preferences')
        for table, fields in {
            'profile_evidence': {'skills_json': "TEXT NOT NULL DEFAULT '[]'", 'revision': 'INTEGER NOT NULL DEFAULT 1',
                                 'material_version_id': 'INTEGER REFERENCES material_versions(id)'},
            'applications': {'material_version_id': 'INTEGER REFERENCES material_versions(id)'},
            'learning_tasks': {'revision': 'INTEGER NOT NULL DEFAULT 1'},
            'analysis_findings': {'review_state': "TEXT NOT NULL DEFAULT 'current'", 'review_note': "TEXT NOT NULL DEFAULT ''",
                'review_snapshot_json': "TEXT NOT NULL DEFAULT '{}'", 'assessment_overrides_json': "TEXT NOT NULL DEFAULT '[]'",
                'revision': 'INTEGER NOT NULL DEFAULT 1'},
        }.items():
            columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
            for field, definition in fields.items():
                if field not in columns:
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {field} {definition}')
        application_columns = {row['name'] for row in connection.execute('PRAGMA table_info(applications)')}
        for field in ('applied_on', 'follow_up_on'):
            if field not in application_columns:
                connection.execute(f"ALTER TABLE applications ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        finding_columns = {row['name'] for row in connection.execute('PRAGMA table_info(analysis_findings)')}
        if 'assessments_json' not in finding_columns:
            connection.execute("ALTER TABLE analysis_findings ADD COLUMN assessments_json TEXT NOT NULL DEFAULT '[]'")
        package_columns = {row['name'] for row in connection.execute('PRAGMA table_info(analysis_packages)')}
        if 'included_ids_json' not in package_columns:
            connection.execute("ALTER TABLE analysis_packages ADD COLUMN included_ids_json TEXT NOT NULL DEFAULT '[]'")
        if 'jd_hashes_json' not in package_columns:
            connection.execute("ALTER TABLE analysis_packages ADD COLUMN jd_hashes_json TEXT NOT NULL DEFAULT '{}'")
        finding_columns = {row['name'] for row in connection.execute('PRAGMA table_info(analysis_findings)')}
        if 'career_stale_ack_json' not in finding_columns:
            connection.execute("ALTER TABLE analysis_findings ADD COLUMN career_stale_ack_json TEXT NOT NULL DEFAULT '{}'")
        if 'requirement_ids_json' not in finding_columns:
            connection.execute("ALTER TABLE analysis_findings ADD COLUMN requirement_ids_json TEXT NOT NULL DEFAULT '[]'")
        if 'personal_evidence_ids_json' not in finding_columns:
            connection.execute("ALTER TABLE analysis_findings ADD COLUMN personal_evidence_ids_json TEXT NOT NULL DEFAULT '[]'")
        career_columns = {row['name'] for row in connection.execute('PRAGMA table_info(career_export_snapshots)')}
        if 'included_requirement_ids_json' not in career_columns:
            connection.execute("ALTER TABLE career_export_snapshots ADD COLUMN included_requirement_ids_json TEXT NOT NULL DEFAULT '[]'")
        batch_columns = {row['name'] for row in connection.execute('PRAGMA table_info(capture_batches)')}
        if 'collector_offset' not in batch_columns:
            connection.execute('ALTER TABLE capture_batches ADD COLUMN collector_offset INTEGER NOT NULL DEFAULT 0')
        search_columns = {row['name'] for row in connection.execute('PRAGMA table_info(capture_search_runs)')}
        if 'platform' not in search_columns:
            connection.execute("ALTER TABLE capture_search_runs ADD COLUMN platform TEXT NOT NULL DEFAULT 'boss'")
        connection.execute('DROP INDEX IF EXISTS ix_one_active_search')
        connection.execute("CREATE UNIQUE INDEX ix_one_active_search ON capture_search_runs(platform) WHERE status IN ('starting','pending','running','stopping','unknown')")
        sync_capture_review_outcomes(connection)
        from .examples import register_legacy_examples
        register_legacy_examples(connection)
        from .company_lists import ensure_builtin
        ensure_builtin(connection)
        connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
