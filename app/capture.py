"""User-initiated capture inbox and durable, resumable intake batches.

No browser automation, OCR claim, unsolicited network access, or automatic application.
Screenshots are evidence only until the user supplies/validates transcribed text.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import sqlite3
from contextlib import closing
import tempfile
import zipfile
from starlette.background import BackgroundTask
from PIL import Image, UnidentifiedImageError
import json
import re
import secrets
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from .directions import Direction
from .company_profile import fill_company_evidence
from .db import connect, database_path, sync_capture_review_outcomes
from . import ocr_adapter, external_collectors

router = APIRouter(prefix='/api/capture', tags=['capture'])

MAX_TEXT = 100_000
MAX_IMAGE = 6 * 1024 * 1024
PLATFORMS = {'boss', 'shixiseng', 'company', 'other', 'manual'}


def _http_url(value: str) -> str:
    value = value.strip()
    if value:
        s = urlsplit(value)
        if s.scheme not in ('http', 'https') or not s.hostname or s.username or s.password:
            raise ValueError('只接受不含账号凭据的 http/https 岗位链接')
    return value


class BatchIn(BaseModel):
    label: str = Field(default='', max_length=160)
    platform: Literal['boss', 'shixiseng', 'company', 'other', 'manual'] = 'manual'
    keyword: str = Field(default='', max_length=1000)
    city: str = Field(default='', max_length=100)


class CaptureIn(BaseModel):
    raw_text: str = Field(default='', max_length=MAX_TEXT)
    source_url: str = Field(default='', max_length=2000)
    # Optional fields can be provided from trusted local/manual tools, not guessed.
    company: str = Field(default='', max_length=140)
    title: str = Field(default='', max_length=180)
    city: str = Field(default='', max_length=120)

    @field_validator('source_url')
    @classmethod
    def validate_url(cls, value):
        return _http_url(value)


class CapturePatch(BaseModel):
    jd_text: str | None = Field(default=None, max_length=MAX_TEXT)
    source_url: str | None = Field(default=None, max_length=2000)
    company: str | None = Field(default=None, max_length=140)
    title: str | None = Field(default=None, max_length=180)
    city: str | None = Field(default=None, max_length=120)
    direction: Direction | None = None
    salary: str | None = Field(default=None, max_length=200)
    duration: str | None = Field(default=None, max_length=200)
    onsite_days: str | None = Field(default=None, max_length=200)
    eligibility: str | None = Field(default=None, max_length=1000)
    posted_at: str | None = Field(default=None, max_length=10)
    deadline: str | None = Field(default=None, max_length=10)

    @field_validator('posted_at', 'deadline')
    @classmethod
    def validate_dates(cls, value):
        from .main import valid_date
        return valid_date(value) if value is not None else None

    @field_validator('source_url')
    @classmethod
    def validate_url(cls, value):
        return _http_url(value) if value is not None else None


class ConfirmCapture(BaseModel):
    company: str = Field(min_length=1, max_length=140)
    title: str = Field(min_length=1, max_length=180)
    city: str = Field(default='', max_length=120)
    jd_text: str = Field(min_length=1, max_length=MAX_TEXT)
    source_url: str = Field(default='', max_length=2000)
    direction: Direction = 'other'
    salary: str = Field(default='', max_length=200)
    duration: str = Field(default='', max_length=200)
    onsite_days: str = Field(default='', max_length=200)
    eligibility: str = Field(default='', max_length=1000)
    posted_at: str = Field(default='', max_length=10)
    deadline: str = Field(default='', max_length=10)

    @field_validator('posted_at', 'deadline')
    @classmethod
    def validate_dates(cls, value):
        from .main import valid_date
        return valid_date(value)

    @field_validator('company', 'title', 'jd_text')
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError('企业、岗位和完整 JD 必须填写；不能凭截图自动推断')
        return value.strip()

    @field_validator('source_url')
    @classmethod
    def validate_url(cls, value):
        return _http_url(value)


class BatchStatus(BaseModel):
    action: Literal['pause', 'resume', 'complete']


class ItemDisposition(BaseModel):
    action: Literal['defer', 'dismiss', 'reopen']


def _batch(conn, batch_id):
    result = conn.execute('SELECT * FROM capture_batches WHERE id=?', (batch_id,)).fetchone()
    if result is None:
        raise HTTPException(404, '采集批次不存在')
    return result


def _item(conn, item_id):
    result = conn.execute('''SELECT i.*, b.platform, b.status AS batch_status
                             FROM capture_items i JOIN capture_batches b ON b.id=i.batch_id
                             WHERE i.id=?''', (item_id,)).fetchone()
    if result is None:
        raise HTTPException(404, '采集记录不存在')
    return result


def _info(row, conn=None):
    record = dict(row)
    run_observed_at = record.get('observed_at')
    record['draft'] = json.loads(record.pop('draft_json'))
    record['has_screenshot'] = bool(record.pop('screenshot_file'))
    record['observed_at'] = record.get('observed_at') or record['draft'].get('observed_at', '')
    record['reused_snapshot'] = bool(record.get('reused_snapshot', record['draft'].get('reused_snapshot', False)))
    if run_observed_at and not record['reused_snapshot']:
        # An unchanged JD shares an intake item across runs. Display this run's
        # real detail visit without rewriting the older run's saved metadata.
        record['draft'].update(observed_at=run_observed_at, last_detail_at=run_observed_at, reused_snapshot=False)
    record['known_job_id'] = None
    record['source_state'] = 'new'
    if conn is not None and record['source_url']:
        known = conn.execute('''SELECT j.id,j.jd_text,s.last_checked_at FROM jobs j
            JOIN job_sources s ON s.job_id=j.id WHERE s.url=? LIMIT 1''', (record['source_url'],)).fetchone()
        if known:
            record['known_job_id'] = known['id']
            record['source_state'] = 'already_saved' if known['jd_text'].strip() == record['raw_text'].strip() else 'jd_changed'
            record['last_checked_at'] = known['last_checked_at']
    return record


def _summary(conn, row):
    result = dict(row)
    counts = {r['status']: r['count'] for r in conn.execute(
        'SELECT status,COUNT(*) AS count FROM capture_items WHERE batch_id=? GROUP BY status', (row['id'],))}
    result['counts'] = {s: counts.get(s, 0) for s in ('pending', 'deferred', 'dismissed', 'imported', 'duplicate', 'review')}
    result['total'] = sum(counts.values())
    result['remaining'] = counts.get('pending', 0)
    search = conn.execute('SELECT id FROM capture_search_runs WHERE batch_id=? ORDER BY id DESC LIMIT 1', (row['id'],)).fetchone()
    result['latest_search_run_id'] = search['id'] if search else None
    return result


def _create_batch(conn, data: BatchIn):
    cur = conn.execute('INSERT INTO capture_batches(label,platform,keyword,city) VALUES(?,?,?,?)',
                       ((data.label.strip() or ' · '.join(filter(None, [data.keyword.strip(),data.city.strip()])) or '手动采集')[:160],data.platform,data.keyword.strip(),data.city.strip()))
    return _summary(conn, _batch(conn, cur.lastrowid))


@router.post('/batches', status_code=201)
def create_batch(data: BatchIn):
    with connect() as conn:
        return _create_batch(conn, data)


@router.get('/batches')
def list_batches():
    with connect() as conn:
        return [_summary(conn,row) for row in conn.execute('SELECT * FROM capture_batches ORDER BY id DESC LIMIT 100')]


@router.get('/batches/{batch_id}')
def get_batch(batch_id: int):
    with connect() as conn:
        return _summary(conn, _batch(conn, batch_id))


@router.patch('/batches/{batch_id}')
def batch_status(batch_id: int, data: BatchStatus):
    with connect() as conn:
        row = _batch(conn, batch_id)
        if data.action == 'complete' and conn.execute(
            "SELECT 1 FROM capture_items WHERE batch_id=? AND status='pending' LIMIT 1", (batch_id,)).fetchone():
            raise HTTPException(409, '仍有待处理的岗位，不得标记整个批次完成')
        status = {'pause':'paused','resume':'open','complete':'completed'}[data.action]
        if row['status']=='completed' and data.action=='pause':
            raise HTTPException(409, '已完成批次若需继续，请先重新打开')
        conn.execute("UPDATE capture_batches SET status=?,updated_at=datetime('now') WHERE id=?", (status,batch_id))
        return _summary(conn, _batch(conn,batch_id))


@router.post('/batches/{batch_id}/items', status_code=201)
def add_item(batch_id: int, data: CaptureIn):
    with connect() as conn:
        batch = _batch(conn, batch_id)
        if batch['status']!='open':
            raise HTTPException(409,'批次已暂停或关闭；先恢复批次')
        if not data.raw_text.strip() and not data.source_url:
            # Screenshot may be uploaded immediately after creation.
            # Distinct empty drafts must not collapse to a single fingerprint.
            fingerprint = secrets.token_hex(32)
        else:
            basis = json.dumps([data.raw_text.strip(),data.source_url],ensure_ascii=False)
            fingerprint = hashlib.sha256(basis.encode('utf-8')).hexdigest()
        existing = conn.execute('SELECT id FROM capture_items WHERE batch_id=? AND fingerprint=?',
                                (batch_id,fingerprint)).fetchone()
        if existing:
            return {'id':existing['id'],'already_exists':True}
        cur = conn.execute('''INSERT INTO capture_items(batch_id,fingerprint,raw_text,source_url,draft_json)
                              VALUES(?,?,?,?,?)''', (batch_id,fingerprint,data.raw_text,data.source_url,
                              json.dumps(data.model_dump(exclude={'raw_text','source_url'}),ensure_ascii=False)))
        conn.execute("UPDATE capture_batches SET updated_at=datetime('now') WHERE id=?",(batch_id,))
        return {'id':cur.lastrowid,'already_exists':False}


@router.get('/batches/{batch_id}/items')
def list_items(batch_id: int, run_id: int | None = None):
    with connect() as conn:
        _batch(conn,batch_id)
        if run_id is not None:
            search = conn.execute('SELECT batch_id FROM capture_search_runs WHERE id=?', (run_id,)).fetchone()
            if search is None or search['batch_id'] != batch_id:
                raise HTTPException(404, '搜索不属于该批次')
            return [_info(r, conn) for r in conn.execute('''SELECT i.*,r.observed_at,r.reused_snapshot
                FROM capture_items i JOIN capture_search_results r ON r.item_id=i.id
                WHERE i.batch_id=? AND r.search_run_id=? ORDER BY i.id DESC LIMIT 1000''', (batch_id, run_id))]
        return [_info(r, conn) for r in conn.execute(
            'SELECT * FROM capture_items WHERE batch_id=? ORDER BY id DESC LIMIT 1000',(batch_id,))]


class BulkItemDisposition(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)
    batch_id: int = Field(gt=0)
    action: Literal['defer', 'dismiss', 'reopen']


class BulkCapturePreview(BaseModel):
    batch_id: int = Field(gt=0)
    ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator('ids')
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value) or any(ident <= 0 for ident in value):
            raise ValueError('资料 ID 必须是互不重复的正整数')
        return value


class BulkCaptureSelection(BaseModel):
    id: int = Field(gt=0)
    version: str = Field(pattern=r'^[a-f0-9]{64}$')


class BulkCaptureCommit(BaseModel):
    batch_id: int = Field(gt=0)
    items: list[BulkCaptureSelection] = Field(min_length=1, max_length=100)


def _capture_version(item):
    # Content hash, not second-resolution updated_at. State is checked separately;
    # keeping it out of this hash allows a lost-response retry without duplicates.
    fields = ('id', 'batch_id', 'platform', 'raw_text', 'source_url', 'draft_json', 'screenshot_file')
    content = json.dumps([item[key] for key in fields], ensure_ascii=False)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _capture_data(item):
    draft = json.loads(item['draft_json'])
    values = {key: draft[key] for key in ConfirmCapture.model_fields if key in draft}
    values.setdefault('jd_text', item['raw_text'])
    values.setdefault('source_url', item['source_url'])
    return ConfirmCapture.model_validate(values)


def _bulk_duplicate(conn, data):
    if data.source_url and conn.execute('SELECT 1 FROM job_sources WHERE url=? LIMIT 1',
                                       (data.source_url,)).fetchone():
        return '来源已收录，请单独核对是否有更新'
    if conn.execute('''SELECT 1 FROM jobs j JOIN companies c ON c.id=j.company_id
         WHERE c.name=? COLLATE NOCASE AND j.title=? COLLATE NOCASE
         AND j.city=? COLLATE NOCASE LIMIT 1''', (data.company, data.title, data.city)).fetchone():
        return '与已收录的企业、岗位、城市相同，请单独判重'
    return ''


@router.post('/items/preview-bulk')
def preview_capture_bulk(data: BulkCapturePreview):
    with connect() as conn:
        conn.execute('BEGIN')
        if _batch(conn, data.batch_id)['status'] != 'open':
            raise HTTPException(409, '请先恢复采集批次，再集中核对')
        rows = []
        identities = {}
        for ident in data.ids:
            item = _item(conn, ident)
            if item['batch_id'] != data.batch_id:
                raise HTTPException(409, '选中的资料不属于当前批次，请刷新收件箱')
            info = _info(item)
            row = {'id': ident, 'version': _capture_version(item), 'item': info,
                   'data': None, 'issue': ''}
            if item['status'] != 'pending':
                row['issue'] = '状态已变化，请刷新收件箱'
            else:
                try:
                    candidate = _capture_data(item)
                    row['data'] = candidate.model_dump()
                    row['issue'] = _bulk_duplicate(conn, candidate)
                    keys = [('title', candidate.company.casefold(), candidate.title.casefold(), candidate.city.casefold())]
                    if candidate.source_url:
                        keys.append(('url', candidate.source_url))
                    for key in keys:
                        identities.setdefault(key, []).append(row)
                except ValidationError as exc:
                    names = {'company': '企业名称', 'title': '岗位名称', 'jd_text': '完整 JD',
                             'source_url': '来源链接', 'posted_at': '发布日期', 'deadline': '截止日期',
                             'direction': '岗位方向', 'city': '城市', 'salary': '薪资',
                             'duration': '实习时长', 'onsite_days': '出勤', 'eligibility': '资格条件'}
                    fields = dict.fromkeys(names.get(e['loc'][0], str(e['loc'][0])) for e in exc.errors())
                    row['issue'] = '缺失或格式有误：' + '、'.join(fields) + '，请单独修正'
            rows.append(row)
        for matches in identities.values():
            if len(matches) > 1:
                for row in matches:
                    row['issue'] = row['issue'] or '本次核对中存在相同来源或相似岗位，请单独判重'
        return {'batch_id': data.batch_id, 'items': rows}


@router.post('/items/commit-bulk')
def commit_capture_bulk(data: BulkCaptureCommit):
    if len({row.id for row in data.items}) != len(data.items):
        raise HTTPException(422, '不能重复提交同一条资料')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if _batch(conn, data.batch_id)['status'] != 'open':
            raise HTTPException(409, '批次已暂停或完成，请重新核对；本次未写入')
        results = []
        for selection in data.items:
            item = _item(conn, selection.id)
            if item['batch_id'] != data.batch_id or _capture_version(item) != selection.version:
                raise HTTPException(409, f'资料 #{selection.id} 已变化，请重新核对；本次未写入')
            try:
                candidate = _capture_data(item)
            except ValidationError:
                raise HTTPException(409, f'资料 #{selection.id} 不完整，请单独修正；本次未写入')
            if item['status'] == 'imported' and json.loads(item['confirmed_json']) == candidate.model_dump():
                results.append({'id': selection.id, 'job_id': item['job_id'], 'already_processed': True})
                continue
            if item['status'] != 'pending':
                raise HTTPException(409, f'资料 #{selection.id} 状态已变化，请重新核对；本次未写入')
            issue = _bulk_duplicate(conn, candidate)
            if issue:
                raise HTTPException(409, f'资料 #{selection.id}：{issue}；本次未写入')
            result = _commit_item(conn, selection.id, candidate)
            results.append({'id': selection.id, **result})
        return {'items': results, 'imported': sum(not row['already_processed'] for row in results),
                'already_processed': sum(row['already_processed'] for row in results)}


@router.post('/items/disposition-bulk')
def bulk_item_disposition(data: BulkItemDisposition):
    if len(set(data.ids)) != len(data.ids) or any(ident <= 0 for ident in data.ids):
        raise HTTPException(422, '资料 ID 必须是互不重复的正整数')
    required_status = 'pending' if data.action != 'reopen' else None
    to_status = {'defer': 'deferred', 'dismiss': 'dismissed', 'reopen': 'pending'}[data.action]
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        batch = _batch(conn, data.batch_id)
        if batch['status'] != 'open':
            raise HTTPException(409, '只能批量处理进行中的采集批次')
        items = [_item(conn, ident) for ident in data.ids]
        if any(item['batch_id'] != data.batch_id or
               (item['status'] != required_status if required_status else
                item['status'] not in ('deferred', 'dismissed'))
               for item in items):
            raise HTTPException(409, '选中资料的批次或状态已经变化，没有修改任何记录')
        for ident in data.ids:
            conn.execute("UPDATE capture_items SET status=?,updated_at=datetime('now') WHERE id=?",
                         (to_status, ident))
        return {'ok': True, 'changed': len(data.ids), 'status': to_status}


@router.patch('/items/{item_id}')
def edit_item(item_id: int, data: CapturePatch):
    with connect() as conn:
        row = _item(conn,item_id)
        if row['status']!='pending' or row['batch_status']=='completed':
            raise HTTPException(409,'只能编辑未提交的采集记录')
        changes = data.model_dump(exclude_unset=True)
        draft = json.loads(row['draft_json'])
        draft.update(changes)
        # The initial capture remains immutable; reviewed corrections live in draft_json.
        conn.execute("""UPDATE capture_items SET draft_json=?,updated_at=datetime('now') WHERE id=?""",
                     (json.dumps(draft,ensure_ascii=False),item_id))
        return _info(_item(conn,item_id))


@router.patch('/items/{item_id}/disposition')
def change_disposition(item_id: int, data: ItemDisposition):
    with connect() as conn:
        item = _item(conn, item_id)
        if item['batch_status'] != 'open' or item['status'] not in ('pending', 'deferred', 'dismissed'):
            raise HTTPException(409, '仅可调整进行中批次内尚未入库的资料')
        status = {'defer': 'deferred', 'dismiss': 'dismissed', 'reopen': 'pending'}[data.action]
        conn.execute("UPDATE capture_items SET status=?,updated_at=datetime('now') WHERE id=?", (status, item_id))
        return _info(_item(conn, item_id), conn)


def _image_format(data: bytes) -> str | None:
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'png'
    if data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'): return 'jpg'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'webp'
    return None


def _evidence_directory() -> Path:
    return database_path().parent / 'capture-evidence'


@router.post('/items/{item_id}/screenshot')
async def upload_screenshot(item_id: int, request: Request):
    # Read in bounded chunks, not into an arbitrarily large in-memory body.
    with connect() as conn:
        row = _item(conn,item_id)
        if row['status']!='pending' or row['batch_status']=='completed':
            raise HTTPException(409,'已处理的记录不能替换截图')
    raw=bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw)>MAX_IMAGE:
            raise HTTPException(413,'截图不能超过 6MB')
    kind=_image_format(raw)
    if not kind:
        raise HTTPException(415,'截图必须是 PNG、JPEG 或 WebP')
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in {'PNG':'PNG','JPEG':'JPEG','WEBP':'WEBP'} or img.width*img.height>40_000_000:
                raise ValueError('不支持的图片格式或尺寸')
            img.verify()
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(415,'截图文件损坏或尺寸异常')
    image_id=secrets.token_hex(16)
    directory=_evidence_directory();directory.mkdir(parents=True,exist_ok=True)
    target=directory/f'{image_id}.{kind}'
    target.write_bytes(raw)
    try:
        with connect() as conn:
            row=_item(conn,item_id)
            if row['status']!='pending' or row['batch_status']=='completed':
                raise HTTPException(409,'记录已处理，不能替换截图')
            prior=row['screenshot_file']
            conn.execute("UPDATE capture_items SET screenshot_file=?,updated_at=datetime('now') WHERE id=?",
                        (target.name,item_id))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if prior and prior!=target.name:
        (_evidence_directory()/prior).unlink(missing_ok=True)
    return {'has_screenshot':True,'size':len(raw)}


@router.get('/items/{item_id}/screenshot')
def view_screenshot(item_id: int):
    with connect() as conn:
        row=_item(conn,item_id)
        name=row['screenshot_file']
    if not name or not re.fullmatch(r'[a-f0-9]{32}\.(png|jpg|webp)',name):
        raise HTTPException(404,'没有截图')
    path=_evidence_directory()/name
    if not path.is_file():
        raise HTTPException(404,'截图文件不在本机（请检查备份）')
    return FileResponse(path,media_type={'png':'image/png','jpg':'image/jpeg','webp':'image/webp'}[name.rsplit('.',1)[1]],
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.post('/items/{item_id}/commit')
def commit_item(item_id: int, data: ConfirmCapture):
    """Single SQLite transaction: either the job/review AND checkpoint both exist, or neither does."""
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        return _commit_item(conn, item_id, data)


def _commit_item(conn, item_id: int, data: ConfirmCapture):
    item=_item(conn,item_id)
    if item['status']!='pending':
        return {'status':item['status'],'job_id':item['job_id'],'review_id':item['review_id'], 'already_processed':True}
    if item['batch_status']!='open':
        raise HTTPException(409,'批次处于暂停或完成状态')
    # Prevent a stale page form from silently replacing the evidence collected.
    if data.source_url != json.loads(item['draft_json']).get('source_url',item['source_url']):
        raise HTTPException(409,'来源链接与采集原件不符，请先更新收件箱记录再提交')
    draft = json.loads(item['draft_json'])
    # Older clients omit date controls; preserve captured values unless the
    # caller explicitly clears them with an empty string.
    from .main import valid_date
    for field in ('posted_at', 'deadline'):
        if field not in data.model_fields_set:
            try:
                setattr(data, field, valid_date(draft.get(field, '') or ''))
            except ValueError as exc:
                raise HTTPException(422, f'{field} 日期无效，请先修正采集草稿') from exc
    if 'jd_text' in draft and (draft['jd_text'] or '').strip() != data.jd_text:
        raise HTTPException(409,'JD 与核验表单最新草稿不一致，请先保存确认内容')
    # Exact source identity is stronger than title matching.
    duplicate = conn.execute('SELECT job_id FROM job_sources WHERE url=? AND length(url)>0 LIMIT 1',
                             (data.source_url,)).fetchone() if data.source_url else None
    review_id=None
    job_id=None
    if duplicate:
        job_id=duplicate['job_id']
        existing_jd = conn.execute('SELECT jd_text,posted_at,deadline FROM jobs WHERE id=?', (job_id,)).fetchone()
        if existing_jd and (existing_jd['jd_text'].strip() != data.jd_text.strip() or
                any(getattr(data, key) and getattr(data, key) != existing_jd[key]
                    for key in ('posted_at', 'deadline'))):
            # A URL match does not mean the content is unchanged. Preserve the
            # new source snapshot in the existing manual-review workflow.
            status='review'
            payload=json.dumps({'company':data.company,'title':data.title,'city':data.city,
                'jd_text':data.jd_text,'direction':data.direction,'salary':data.salary,
                'eligibility':data.eligibility,'duration':data.duration,'onsite_days':data.onsite_days,'source_url':data.source_url,
                'posted_at':data.posted_at,'deadline':data.deadline,
                'source_platform':item['platform']},ensure_ascii=False,sort_keys=True)
            fingerprint=hashlib.sha256(payload.encode()).hexdigest()
            conn.execute('''INSERT OR IGNORE INTO import_reviews(fingerprint,input_json,match_job_id,reason)
              VALUES(?,?,?,?)''',(fingerprint,payload,job_id,'相同来源链接的岗位资料已变化，须人工核对新版本'))
            prior_review=conn.execute('SELECT id,status FROM import_reviews WHERE fingerprint=?',(fingerprint,)).fetchone()
            if prior_review['status']=='pending':
                review_id=prior_review['id']
            else:
                # This exact version has been reviewed previously. Do not
                # point the capture UI at a resolved review that cannot reopen.
                status='duplicate'
        else:
            status='duplicate'
    else:
        similar=conn.execute('''SELECT j.id FROM jobs j JOIN companies c ON c.id=j.company_id
          WHERE c.name=? COLLATE NOCASE AND j.title=? COLLATE NOCASE AND j.city=? COLLATE NOCASE LIMIT 1''',
          (data.company,data.title,data.city)).fetchone()
        if similar:
            status='review'
            # Follow the existing manual-review path, preserving candidate JD.
            payload=json.dumps({'company':data.company,'title':data.title,'city':data.city,
              'jd_text':data.jd_text,'direction':data.direction,'salary':data.salary,
              'eligibility':data.eligibility,'duration':data.duration,'onsite_days':data.onsite_days,'source_url':data.source_url,
              'posted_at':data.posted_at,'deadline':data.deadline,
              'source_platform':item['platform']},ensure_ascii=False,sort_keys=True)
            fingerprint=hashlib.sha256(payload.encode()).hexdigest()
            conn.execute('''INSERT OR IGNORE INTO import_reviews(fingerprint,input_json,match_job_id,reason)
              VALUES(?,?,?,?)''',(fingerprint,payload,similar['id'],'与现有企业/岗位/城市相似，需人工判重'))
            review_id=conn.execute('SELECT id FROM import_reviews WHERE fingerprint=?',(fingerprint,)).fetchone()['id']
        else:
            status='imported'
            company=conn.execute('SELECT id FROM companies WHERE name=? COLLATE NOCASE',(data.company,)).fetchone()
            company_id=company['id'] if company else conn.execute('INSERT INTO companies(name) VALUES(?)',(data.company,)).lastrowid
            job_id=conn.execute('''INSERT INTO jobs(company_id,title,direction,city,salary,eligibility,jd_text,duration,onsite_days,posted_at,deadline)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(company_id,data.title,data.direction,data.city,data.salary,data.eligibility,data.jd_text,data.duration,data.onsite_days,data.posted_at,data.deadline)).lastrowid
            conn.execute('INSERT INTO job_sources(job_id,platform,url) VALUES(?,?,?)',
                         (job_id,item['platform'],data.source_url))
            conn.execute('INSERT INTO job_history(job_id,event,payload_json) VALUES(?,?,?)',
                         (job_id,'captured',json.dumps({'batch_id':item['batch_id'],'capture_item_id':item_id,
                            'source_url':data.source_url,'jd_text':data.jd_text,'screenshot_attached':bool(item['screenshot_file'])},ensure_ascii=False)))
    if draft.get('company_evidence'):
        company = conn.execute('SELECT id FROM companies WHERE name=? COLLATE NOCASE', (data.company,)).fetchone()
        if company:
            fill_company_evidence(conn, company['id'], draft, data.company)
    conn.execute("""UPDATE capture_items SET status=?,job_id=?,review_id=?,
                  confirmed_json=?,updated_at=datetime('now') WHERE id=?""",
                 (status,job_id,review_id,data.model_dump_json(),item_id))
    if review_id is not None and sync_capture_review_outcomes(conn, review_id):
        updated = _item(conn, item_id)
        status, job_id = updated['status'], updated['job_id']
    conn.execute("UPDATE capture_batches SET updated_at=datetime('now') WHERE id=?",(item['batch_id'],))
    return {'status':status,'job_id':job_id,'review_id':review_id,'already_processed':False}

@router.get('/backup')
def download_capture_backup():
    """SQLite snapshot, screenshot evidence and owned search results in one ZIP."""
    directory=_evidence_directory()
    tempdir=Path(tempfile.mkdtemp(prefix='radar-bundle-'))
    snapshot=tempdir/'radar.sqlite'
    archive=tempdir/'radar-complete-backup.zip'
    try:
        # Use SQLite's backup API to obtain a transactionally consistent image.
        with connect() as source:
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination)
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as out:
            out.write(snapshot,'radar.sqlite')
            names=set()
            with closing(sqlite3.connect(snapshot)) as db:
                names={r[0] for r in db.execute("SELECT screenshot_file FROM capture_items WHERE screenshot_file != ''")}
                from .local_collector import owned_snapshot_tasks
                tasks = owned_snapshot_tasks(db)
            from . import shixiseng_runtime, boss_runtime, boss_favorites
            missing_tasks = []
            for platform, task_id in tasks:
                runtime = {'boss': boss_runtime, 'shixiseng': shixiseng_runtime, 'boss-favorite': boss_favorites}[platform]
                try:
                    value = runtime.read_snapshot(task_id)
                except runtime.SnapshotMissingError:
                    missing_tasks.append(task_id)
                    continue
                out.writestr(f'{platform}-runs/{task_id}/result.json', json.dumps(value, ensure_ascii=False))
            included=[];missing=[]
            for name in sorted(names):
                if not re.fullmatch(r'[a-f0-9]{32}\.(png|jpg|webp)',name):
                    raise ValueError('数据库中存在非法截图引用，不能生成完整备份')
                path=directory/name
                if path.is_file():
                    out.write(path,f'capture-evidence/{name}');included.append(name)
                else:
                    missing.append(name)
            if missing:
                raise HTTPException(409, '截图文件缺失或上传时发生替换，完整备份已取消：'+', '.join(missing[:3]))
            out.writestr('manifest.json',json.dumps({'schema':'radar-capture-backup-v1',
                'evidence_count':len(included),'missing_evidence':missing,
                'missing_search_snapshots':missing_tasks},ensure_ascii=False,indent=2))
        return FileResponse(archive,filename='ai-radar-complete-backup.zip',media_type='application/zip',
            background=BackgroundTask(shutil.rmtree,tempdir,ignore_errors=True))
    except Exception:
        shutil.rmtree(tempdir,ignore_errors=True)
        raise

# v0.7 existing-engine integrations. External data enters the human review queue.
def _enqueue_from_adapter(conn, batch_id: int, candidate: dict) -> bool:
    fingerprint = hashlib.sha256(json.dumps([candidate['raw_text'], candidate['source_url']],
                                  ensure_ascii=False).encode('utf-8')).hexdigest()
    draft = {key: candidate.get(key, '') for key in ('company', 'title', 'city', 'salary', 'duration', 'onsite_days', 'employment_type', 'observed_at', 'last_detail_at', 'reused_snapshot', 'jd_available', 'eligibility', 'posted_at', 'deadline')}
    if candidate.get('company_evidence'):
        draft['company_evidence'] = candidate['company_evidence']
    cursor = conn.execute("""INSERT OR IGNORE INTO capture_items
               (batch_id,fingerprint,raw_text,source_url,draft_json) VALUES(?,?,?,?,?)""",
               (batch_id, fingerprint, candidate['raw_text'], candidate['source_url'],
                json.dumps(draft, ensure_ascii=False)))
    if not cursor.rowcount and draft.get('company_evidence'):
        old = conn.execute('SELECT * FROM capture_items WHERE batch_id=? AND fingerprint=?', (batch_id, fingerprint)).fetchone()
        saved = json.loads(old['draft_json'])
        if saved.get('company', '').casefold() == candidate['company'].casefold():
            saved['company_evidence'] = draft['company_evidence']
            conn.execute("UPDATE capture_items SET draft_json=?,updated_at=datetime('now') WHERE id=?",
                         (json.dumps(saved, ensure_ascii=False), old['id']))
            if old['status'] in {'imported', 'duplicate'} and old['job_id']:
                company = conn.execute('SELECT c.* FROM companies c JOIN jobs j ON j.company_id=c.id WHERE j.id=?', (old['job_id'],)).fetchone()
                if company and company['name'].casefold() == saved['company'].casefold():
                    fill_company_evidence(conn, company['id'], saved, company['name'])
    return cursor.rowcount > 0


@router.get('/ocr/status')
def ocr_status():
    return {'provider': 'rapidocr', 'installed': ocr_adapter.is_available(),
            'local_only': True, 'review_required': True}


@router.post('/items/{item_id}/ocr')
def ocr_screenshot(item_id: int):
    if not ocr_adapter.is_available():
        raise HTTPException(503, '未安装 RapidOCR；请安装 requirements-ocr.txt')
    with connect() as conn:
        row = _item(conn, item_id)
        if row['status'] != 'pending':
            raise HTTPException(409, '已提交的记录不能再 OCR')
        name = row['screenshot_file']
    if not name or not re.fullmatch(r'[a-f0-9]{32}\.(png|jpg|webp)', name):
        raise HTTPException(404, '请先上传截图')
    path = _evidence_directory() / name
    if not path.is_file():
        raise HTTPException(404, '截图已不存在')
    try:
        result = ocr_adapter.recognize_file(path)
    except Exception as exc:
        raise HTTPException(502, 'OCR 识别失败，请检查图片或人工录入') from exc
    # Deliberately no database mutation: OCR text is a candidate to review.
    return {**result, 'review_required': True}


class ConfirmExternalImport(BaseModel):
    batch_id: int
    confirmed: Literal[True]
    limit: int = Field(default=100, ge=1, le=500)
    restart_from_first: bool = False


@router.get('/connectors/bosshunter/status')
def boss_connector_status(batch_id: int | None = None, preflight: bool = False):
    from . import search_runs
    run = search_runs.latest_run(batch_id) if batch_id else None
    if run and run.get('platform', 'boss') != 'boss':
        run = None
    if run and search_runs.local_platform(run) == 'boss':
        return boss_native_status(batch_id, preflight)
    try:
        data = external_collectors.request_bosshunter('/api/agent/state')
        result = {'connected': isinstance(data, dict) and 'error' not in data,
                'origin': external_collectors.BOSSHUNTER_ORIGIN, 'mode': 'read_only',
                'preferences': external_collectors.public_preferences(data.get('preferences')) if isinstance(data, dict) else {},
                'task': external_collectors.public_task(data.get('task')) if isinstance(data, dict) else None,
                'last_task': external_collectors.public_task(data.get('last_task')) if isinstance(data, dict) else None,
                'search_run': run}
        try:
            result['capabilities'] = search_runs.capabilities()
            if run and run['task_id']:
                result['search_run'] = search_runs.refresh_run(run['id'])
        except HTTPException as exc:
            result['result_error'] = str(exc.detail)
            result.setdefault('capabilities', {'matched_results': False})
        if preflight:
            try:
                result['readiness'] = search_runs.readiness()
            except HTTPException as exc:
                result['readiness'] = {'ok': False, 'checks': [], 'messages': [str(exc.detail)]}
        return result
    except external_collectors.ConnectorError:
        return {'connected': False, 'origin': external_collectors.BOSSHUNTER_ORIGIN,
                'mode': 'read_only', 'search_run': run, 'help': '请使用 BOSS 链路启动脚本启动服务；任务与已同步资料仍保留在本机'}


@router.get('/connectors/boss-native/status')
def boss_native_status(batch_id: int | None = None, preflight: bool = False,
                       browser_mode: Literal['chrome_use', 'managed'] | None = None):
    from . import search_runs
    run = search_runs.latest_run(batch_id) if batch_id else None
    if run and search_runs.local_platform(run) != 'boss':
        run = None
    mode = run['preferences'].get('browser_mode', 'managed') if run else browser_mode or 'managed'
    result = {'connected': True, 'origin': 'native', 'connector': 'native', 'mode': 'read_only',
              'browser_mode': mode,
              'search_run': run, 'task': None, 'preferences': run['preferences'] if run else {}}
    try:
        result['capabilities'] = search_runs.runtime_call('capabilities', collector='boss')
        if run and run.get('task_id'):
            result['search_run'] = search_runs.refresh_run(run['id'])
            if result['search_run']['status'] in search_runs.ACTIVE:
                result['task'] = {'id': run['task_id'], 'mode': run['preferences'].get('collection_mode', 'collect'),
                                  'status': result['search_run']['status']}
        if preflight:
            result['readiness'] = search_runs.runtime_call('readiness', collector='boss', browser_mode=mode)
    except HTTPException as exc:
        result.update(connected=False, result_error=str(exc.detail))
        result.setdefault('capabilities', {'matched_results': False})
        if preflight:
            result['readiness'] = {'ok': False, 'checks': [], 'messages': [str(exc.detail)]}
    return result


class BossSearchPreview(BaseModel):
    batch_id: int = Field(gt=0)
    max_pages: int = Field(default=1, ge=1, le=3, strict=True)
    internship_only: bool = Field(default=True, strict=True)


class BossNativeSearchPreview(BossSearchPreview):
    max_jobs: int = Field(default=60, ge=1, le=60, strict=True)
    collection_mode: Literal['collect', 'discover'] = 'collect'
    browser_mode: Literal['chrome_use', 'managed'] | None = None


class BossNativeCollect(BossNativeSearchPreview):
    confirmed: Literal[True]


def _boss_search_preferences(batch_id: int, max_pages: int) -> dict:
    with connect() as conn:
        batch = _batch(conn, batch_id)
        if batch['platform'] != 'boss' or batch['status'] != 'open':
            raise HTTPException(409, '请先选择进行中的 BOSS 批次')
        keywords = list(dict.fromkeys(v.strip() for v in re.split(r'[,，;；\n]+', batch['keyword']) if v.strip()))
        cities = list(dict.fromkeys(v.strip() for v in re.split(r'[,，;；\n]+', batch['city']) if v.strip()))
    if not keywords or not cities:
        raise HTTPException(400, 'BOSS 采集批次必须填写搜索词和城市；多个值用逗号、分号或换行分隔')
    if len(keywords) > 30 or len(cities) > 30 or any(len(v) > 120 for v in keywords + cities):
        raise HTTPException(400, '搜索词和城市每项最多 120 字，分别最多 30 项')
    return {'platform_order': ['boss'], 'keywords': keywords, 'cities': cities,
            'allow_internship': True, 'recruitment_type': 'both', 'max_pages': {'boss': max_pages}}


def _boss_native_preferences(data: BossNativeSearchPreview) -> dict:
    from . import boss_runtime, search_runs
    preferences = _boss_search_preferences(data.batch_id, data.max_pages)
    mode = data.browser_mode
    if mode is None:
        prior = search_runs.latest_run(data.batch_id)
        mode = (prior['preferences'].get('browser_mode', 'managed')
                if prior and search_runs.local_platform(prior) == 'boss' else 'managed')
    preferences.update(connector='native', internship_only=data.internship_only,
                       recruitment_type='internship' if data.internship_only else 'both',
                       max_jobs=data.max_jobs, collection_mode=data.collection_mode, browser_mode=mode)
    try:
        boss_runtime.validate_preferences(preferences)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(503, external_collectors.safe_message(exc)) from exc
    return preferences


@router.post('/connectors/boss-native/search-preview')
def boss_native_preview(data: BossNativeSearchPreview):
    preferences = _boss_native_preferences(data)
    mode = preferences['browser_mode']
    return {'batch_id': data.batch_id, 'connector': 'native', 'preferences': preferences, 'changes': [],
            'requires_confirmation': True, 'settings_persist': False,
            'internship_only': data.internship_only, 'max_jobs': data.max_jobs,
            'collection_mode': data.collection_mode, 'browser_mode': mode, 'filter_changes': [],
            'warnings': [('将使用你已打开的 Chrome；如需登录或验证，请切到检索标签完成后继续。'
                          if mode == 'chrome_use' else '将打开本机浏览器；首次登录或验证码需要在该窗口中完成。')]}


@router.post('/connectors/boss-native/start-collect')
def boss_native_collect(data: BossNativeCollect):
    from . import search_runs
    return search_runs.start_search(data.batch_id, _boss_native_preferences(data),
                                    data.internship_only, platform='boss', connector='native')


def _boss_request(path, post):
    try:
        response = external_collectors.request_bosshunter(path, post=post)
    except external_collectors.ConnectorError as exc:
        raise HTTPException(503, external_collectors.safe_message(exc)) from exc
    if not isinstance(response, dict) or response.get('error'):
        raise HTTPException(409, external_collectors.upstream_error(response))
    return response


@router.post('/connectors/bosshunter/search-preview')
def boss_connector_search_preview(data: BossSearchPreview):
    from . import search_runs
    prior = search_runs.latest_run(data.batch_id)
    if prior and search_runs.local_platform(prior) == 'boss':
        return boss_native_preview(BossNativeSearchPreview(
            **data.model_dump(), max_jobs=prior['preferences'].get('max_jobs', 60),
            collection_mode=prior['preferences'].get('collection_mode', 'collect')))
    preferences = _boss_search_preferences(data.batch_id, data.max_pages)
    search_runs.capabilities()
    options = search_runs.request('/api/radar/search-options/preview', {'internship_only': data.internship_only})
    response = _boss_request('/api/agent/config/preview', {'preferences': preferences})
    return {'batch_id': data.batch_id, 'preferences': external_collectors.public_preferences(response.get('preferences', preferences)),
            'changes': external_collectors.public_changes(response.get('changes')), 'requires_confirmation': True,
            'settings_persist': True, 'internship_only': data.internship_only, 'filter_changes': options.get('changes', [])}


class ConfirmCollect(BaseModel):
    confirmed: Literal[True]
    batch_id: int | None = Field(default=None, gt=0)
    max_pages: int = Field(default=1, ge=1, le=3, strict=True)
    internship_only: bool = Field(default=True, strict=True)


@router.post('/connectors/bosshunter/start-collect')
def boss_connector_collect(data: ConfirmCollect):
    preferences = None
    if data.batch_id is not None:
        from .search_runs import start_search, latest_run, local_platform
        prior = latest_run(data.batch_id)
        if prior and local_platform(prior) == 'boss':
            return boss_native_collect(BossNativeCollect(
                **data.model_dump(), max_jobs=prior['preferences'].get('max_jobs', 60),
                collection_mode=prior['preferences'].get('collection_mode', 'collect')))
        preferences = _boss_search_preferences(data.batch_id, data.max_pages)
        return start_search(data.batch_id, preferences, data.internship_only)
    response = _boss_request('/api/agent/tasks', {'mode': 'collect', 'confirm': True})
    return {'started': True, 'batch_id': data.batch_id, 'preferences': preferences,
            'settings_persist': preferences is not None,
            'upstream': {'task': external_collectors.public_task(response.get('task'))}}


@router.post('/connectors/bosshunter/import')
def boss_connector_import(data: ConfirmExternalImport):
    with connect() as conn:
        batch = _batch(conn, data.batch_id)
        if batch['platform'] != 'boss' or batch['status'] != 'open':
            raise HTTPException(409, '请先选择进行中的 BOSS 批次')
        start = 0 if data.restart_from_first else batch['collector_offset']
    candidates = []
    rejected = 0
    offset = start
    try:
        while offset - start < data.limit:
            count = min(100, data.limit - (offset - start))
            response = external_collectors.request_bosshunter(f'/api/jobs?limit={count}&offset={offset}')
            if not isinstance(response, list):
                raise external_collectors.ConnectorError('BossHunter 岗位列表格式不兼容')
            if not response:
                break
            for raw in response:
                value = external_collectors.normalize_bosshunter_job(raw)
                if value is None:
                    rejected += 1
                else:
                    candidates.append(value)
            offset += len(response)
            if len(response) < count:
                break
    except external_collectors.ConnectorError as exc:
        raise HTTPException(503, str(exc)) from exc
    created = duplicate = incomplete = 0
    with connect() as conn:
        batch = _batch(conn, data.batch_id)
        if batch['status'] != 'open':
            raise HTTPException(409, '批次已暂停')
        for candidate in candidates:
            if _enqueue_from_adapter(conn, data.batch_id, candidate):
                created += 1
                if not candidate['jd_available']:
                    incomplete += 1
            else:
                duplicate += 1
        # Advance the persistent cursor in the same transaction as candidate
        # writes. Re-importing after a crash is safe and idempotent.
        if not data.restart_from_first and batch['collector_offset'] != start:
            raise HTTPException(409, '采集进度已被其他请求更新，请刷新后重试')
        conn.execute("UPDATE capture_batches SET collector_offset=?,updated_at=datetime('now') WHERE id=?",
                     (offset, data.batch_id))
    return {'created': created, 'already_exists': duplicate, 'rejected': rejected,
            'missing_jd': incomplete, 'scanned': offset-start, 'next_offset': offset,
            'restart_from_first': data.restart_from_first, 'review_required': True}


class WorkbenchText(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)


@router.post('/batches/{batch_id}/workbench-text')
def workbench_text_import(batch_id: int, data: WorkbenchText):
    try:
        items = external_collectors.split_workbench_text(data.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not items:
        raise HTTPException(422, '未能识别出带公司和岗位名称的候选记录')
    with connect() as conn:
        batch = _batch(conn, batch_id)
        if batch['status'] != 'open':
            raise HTTPException(409, '批次已暂停或完成')
        created = sum(_enqueue_from_adapter(conn, batch_id, item) for item in items)
        conn.execute("UPDATE capture_batches SET updated_at=datetime('now') WHERE id=?", (batch_id,))
    return {'created': created, 'already_exists': len(items)-created,
            'total': len(items), 'review_required': True}


class ShixisengSearchPreview(BossSearchPreview):
    internship_only: Literal[True] = True
    max_jobs: int = Field(default=60, ge=1, le=60, strict=True)
    collection_mode: Literal['collect', 'discover', 'hydrate'] = 'collect'
    force_refresh: bool = Field(default=True, strict=True)


class ShixisengCollect(ShixisengSearchPreview):
    confirmed: Literal[True]


def _shixiseng_search_preferences(batch_id: int, max_pages: int, max_jobs: int = 60, collection_mode: str = 'collect', force_refresh: bool = True) -> dict:
    with connect() as conn:
        batch = _batch(conn, batch_id)
        if batch['platform'] != 'shixiseng' or batch['status'] != 'open':
            raise HTTPException(409, '请先选择进行中的实习僧批次')
        keywords = list(dict.fromkeys(v.strip() for v in re.split(r'[,，;；\n]+', batch['keyword']) if v.strip()))
        cities = list(dict.fromkeys(v.strip() for v in re.split(r'[,，;；\n]+', batch['city']) if v.strip()))
    if not keywords or not cities:
        raise HTTPException(400, '实习僧采集批次必须填写搜索词和城市')
    if any(len(v) > 120 for v in keywords + cities) or len(keywords) * len(cities) > 9 or len(keywords) * len(cities) * max_pages > 12:
        raise HTTPException(400, '每项最多 120 字，最多 9 个搜索组合和 12 页；请减少关键词、城市或页数')
    preferences = {'platform_order': ['shixiseng'], 'keywords': keywords, 'cities': cities,
            'allow_internship': True, 'internship_only': True, 'max_jobs': max_jobs,
            'recruitment_type': 'internship', 'max_pages': {'shixiseng': max_pages},
            'collection_mode': collection_mode, 'force_refresh': True}
    from . import discovery, execution_budget
    with connect() as conn:
        if collection_mode == 'hydrate':
            preferences['candidates'] = discovery.pending_candidates(conn, batch_id)
            if not preferences['candidates']:
                raise HTTPException(409, '本批次没有未读详情；先检索列表或检查已保存结果')
        preferences = execution_budget.constrain(conn, batch_id, preferences)
    return preferences


@router.get('/connectors/shixiseng/status')
def shixiseng_connector_status(batch_id: int | None = None, preflight: bool = False):
    from . import search_runs
    run = search_runs.latest_run(batch_id) if batch_id else None
    if run and run.get('platform') != 'shixiseng':
        run = None
    result = {'connected': True, 'mode': 'read_only', 'task': None, 'search_run': run,
              'preferences': run['preferences'] if run else {}}
    try:
        result['capabilities'] = search_runs.runtime_call('capabilities')
        if run and run.get('task_id'):
            result['search_run'] = search_runs.refresh_run(run['id'])
            if result['search_run']['status'] in search_runs.ACTIVE:
                result['task'] = {'id': run['task_id'], 'mode': 'collect', 'status': result['search_run']['status']}
        if preflight:
            result['readiness'] = search_runs.runtime_call('readiness')
    except HTTPException as exc:
        result.update(connected=False, result_error=str(exc.detail))
        result.setdefault('capabilities', {'matched_results': False})
        if preflight:
            result['readiness'] = {'ok': False, 'checks': [], 'messages': [str(exc.detail)]}
    return result


@router.post('/connectors/shixiseng/search-preview')
def shixiseng_connector_preview(data: ShixisengSearchPreview):
    from . import search_runs
    preferences = _shixiseng_search_preferences(data.batch_id, data.max_pages, data.max_jobs, data.collection_mode, data.force_refresh)
    search_runs.runtime_call('capabilities')
    return {'batch_id': data.batch_id, 'preferences': preferences, 'changes': [],
            'requires_confirmation': True, 'settings_persist': False,
            'internship_only': True, 'max_jobs': preferences['max_jobs'], 'collection_mode': data.collection_mode,
            'filter_changes': [], 'cache_ttl_hours': 24,
            'warnings': ['每次重新读取原始岗位详情，不复用旧详情；未读取的候选保留在本地。']}


@router.post('/connectors/shixiseng/start-collect')
def shixiseng_connector_collect(data: ShixisengCollect):
    from . import search_runs
    preferences = _shixiseng_search_preferences(data.batch_id, data.max_pages, data.max_jobs, data.collection_mode, data.force_refresh)
    return search_runs.start_search(data.batch_id, preferences, True, platform='shixiseng')
