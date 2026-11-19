import json
import hashlib
import sqlite3
from contextlib import closing
import tempfile
import os
import re
from datetime import date
from urllib.parse import urlsplit
from starlette.background import BackgroundTask
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .career_workspace import router as career_workspace_router, EvidenceData, create_evidence, evidence_record, learning_records
from .materials import router as materials_router
from .examples import router as examples_router
from .directions import Direction
from .company_profile import CompanyEvidenceFields, normalize_evidence
from .company_lists import router as company_lists_router, matched_company_ids
from .db import connect, init_db, database_path, SCHEMA_VERSION, sync_capture_review_outcomes
from .skill_rules import suggest_skills
from .analysis import router as analysis_router
from .career_export import router as career_export_router
from .workflow import router as workflow_router
from .ai_review import router as ai_review_router
from .requirements import router as requirements_router
from .requirement_stats import router as requirement_stats_router
from .skill_gap import router as skill_gap_router
from .gpt_zip import router as gpt_zip_router
from .exports import router as exports_router
from .capture import router as capture_router
from .search_runs import router as search_router
from .search_planner import router as planner_router
from .discovery import router as discovery_router
from .support import router as support_router, VERSION
from .job_search import router as job_search_router
from .boss_favorites import router as boss_favorites_router
from .agent_api import router as agent_router, public_router as agent_public_router, agent_token

ROOT = Path(__file__).resolve().parents[1]
@asynccontextmanager
async def lifespan(app):
    init_db()
    agent_token()  # local agents read this file before their first call
    yield


app = FastAPI(title='择木 · 实习工作台', version=VERSION, docs_url='/api/docs', lifespan=lifespan)


@app.middleware('http')
async def local_origin_guard(request: Request, call_next):
    """Reject cross-site writes and DNS-rebinding Host headers on the local API.

    Native clients and local integration tests may omit Origin. Browser requests
    must originate from the same loopback origin and port as the application.
    """
    if getattr(app.state, 'restoring', False):
        return JSONResponse({'detail': '正在关闭服务以恢复备份，请等待应用重启'}, status_code=503)
    host = request.headers.get('host', '')
    parsed_host = urlsplit('//' + host)
    if parsed_host.hostname not in ('127.0.0.1', 'localhost', 'testserver'):
        return JSONResponse({'detail':'仅接受本机地址访问'}, status_code=403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin:
            parsed = urlsplit(origin)
            try:
                same_port = parsed.port == parsed_host.port
            except ValueError:
                same_port = False
            if (parsed.scheme != 'http' or parsed.hostname != parsed_host.hostname
                    or not same_port or parsed.username or parsed.password):
                return JSONResponse({'detail':'禁止跨站修改本地岗位数据'}, status_code=403)
    return await call_next(request)

app.include_router(career_workspace_router)
app.include_router(materials_router)
app.include_router(examples_router)
app.include_router(company_lists_router)

app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
app.include_router(analysis_router)
app.include_router(career_export_router)
app.include_router(workflow_router)
app.include_router(ai_review_router)
app.include_router(requirement_stats_router)
app.include_router(skill_gap_router)
app.include_router(gpt_zip_router)
app.include_router(requirements_router)
app.include_router(exports_router)
app.include_router(capture_router)
app.include_router(search_router)
app.include_router(planner_router)
app.include_router(discovery_router)
app.include_router(support_router)
app.include_router(job_search_router)
app.include_router(boss_favorites_router)
app.include_router(agent_router)
app.include_router(agent_public_router)

JOB_STATUS = Literal['candidate', 'verified', 'uncertain', 'closed']
PRIORITY = Literal['unrated', 'high', 'medium', 'low', 'skip']
STAGE = Literal['not_applied', 'contacted', 'resume_sent', 'interview', 'offer', 'rejected', 'withdrawn']


def valid_date(value: str):
    if value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期须使用 YYYY-MM-DD')
    if value:
        date.fromisoformat(value)
    return value


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


def rowdict(row):
    return dict(row) if row else None


def required(conn, table: str, item_id: int):
    if table not in {'jobs', 'companies'}:
        raise ValueError('Invalid table')
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f'{table} record not found')
    return row


class CompanyIn(CompanyEvidenceFields):
    name: str = Field(min_length=1, max_length=140)
    sector: str = Field(default='', max_length=180)
    city: str = Field(default='', max_length=120)
    website: str = Field(default='', max_length=1000)
    status: Literal['watching', 'active', 'paused'] = 'watching'
    notes: str = ''

    @field_validator('name')
    @classmethod
    def trim_name(cls, value):
        return value.strip()


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(default='uncategorized', max_length=80)
    necessity: Literal['must', 'bonus', 'unspecified'] = 'unspecified'
    evidence: str = ''


class JobIn(BaseModel):
    company_id: int
    title: str = Field(min_length=1, max_length=180)
    direction: Direction = 'other'
    city: str = ''
    salary: str = ''
    duration: str = ''
    onsite_days: str = ''
    team: str = Field(default='', max_length=160)
    eligibility: str = Field(default='', max_length=1000)
    posted_at: str = ''
    deadline: str = ''
    jd_text: str = ''
    status: JOB_STATUS = 'candidate'
    priority: PRIORITY = 'unrated'
    notes: str = ''
    source_platform: str = 'manual'
    source_url: str = ''
    skills: list[SkillIn] = Field(default_factory=list)

    @field_validator('posted_at', 'deadline')
    @classmethod
    def validate_dates(cls, value):
        return valid_date(value)


class ImportedJob(BaseModel):
    company: str = Field(min_length=1, max_length=140)
    title: str = Field(min_length=1, max_length=180)
    direction: Direction = 'other'
    city: str = ''
    salary: str = ''
    duration: str = ''
    onsite_days: str = ''
    team: str = Field(default='', max_length=160)
    eligibility: str = Field(default='', max_length=1000)
    posted_at: str = ''
    deadline: str = ''
    jd_text: str = ''
    status: JOB_STATUS = 'candidate'
    priority: PRIORITY = 'unrated'
    notes: str = ''
    source_platform: str = Field(default='manual_import', max_length=100)
    source_url: str = Field(default='', max_length=2000)
    skills: list[str] = Field(default_factory=list, max_length=40)

    @field_validator('company', 'title')
    @classmethod
    def trim_required(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('不可为空白')
        return value

    @field_validator('posted_at', 'deadline')
    @classmethod
    def validate_dates(cls, value):
        return valid_date(value)


class ImportRequest(BaseModel):
    rows: list[ImportedJob] = Field(min_length=1, max_length=500)
    dry_run: bool = True


class ApplicationIn(BaseModel):
    stage: STAGE = 'not_applied'
    applied_on: str = Field(default='', max_length=10)
    follow_up_on: str = Field(default='', max_length=10)
    material_version_id: int | None = Field(default=None, gt=0)
    resume_version: str = ''
    recruiter: str = ''
    last_contact: str = ''
    next_action: str = ''
    notes: str = ''

    @field_validator('applied_on', 'follow_up_on')
    @classmethod
    def validate_dates(cls, value):
        return valid_date(value)


class JobPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    direction: Direction | None = None
    city: str | None = None
    salary: str | None = None
    duration: str | None = None
    onsite_days: str | None = None
    team: str | None = Field(default=None, max_length=160)
    eligibility: str | None = Field(default=None, max_length=1000)
    posted_at: str | None = None
    deadline: str | None = None
    status: JOB_STATUS | None = None
    priority: PRIORITY | None = None
    notes: str | None = None
    jd_text: str | None = None
    application: ApplicationIn | None = None
    expected_fields: dict[str, str] | None = None
    expected_application: ApplicationIn | None = None

    @field_validator('posted_at', 'deadline')
    @classmethod
    def validate_dates(cls, value):
        return valid_date(value) if value is not None else None


class CompanyPatch(CompanyEvidenceFields):
    name: str | None = Field(default=None, min_length=1, max_length=140)
    sector: str | None = Field(default=None, max_length=180)
    city: str | None = Field(default=None, max_length=120)
    website: str | None = Field(default=None, max_length=1000)
    status: Literal['watching', 'active', 'paused'] | None = None
    notes: str | None = None


class LearningPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    related_skill: str | None = None
    source_job_id: int | None = None
    priority: Literal['high', 'medium', 'low'] | None = None
    status: Literal['todo', 'doing', 'done'] | None = None
    notes: str | None = None


class EvidenceIn(EvidenceData):
    pass

class ReviewResolution(BaseModel):
    action: Literal['link_source', 'create_separate', 'dismiss']


class SourceIn(BaseModel):
    platform: str = Field(min_length=1, max_length=100)
    url: str = Field(default='', max_length=2000)
    verification: Literal['unverified', 'verified', 'unavailable'] = 'unverified'


class SourceReview(BaseModel):
    verification: Literal['unverified', 'verified', 'unavailable']


class SkillReview(BaseModel):
    confirmed: Literal[True]
    skills: list[SkillIn] = Field(max_length=80)


class ObservationIn(BaseModel):
    event_type: Literal['note', 'hiring', 'product', 'funding', 'team'] = 'note'
    observed_on: str
    description: str = Field(min_length=1, max_length=4000)
    source_url: str = Field(default='', max_length=2000)

    @field_validator('observed_on')
    @classmethod
    def validate_dates(cls, value):
        if not value:
            raise ValueError('观察日期不能为空')
        return valid_date(value)


class LearningIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    related_skill: str = ''
    source_job_id: int | None = None
    priority: Literal['high', 'medium', 'low'] = 'medium'
    status: Literal['todo', 'doing', 'done'] = 'todo'
    notes: str = ''


def track(conn, job_id, event, payload):
    conn.execute('INSERT INTO job_history(job_id,event,payload_json) VALUES(?,?,?)',
                 (job_id, event, json.dumps(payload, ensure_ascii=False)))


@app.get('/api/dashboard')
def dashboard():
    with connect() as conn:
        from .examples import example_summary
        count = lambda sql: conn.execute(sql).fetchone()[0]
        due_followups = [dict(row) for row in conn.execute('''SELECT a.job_id,a.stage,
            a.follow_up_on,a.next_action,j.title,c.name AS company FROM applications a
            JOIN jobs j ON j.id=a.job_id JOIN companies c ON c.id=j.company_id
            WHERE a.follow_up_on!='' AND a.follow_up_on<=?
              AND a.stage NOT IN ('rejected','withdrawn')
            ORDER BY a.follow_up_on,a.job_id''', (date.today().isoformat(),))]
        return {
            'examples': example_summary(conn),
            'due_followups': due_followups[:5], 'due_followup_count': len(due_followups),
            'companies': count('SELECT count(*) FROM companies'),
            'jobs': count('SELECT count(*) FROM jobs'),
            'verified': count("SELECT count(*) FROM jobs WHERE status='verified'"),
            'priority': count("SELECT count(*) FROM jobs WHERE priority='high'"),
            'applications': count("SELECT count(*) FROM applications WHERE stage!='not_applied'"),
            'learning_open': count("SELECT count(*) FROM learning_tasks WHERE status!='done'"),
            'observations': count('SELECT count(*) FROM company_observations'),
            'directions': [dict(r) for r in conn.execute('SELECT direction, count(*) AS count FROM jobs GROUP BY direction ORDER BY count DESC')],
            'recent': [dict(r) for r in conn.execute('''SELECT j.id,j.title,j.status,j.priority,j.city,j.created_at,c.name AS company
                FROM jobs j JOIN companies c ON j.company_id=c.id ORDER BY j.id DESC LIMIT 5''')]
        }


@app.get('/api/companies')
def companies(q: str = ''):
    with connect() as conn:
        return [dict(r) for r in conn.execute('''SELECT c.*,count(j.id) AS job_count FROM companies c
           LEFT JOIN jobs j ON j.company_id=c.id WHERE c.name LIKE ? OR c.sector LIKE ?
           GROUP BY c.id ORDER BY c.id DESC''', (f'%{q}%', f'%{q}%'))]


@app.get('/api/companies/{company_id}/jobs')
def company_jobs(company_id: int):
    with connect() as conn:
        required(conn, 'companies', company_id)
        return [dict(row) for row in conn.execute("""
            SELECT j.id,j.title,j.direction,j.city,j.priority,j.status,
                   j.created_at, j.updated_at
            FROM jobs j WHERE j.company_id=?
            ORDER BY CASE j.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                     j.id DESC
        """, (company_id,))]


@app.post('/api/companies', status_code=201)
def create_company(data: CompanyIn):
    if not data.name:
        raise HTTPException(422, '企业名称不可为空')
    values = normalize_evidence(data.model_dump())
    columns = ','.join(values)
    placeholders = ','.join('?' for _ in values)
    with connect() as conn:
        try:
            cur = conn.execute(f'INSERT INTO companies({columns}) VALUES({placeholders})', tuple(values.values()))
        except Exception as ex:
            if 'UNIQUE constraint failed' in str(ex):
                raise HTTPException(409, '公司已存在，请在现有公司下添加岗位')
            raise
        return {'id': cur.lastrowid}


@app.patch('/api/companies/{company_id}')
def update_company(company_id: int, data: CompanyPatch):
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, '没有需要更新的字段')
    if 'name' in changes:
        changes['name'] = (changes['name'] or '').strip()
        if not changes['name']:
            raise HTTPException(422, '企业名称不可为空')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = dict(required(conn, 'companies', company_id))
        changes = normalize_evidence(changes, current)
        changes = {k: v for k, v in changes.items() if current[k] != v}
        if not changes:
            return {'ok': True, 'changed': False}
        try:
            conn.execute('UPDATE companies SET '+','.join(f'{k}=?' for k in changes)+' WHERE id=?',
                         [*changes.values(), company_id])
        except sqlite3.IntegrityError:
            raise HTTPException(409, '企业名称与已有记录冲突')
        return {'ok': True, 'changed': True}


@app.get('/api/companies/{company_id}/observations')
def list_company_observations(company_id: int):
    with connect() as conn:
        required(conn, 'companies', company_id)
        return [dict(r) for r in conn.execute('''SELECT * FROM company_observations
            WHERE company_id=? ORDER BY observed_on DESC,id DESC''', (company_id,))]


@app.post('/api/companies/{company_id}/observations', status_code=201)
def add_company_observation(company_id: int, data: ObservationIn):
    with connect() as conn:
        required(conn, 'companies', company_id)
        cur = conn.execute('''INSERT INTO company_observations(company_id,event_type,observed_on,description,source_url)
            VALUES(?,?,?,?,?)''', (company_id,*data.model_dump().values()))
        return {'id':cur.lastrowid}


@app.delete('/api/companies/{company_id}/observations/{observation_id}')
def delete_company_observation(company_id: int, observation_id: int):
    with connect() as conn:
        required(conn,'companies', company_id)
        if not conn.execute('DELETE FROM company_observations WHERE id=? AND company_id=?',
                            (observation_id,company_id)).rowcount:
            raise HTTPException(404, '观察记录不存在')
        return {'ok':True}


class JobBulkUpdate(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    priority: PRIORITY | None = None
    direction: Direction | None = None

    @field_validator('ids')
    @classmethod
    def valid_ids(cls, values):
        if len(values) != len(set(values)) or any(type(x) is not int or x <= 0 for x in values):
            raise ValueError('岗位 ID 不可重复，且必须为正整数')
        return values


class PriorityResultRow(BaseModel):
    job_id: int = Field(gt=0)
    priority: PRIORITY
    reason: str = Field(default='', max_length=1000)


class PriorityResultImport(BaseModel):
    package_id: str = Field(default='', max_length=36)
    rows: list[PriorityResultRow] = Field(min_length=1, max_length=1000)
    dry_run: bool = True


@app.post('/api/jobs/bulk')
def bulk_update_jobs(data: JobBulkUpdate):
    if data.priority is None and data.direction is None:
        raise HTTPException(422, '请选择需要修改的优先级或岗位方向')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = {}
        for offset in range(0, len(data.ids), 400):
            part = data.ids[offset:offset + 400]
            placeholders = ','.join('?' for _ in part)
            existing.update({row['id']: row for row in conn.execute(
                f'SELECT id,priority,direction FROM jobs WHERE id IN ({placeholders})', part)})
        missing = sorted(set(data.ids) - set(existing))
        if missing:
            raise HTTPException(422, f'岗位不存在：{missing[:20]}；没有修改任何记录')
        changed = 0
        for ident in data.ids:
            old = existing[ident]
            updates = {key: value for key, value in (
                ('priority', data.priority), ('direction', data.direction))
                if value is not None and old[key] != value}
            if not updates:
                continue
            clause = ','.join(f'{key}=?' for key in updates)
            conn.execute(
                f"UPDATE jobs SET {clause},updated_at=datetime('now') WHERE id=?",
                [*updates.values(), ident])
            track(conn, ident, 'bulk_updated', {
                'before': {key: old[key] for key in updates},
                'after': updates})
            changed += 1
        return {'ok': True, 'total': len(data.ids), 'changed': changed,
                'unchanged': len(data.ids) - changed}


@app.post('/api/jobs/priority-import')
def import_job_priorities(data: PriorityResultImport):
    from .analysis import PackageRequest, digest, fetch_records
    if not data.package_id:
        raise HTTPException(409, '优先级结果缺少 package_id，无法核对分析时的岗位版本；请重新导出并返回带批次的结果')
    ids = [row.job_id for row in data.rows]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, 'AI 返回了重复的岗位 ID，请先修改文件')
    with connect() as conn:
        conn.execute('BEGIN' if data.dry_run else 'BEGIN IMMEDIATE')
        package = conn.execute('SELECT * FROM analysis_packages WHERE id=?', (data.package_id,)).fetchone()
        if package is None:
            raise HTTPException(404, '分析包不存在，请从当前岗位库重新导出')
        if not set(ids).issubset(json.loads(package['included_ids_json'])):
            raise HTTPException(422, '优先级结果包含不属于本分析包的岗位；没有修改任何记录')
        private = bool(json.loads(package['filters_json']).get('include_private', False))
        original = json.loads(package['snapshot_json'])
        records = {}
        for offset in range(0, len(ids), 250):
            records.update({j['job_id']: j for j in fetch_records(conn, PackageRequest(
                job_ids=ids[offset:offset+250], include_private=private))})
        stale = []
        replayed = set()
        for row in data.rows:
            version = digest(records[row.job_id])
            latest = conn.execute("SELECT payload_json FROM job_history WHERE job_id=? AND event='priority_assessed' ORDER BY id DESC LIMIT 1", (row.job_id,)).fetchone()
            prior = json.loads(latest['payload_json']) if latest else {}
            if (prior.get('package_id') == data.package_id and prior.get('applied_version') == version
                    and prior.get('after') == row.priority and prior.get('reason') == row.reason.strip()):
                replayed.add(row.job_id)
            elif original.get(str(row.job_id)) != version:
                stale.append(row.job_id)
        if stale and not data.dry_run:
            raise HTTPException(409, f'岗位资料或优先级在导出后已变化：{stale}；没有覆盖任何记录，请重新导出评价')
        existing = {}
        for offset in range(0, len(ids), 400):
            part = ids[offset:offset + 400]
            for row in conn.execute('SELECT id,priority FROM jobs WHERE id IN (' +
                    ','.join('?' for _ in part) + ')', part):
                existing[row['id']] = row['priority']
        missing = sorted(set(ids) - set(existing))
        if missing:
            raise HTTPException(422, f'以下岗位不存在：{missing[:20]}；请确认文件来自当前岗位库')
        changes = []
        changed = 0
        for row in data.rows:
            old = existing[row.job_id]
            is_changed = old != row.priority
            changed += int(is_changed)
            changes.append({'job_id': row.job_id, 'before': old,
                            'priority': row.priority, 'reason': row.reason,
                            'changed': is_changed})
            if not data.dry_run and row.job_id not in replayed and (is_changed or row.reason.strip()):
                if is_changed:
                    conn.execute("UPDATE jobs SET priority=?,updated_at=datetime('now') WHERE id=?",
                                 (row.priority, row.job_id))
                latest = conn.execute("""SELECT payload_json FROM job_history
                    WHERE job_id=? AND event='priority_assessed'
                    ORDER BY id DESC LIMIT 1""", (row.job_id,)).fetchone()
                previous = json.loads(latest['payload_json']) if latest else {}
                if is_changed or previous.get('after') != row.priority or previous.get('reason') != row.reason.strip():
                    track(conn, row.job_id, 'priority_assessed', {
                        'before': old, 'after': row.priority,
                        'reason': row.reason.strip(), 'source': 'user_imported_gpt_json',
                        'package_id': data.package_id,
                        'applied_version': digest(fetch_records(conn, PackageRequest(
                            job_ids=[row.job_id], include_private=private))[0])})
        return {'dry_run': data.dry_run, 'total': len(ids), 'changed': changed,
                'unchanged': len(ids) - changed, 'items': changes[:30], 'stale_job_ids': stale}


@app.get('/api/jobs')
def jobs(q: str = '', direction: str = '', status: str = '', priority: str = '',
         company_list_id: Annotated[int | None, Query(ge=1)] = None,
         company_id: Annotated[int | None, Query(ge=1)] = None):
    sql = '''SELECT j.*,c.name AS company,c.headcount_min AS company_headcount_min,c.headcount_max AS company_headcount_max,c.headcount_text AS company_headcount_text,
             (SELECT platform FROM job_sources WHERE job_id=j.id ORDER BY id LIMIT 1) AS source_platform,
             (SELECT url FROM job_sources WHERE job_id=j.id ORDER BY id LIMIT 1) AS source_url,
             coalesce(a.stage,'not_applied') AS application_stage
             FROM jobs j JOIN companies c ON c.id=j.company_id
             LEFT JOIN applications a ON a.job_id=j.id
             WHERE (j.title LIKE ? OR j.jd_text LIKE ? OR c.name LIKE ?)'''
    params = [f'%{q}%'] * 3
    if direction:
        sql += ' AND j.direction=?'; params.append(direction)
    if status:
        sql += ' AND j.status=?'; params.append(status)
    if priority:
        sql += ' AND j.priority=?'; params.append(priority)
    if company_id is not None:
        sql += ' AND j.company_id=?'; params.append(company_id)
    sql += ' ORDER BY CASE j.priority WHEN \'high\' THEN 0 WHEN \'medium\' THEN 1 ELSE 2 END, j.id DESC'
    with connect() as conn:
        ids = set(matched_company_ids(conn, company_list_id)) if company_list_id is not None else None
        return [dict(r) for r in conn.execute(sql, params) if ids is None or r['company_id'] in ids]


@app.post('/api/jobs', status_code=201)
def create_job(data: JobIn):
    with connect() as conn:
        required(conn, 'companies', data.company_id)
        fields = ['company_id', 'title', 'direction', 'city', 'salary', 'duration', 'onsite_days', 'jd_text', 'status', 'priority', 'notes',
                  'team', 'eligibility', 'posted_at', 'deadline']
        payload = data.model_dump()
        cur = conn.execute('''INSERT INTO jobs(company_id,title,direction,city,salary,duration,onsite_days,jd_text,status,priority,notes,
               team,eligibility,posted_at,deadline) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [payload[f] for f in fields])
        job_id = cur.lastrowid
        conn.execute('INSERT INTO job_sources(job_id,platform,url) VALUES(?,?,?)',
                     (job_id, data.source_platform or 'manual', data.source_url))
        for skill in data.skills:
            name = skill.name.strip()
            if not name:
                continue
            conn.execute('INSERT OR IGNORE INTO skills(name,category) VALUES(?,?)', (name,skill.category))
            skill_id = conn.execute('SELECT id FROM skills WHERE name=? COLLATE NOCASE', (name,)).fetchone()['id']
            conn.execute('''INSERT INTO job_skills(job_id,skill_id,necessity,evidence) VALUES(?,?,?,?)
              ON CONFLICT(job_id,skill_id) DO UPDATE SET necessity=excluded.necessity,evidence=excluded.evidence''',
              (job_id, skill_id, skill.necessity, skill.evidence))
        track(conn, job_id, 'created', {'title': data.title, 'jd_text': data.jd_text, 'source': data.source_platform})
        return {'id': job_id}


def _find_import_match(conn, row: ImportedJob, seen: set, seen_urls: set):
    url = row.source_url.strip()
    if url:
        if url in seen_urls:
            return 'duplicate', None, '导入文件中来源链接重复'
        seen_urls.add(url)
        prior = conn.execute('SELECT job_id FROM job_sources WHERE url=? LIMIT 1', (url,)).fetchone()
        if prior:
            previous = conn.execute('SELECT jd_text FROM jobs WHERE id=?', (prior['job_id'],)).fetchone()
            if row.jd_text.strip() and previous['jd_text'].strip() != row.jd_text.strip():
                return 'review', prior['job_id'], '同一来源的 JD 已变化，请审核新版本'
            return 'duplicate', prior['job_id'], '来源链接已入库'
    # 标题/城市相同并不足以证明属于同一个团队：只送人工核验，不强行合并。
    match = conn.execute("""SELECT j.id FROM jobs j JOIN companies c ON c.id=j.company_id
        WHERE c.name=? COLLATE NOCASE AND j.title=? COLLATE NOCASE AND
        coalesce(j.city,'')=? COLLATE NOCASE LIMIT 1""",
        (row.company, row.title, row.city)).fetchone()
    if match:
        return 'review', match['id'], '同企业、岗位名称及城市存在相似记录'
    key = (row.company.casefold(),row.title.casefold(),row.city.casefold())
    if key in seen:
        return 'review', None, '导入文件中存在可能重复的岗位'
    seen.add(key)
    return 'new', None, '可新增'


@app.post('/api/import/jobs')
def import_jobs(request: ImportRequest):
    """导入用户主动提供的岗位资料。预览不写入；有歧义时跳过，绝不自动合并。"""
    results = []
    seen = set()
    seen_urls = set()
    with connect() as conn:
        for index, row in enumerate(request.rows, 1):
            action, existing_id, reason = _find_import_match(conn, row, seen, seen_urls)
            item = {'row': index, 'company': row.company, 'title': row.title,
                    'action': action, 'existing_job_id': existing_id, 'reason': reason}
            if not request.dry_run and action == 'review':
                payload = json.dumps(row.model_dump(), ensure_ascii=False, sort_keys=True)
                fingerprint = hashlib.sha256(payload.encode('utf-8')).hexdigest()
                conn.execute('''INSERT OR IGNORE INTO import_reviews(fingerprint,input_json,match_job_id,reason)
                    VALUES(?,?,?,?)''', (fingerprint,payload,existing_id,reason))
            if not request.dry_run and action == 'new':
                company_row = conn.execute('SELECT id FROM companies WHERE name=? COLLATE NOCASE',(row.company,)).fetchone()
                if company_row:
                    company_id = company_row['id']
                else:
                    company_id = conn.execute('INSERT INTO companies(name) VALUES(?)',(row.company,)).lastrowid
                payload = row.model_dump()
                fields = ['title','direction','city','salary','duration','onsite_days','jd_text','status','priority','notes',
                          'team','eligibility','posted_at','deadline']
                job_id = conn.execute("""INSERT INTO jobs(company_id,title,direction,city,salary,duration,onsite_days,
                    jd_text,status,priority,notes,team,eligibility,posted_at,deadline) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [company_id]+[payload[field] for field in fields]).lastrowid
                conn.execute('INSERT INTO job_sources(job_id,platform,url) VALUES(?,?,?)',
                    (job_id,row.source_platform or 'manual_import', row.source_url))
                for skill_name in dict.fromkeys(x.strip() for x in row.skills if x.strip()):
                    conn.execute('INSERT OR IGNORE INTO skills(name) VALUES(?)',(skill_name,))
                    skill_id=conn.execute('SELECT id FROM skills WHERE name=? COLLATE NOCASE',(skill_name,)).fetchone()['id']
                    conn.execute('INSERT INTO job_skills(job_id,skill_id) VALUES(?,?)',(job_id,skill_id))
                track(conn,job_id,'imported',{'source_platform':row.source_platform,'source_url':row.source_url,
                    'jd_text':row.jd_text,'method':'user_provided_import'})
                item['existing_job_id'] = job_id
            results.append(item)
        totals = {name: sum(item['action']==name for item in results) for name in ('new','duplicate','review')}
        return {'dry_run':request.dry_run,'counts':totals,'results':results}


@app.get('/api/reviews')
def get_reviews(status: Literal['pending', 'linked', 'independent', 'dismissed'] = 'pending'):
    with connect() as conn:
        items = [dict(r) for r in conn.execute('''SELECT r.*, j.title AS match_title,
             c.name AS match_company FROM import_reviews r
             LEFT JOIN jobs j ON j.id=r.match_job_id LEFT JOIN companies c ON c.id=j.company_id
             WHERE r.status=? ORDER BY r.id DESC''', (status,))]
        for item in items:
            item['input'] = json.loads(item.pop('input_json'))
        return items


@app.post('/api/reviews/{review_id}/resolve')
def resolve_review(review_id: int, decision: ReviewResolution):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        review = conn.execute('SELECT * FROM import_reviews WHERE id=?', (review_id,)).fetchone()
        if not review:
            raise HTTPException(404, '未找到待审核记录')
        if review['status'] != 'pending':
            raise HTTPException(409, '该条记录已处理，不能重复处理')
        row = ImportedJob.model_validate_json(review['input_json'])
        result_job_id = None
        if decision.action == 'link_source':
            if review['match_job_id'] is None or not row.source_url.strip():
                raise HTTPException(422, '没有可关联的现有岗位或有效来源链接')
            conflict = conn.execute('SELECT job_id FROM job_sources WHERE url=? AND length(url)>0 LIMIT 1',
                                    (row.source_url,)).fetchone()
            if conflict and conflict['job_id'] != review['match_job_id']:
                raise HTTPException(409, '该来源链接已关联到其他岗位')
            conn.execute('INSERT OR IGNORE INTO job_sources(job_id,platform,url) VALUES(?,?,?)',
                         (review['match_job_id'],row.source_platform,row.source_url))
            result_job_id = review['match_job_id']
            track(conn,result_job_id,'source_review_linked',{'review_id':review_id,'url':row.source_url})
            existing_jd = conn.execute('SELECT jd_text FROM jobs WHERE id=?',(result_job_id,)).fetchone()
            if existing_jd and existing_jd['jd_text'].strip() != row.jd_text.strip():
                # Preserve the reviewed candidate as history. Do not silently
                # replace the user's current JD or reinterpret the job status.
                track(conn,result_job_id,'source_jd_change_reviewed',{
                    'review_id':review_id,'candidate_jd':row.jd_text,'source_url':row.source_url})
            status='linked'
        elif decision.action == 'create_separate':
            if row.source_url.strip() and conn.execute('''SELECT 1 FROM job_sources
                    WHERE url=? AND length(url)>0 LIMIT 1''',(row.source_url,)).fetchone():
                raise HTTPException(409, '该来源链接已经属于其他岗位；不能新建重复来源，请关联已有岗位或驳回')
            company = conn.execute('SELECT id FROM companies WHERE name=? COLLATE NOCASE',(row.company,)).fetchone()
            company_id = company['id'] if company else conn.execute('INSERT INTO companies(name) VALUES(?)',(row.company,)).lastrowid
            result_job_id = conn.execute('''INSERT INTO jobs(company_id,title,direction,city,salary,duration,onsite_days,jd_text,status,priority,notes,
                team,eligibility,posted_at,deadline) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(company_id,row.title,row.direction,row.city,row.salary,row.duration,
                row.onsite_days,row.jd_text,row.status,row.priority,row.notes,row.team,row.eligibility,row.posted_at,row.deadline)).lastrowid
            conn.execute('INSERT INTO job_sources(job_id,platform,url) VALUES(?,?,?)',(result_job_id,row.source_platform,row.source_url))
            for name in dict.fromkeys(x.strip() for x in row.skills if x.strip()):
                conn.execute('INSERT OR IGNORE INTO skills(name) VALUES(?)',(name,))
                skill_id=conn.execute('SELECT id FROM skills WHERE name=? COLLATE NOCASE',(name,)).fetchone()['id']
                conn.execute('INSERT INTO job_skills(job_id,skill_id) VALUES(?,?)',(result_job_id,skill_id))
            track(conn,result_job_id,'review_created_separate',{'review_id':review_id,'jd_text':row.jd_text})
            status='independent'
        else:
            status='dismissed'
        conn.execute("UPDATE import_reviews SET status=?,resolved_job_id=?,resolved_at=datetime('now') WHERE id=?",
                     (status,result_job_id,review_id))
        sync_capture_review_outcomes(conn, review_id)
        return {'ok':True,'status':status,'job_id':result_job_id}


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: int):
    with connect() as conn:
        conn.execute('BEGIN')
        job = dict(required(conn, 'jobs', job_id))
        job['company'] = rowdict(required(conn, 'companies', job['company_id']))
        job['sources'] = [dict(r) for r in conn.execute('SELECT * FROM job_sources WHERE job_id=?', (job_id,))]
        job['skills'] = [dict(r) for r in conn.execute('''SELECT s.name,s.category,js.necessity,js.evidence FROM job_skills js
                         JOIN skills s ON s.id=js.skill_id WHERE js.job_id=? ORDER BY s.name''', (job_id,))]
        job['application'] = rowdict(conn.execute('SELECT * FROM applications WHERE job_id=?',(job_id,)).fetchone())
        job['history'] = [dict(r) for r in conn.execute('SELECT * FROM job_history WHERE job_id=? ORDER BY id DESC', (job_id,))]
        from .job_dossier import job_dossier
        job['dossier'] = job_dossier(conn, job)
        return job


@app.patch('/api/jobs/{job_id}')
def update_job(job_id: int, data: JobPatch):
    changes = data.model_dump(exclude_none=True, exclude={'application', 'expected_fields', 'expected_application'})
    if not changes and data.application is None:
        raise HTTPException(400, '没有需要更新的字段')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = required(conn, 'jobs', job_id)
        if data.expected_fields is not None:
            if set(data.expected_fields) != set(changes):
                raise HTTPException(422, '更新字段与原值不一致，请重新打开岗位')
            if any((current[key] or '') != value and current[key] != changes[key]
                   for key, value in data.expected_fields.items()):
                raise HTTPException(409, '你正在修改的岗位字段已在其他窗口更新。本次未保存；请复制你的修改，再重新打开岗位核对。')
        if data.application is not None and data.expected_application is not None:
            row = conn.execute('SELECT * FROM applications WHERE job_id=?', (job_id,)).fetchone()
            actual = {key: (row[key] or value) if row else value
                      for key, value in ApplicationIn().model_dump().items()}
            if actual != application_payload(data.expected_application, row) and actual != application_payload(data.application, row):
                raise HTTPException(409, '投递记录已在其他窗口更新。本次未保存；请复制你的修改，再重新打开岗位核对。')
        changes = {key: value for key,value in changes.items() if current[key] != value}
        if changes:
            previous = {key: current[key] for key in changes}
            clause = ','.join(f'{key}=?' for key in changes)
            conn.execute(f"UPDATE jobs SET {clause},updated_at=datetime('now') WHERE id=?", list(changes.values())+[job_id])
            track(conn, job_id, 'updated', {'before':previous,'after':changes})
        application_changed = write_application(conn, job_id, data.application) if data.application is not None else False
        return {'ok': True, 'changed': bool(changes) or application_changed}


@app.post('/api/jobs/{job_id}/sources', status_code=201)
def add_source(job_id: int, data: SourceIn):
    with connect() as conn:
        required(conn, 'jobs', job_id)
        if data.url:
            owner = conn.execute('SELECT job_id FROM job_sources WHERE url=? LIMIT 1',(data.url,)).fetchone()
            if owner and owner['job_id'] != job_id:
                raise HTTPException(409, '此来源链接已关联至其他岗位，请先检查重复记录')
        conn.execute('''INSERT INTO job_sources(job_id,platform,url,verification) VALUES(?,?,?,?)
          ON CONFLICT(job_id,platform,url) DO UPDATE SET verification=excluded.verification,last_checked_at=datetime('now')''',
          (job_id,data.platform,data.url,data.verification))
        track(conn, job_id, 'source_added', data.model_dump())
        return {'ok': True}


@app.patch('/api/jobs/{job_id}/sources/{source_id}')
def check_source(job_id: int, source_id: int, data: SourceReview):
    """Only an explicit user action records source verification; never infer job availability."""
    with connect() as conn:
        required(conn, 'jobs', job_id)
        source = conn.execute('SELECT * FROM job_sources WHERE id=? AND job_id=?', (source_id,job_id)).fetchone()
        if source is None:
            raise HTTPException(404, '岗位来源不存在')
        conn.execute('''UPDATE job_sources SET verification=?,last_checked_at=datetime('now') WHERE id=?''',
                     (data.verification,source_id))
        track(conn,job_id,'source_checked',{'source_id':source_id,'before':source['verification'],
                                          'after':data.verification,'user_confirmed':True})
        return {'ok':True}


@app.get('/api/jobs/{job_id}/skill-suggestions')
def job_skill_suggestions(job_id: int):
    with connect() as conn:
        job = required(conn, 'jobs', job_id)
        linked = {r['name'] for r in conn.execute('''SELECT s.name FROM job_skills js
            JOIN skills s ON s.id=js.skill_id WHERE js.job_id=?''', (job_id,))}
        return {'mode':'offline_keyword_review_only', 'auto_saved':False,
                'candidates':suggest_skills(job['jd_text'],linked)}


@app.put('/api/jobs/{job_id}/skills')
def replace_job_skills(job_id: int, data: SkillReview):
    """User-confirmed complete set of skills. Empty list explicitly clears it."""
    with connect() as conn:
        job = required(conn, 'jobs', job_id)
        normalized = set()
        for skill in data.skills:
            name = skill.name.strip()
            if not name:
                raise HTTPException(422, '技能名称不能为空')
            if name.casefold() in normalized:
                raise HTTPException(422, '技能名称重复')
            normalized.add(name.casefold())
            if skill.evidence and skill.evidence not in job['jd_text']:
                raise HTTPException(422, f'{name} 的参考资料片段不是当前 JD 原文')
        before = [dict(r) for r in conn.execute('''SELECT s.name,js.necessity,js.evidence FROM job_skills js
            JOIN skills s ON js.skill_id=s.id WHERE js.job_id=? ORDER BY s.name''', (job_id,))]
        conn.execute('DELETE FROM job_skills WHERE job_id=?', (job_id,))
        for skill in data.skills:
            name = skill.name.strip()
            conn.execute('INSERT OR IGNORE INTO skills(name,category) VALUES(?,?)', (name,skill.category))
            skill_id = conn.execute('SELECT id FROM skills WHERE name=? COLLATE NOCASE', (name,)).fetchone()['id']
            conn.execute('INSERT INTO job_skills(job_id,skill_id,necessity,evidence) VALUES(?,?,?,?)',
                         (job_id,skill_id,skill.necessity,skill.evidence))
        after = [dict(r) for r in conn.execute('''SELECT s.name,js.necessity,js.evidence FROM job_skills js
            JOIN skills s ON js.skill_id=s.id WHERE js.job_id=? ORDER BY s.name''', (job_id,))]
        from .requirements import source_hash
        track(conn,job_id,'skills_reviewed',{'before':before,'after':after,'user_confirmed':True,
                                           'source_jd_hash':source_hash(job)})
        return {'ok':True,'changed':before != after,'count':len(after)}


@app.get('/api/skills')
def skills():
    with connect() as conn:
        return [dict(r) for r in conn.execute('''SELECT s.name,s.category,count(DISTINCT js.job_id) AS job_count,
            sum(CASE WHEN js.necessity='must' THEN 1 ELSE 0 END) AS must_count,
            count(DISTINCT j.company_id) AS company_count FROM skills s
            JOIN job_skills js ON js.skill_id=s.id JOIN jobs j ON j.id=js.job_id
            GROUP BY s.id ORDER BY job_count DESC,s.name''')]


@app.get('/api/quality')
def quality_audit():
    """A reversible, read-only list of missing evidence; not a truth judgment."""
    with connect() as conn:
        rows = [dict(row) for row in conn.execute('''SELECT j.id,j.title,j.status,j.city,j.jd_text,j.team,j.eligibility,
            j.posted_at,j.deadline,c.name AS company,
            (SELECT count(*) FROM job_sources WHERE job_id=j.id AND length(trim(url))>0) AS url_count,
            (SELECT count(*) FROM job_sources WHERE job_id=j.id AND verification='verified') AS checked_source_count,
            (SELECT count(*) FROM job_skills WHERE job_id=j.id) AS skill_count
            FROM jobs j JOIN companies c ON j.company_id=c.id ORDER BY j.id DESC''')]
        invalid_evidence = {row['job_id'] for row in conn.execute('''SELECT js.job_id,js.evidence,j.jd_text FROM job_skills js
            JOIN jobs j ON j.id=js.job_id WHERE length(js.evidence)>0''')
            if row['evidence'] not in row['jd_text']}
        missing_totals = {'jd':0,'source_link':0,'city':0,'eligibility':0,
                          'unverified_source':0,'skills':0,'stale_skill_evidence':0}
        checks = []
        for job in rows:
            issues = []
            if not job['jd_text'].strip(): issues.append('jd')
            if not job['url_count']: issues.append('source_link')
            if not job['city'].strip(): issues.append('city')
            if not job['eligibility'].strip(): issues.append('eligibility')
            if not job['checked_source_count']: issues.append('unverified_source')
            if not job['skill_count']: issues.append('skills')
            if job['id'] in invalid_evidence: issues.append('stale_skill_evidence')
            for issue in issues: missing_totals[issue] += 1
            if issues:
                checks.append({'job_id':job['id'],'title':job['title'],'company':job['company'],
                               'issues':issues})
        return {'jobs_checked':len(rows),'issue_counts':missing_totals,'jobs_with_issues':checks}


@app.get('/api/applications')
def applications():
    with connect() as conn:
        return [dict(r) for r in conn.execute('''SELECT a.*,j.title,c.name AS company,j.id AS job_id
          FROM applications a JOIN jobs j ON a.job_id=j.id JOIN companies c ON j.company_id=c.id
          ORDER BY a.updated_at DESC''')]


def application_payload(data: ApplicationIn, current=None):
    payload = data.model_dump()
    for field in ('applied_on', 'follow_up_on', 'material_version_id'):
        if field not in data.model_fields_set and current:
            payload[field] = current[field] if field == 'material_version_id' else current[field] or ''
    return payload


def write_application(conn, job_id: int, data: ApplicationIn):
    current = conn.execute('SELECT * FROM applications WHERE job_id=?', (job_id,)).fetchone()
    payload = application_payload(data, current)
    previous = {key: (current[key] if key == 'material_version_id' else current[key] or '') for key in payload} if current else ApplicationIn().model_dump()
    if payload['material_version_id']:
        if not conn.execute('SELECT 1 FROM material_versions WHERE id=?', (payload['material_version_id'],)).fetchone():
            raise HTTPException(422, '材料版本不存在')
    if previous == payload:
        return False
    columns = ['job_id', *payload]
    updates = ','.join(f'{field}=excluded.{field}' for field in payload)
    conn.execute(f"INSERT INTO applications({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                 f"ON CONFLICT(job_id) DO UPDATE SET {updates},updated_at=datetime('now')",
                 (job_id, *payload.values()))
    track(conn,job_id,'application_updated',payload)
    return True


@app.put('/api/jobs/{job_id}/application')
def upsert_application(job_id: int, data: ApplicationIn):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        required(conn, 'jobs', job_id)
        write_application(conn, job_id, data)
        return {'ok':True}


@app.get('/api/learning')
def learning():
    with connect() as conn:
        return learning_records(conn)


@app.post('/api/learning', status_code=201)
def create_learning(data: LearningIn):
    with connect() as conn:
        if data.source_job_id is not None:
            required(conn, 'jobs', data.source_job_id)
        cur = conn.execute('''INSERT INTO learning_tasks(title,related_skill,source_job_id,priority,status,notes)
          VALUES(?,?,?,?,?,?)''', tuple(data.model_dump().values()))
        return {'id':cur.lastrowid}


@app.patch('/api/learning/{task_id}')
def update_learning(task_id: int, data: LearningPatch):
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, '没有需要更新的字段')
    with connect() as conn:
        if not conn.execute('SELECT id FROM learning_tasks WHERE id=?',(task_id,)).fetchone():
            raise HTTPException(404, '学习任务不存在')
        if changes.get('source_job_id') is not None:
            required(conn,'jobs',changes['source_job_id'])
        conn.execute('UPDATE learning_tasks SET '+','.join(f'{k}=?' for k in changes)+',revision=revision+1 WHERE id=?',
                     [*changes.values(),task_id])
        return {'ok':True}


@app.get('/api/evidence')
def list_evidence():
    with connect() as conn:
        return [evidence_record(r) for r in conn.execute('SELECT * FROM profile_evidence ORDER BY id DESC')]


@app.post('/api/evidence', status_code=201)
def add_evidence(data: EvidenceIn):
    with connect() as conn:
        return {'id': create_evidence(conn, data)}


@app.delete('/api/evidence/{evidence_id}')
def delete_evidence(evidence_id: int):
    with connect() as conn:
        count = conn.execute('DELETE FROM profile_evidence WHERE id=?',(evidence_id,)).rowcount
        if not count:
            raise HTTPException(404, '能力参考资料不存在')
        return {'ok':True}


@app.get('/api/backup/sqlite')
def download_sqlite_backup():
    # SQLite online backup produces a transactionally consistent snapshot including its WAL.
    init_db()
    with tempfile.NamedTemporaryFile(prefix='radar-backup-',suffix='.sqlite',delete=False) as tmp:
        temp_path = Path(tmp.name)
    try:
        with closing(sqlite3.connect(database_path())) as source, closing(sqlite3.connect(temp_path)) as destination:
            source.backup(destination)
        with closing(sqlite3.connect(f'file:{temp_path}?mode=ro', uri=True)) as check:
            if check.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('备份完整性检查失败')
        return FileResponse(temp_path,media_type='application/vnd.sqlite3',
            filename='zemu-backup.sqlite',background=BackgroundTask(temp_path.unlink, missing_ok=True))
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@app.get('/api/export')
def export_data():
    with connect() as conn:
        tables = ['example_records','personal_materials','material_versions','evidence_revisions','finding_revisions','finding_action_tasks','learning_outcomes','learning_completions','material_imports','evidence_material_quotes','companies','jobs','job_sources','job_history','skills','job_skills','applications','learning_tasks','import_reviews','profile_evidence','personal_skill_status','company_observations','analysis_packages','jd_extraction_batches','analysis_findings','job_requirements','ai_review_batches','ai_requirement_reviews','ai_review_events','skill_aliases','skill_alias_events','career_export_snapshots','capture_batches','capture_items','capture_search_runs','capture_search_results', 'planner_library', 'planner_families', 'planner_plans', 'planner_actions', 'discovery_sources', 'discovery_details', 'discovery_hits', 'discovery_receipts']
        tables += ['company_lists', 'company_list_entries', 'company_list_defaults', 'company_name_mappings', 'company_name_links']
        tables += ['boss_favorite_batches', 'boss_favorite_targets', 'boss_favorite_candidates', 'boss_favorite_runs']
        data = {name:[dict(r) for r in conn.execute(f'SELECT * FROM {name}')] for name in tables}
        for version in data['material_versions']:
            version.pop('content', None)
        return JSONResponse(data, headers={'Content-Disposition':'attachment; filename="zemu-export.json"'})


@app.get('/api/health')
def health():
    return {'ok': True, 'version': VERSION, 'db_schema_version':SCHEMA_VERSION,
            'collectors_enabled': False, 'external_collectors_optional': True,
            'auto_apply_enabled': False}
