"""Immutable personal material snapshots and direct experience import."""
import hashlib
import base64
import binascii
import json
import re
from pathlib import PureWindowsPath
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .career_workspace import EvidenceData, create_evidence, encoded
from .db import connect

router = APIRouter(prefix='/api/materials')
MAX_ATTACHMENT = 8 * 1024 * 1024


class MaterialIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=160)
    kind: Literal['resume', 'project', 'other'] = 'resume'
    title: str = Field(default='', max_length=160)
    text: str = Field(default='', max_length=50000)
    material_id: int | None = Field(default=None, gt=0)
    copy_attachment_from: int | None = Field(default=None, gt=0)


def save_version(conn, data, filename='', content=b'', media_type='text/plain'):
    if data.copy_attachment_from and not content:
        original = conn.execute('SELECT * FROM material_versions WHERE id=?', (data.copy_attachment_from,)).fetchone()
        if original is None or original['material_id'] != data.material_id:
            raise HTTPException(422, '沿用的附件不属于当前材料')
        if original['filename']:
            filename, content, media_type = original['filename'], bytes(original['content']), original['media_type']
    if not data.name.strip():
        raise HTTPException(422, '请填写材料名称')
    if not data.text.strip() and not content:
        raise HTTPException(422, '请粘贴材料文本或选择实际文件')
    if data.material_id:
        material = conn.execute('SELECT * FROM personal_materials WHERE id=?', (data.material_id,)).fetchone()
        if not material:
            raise HTTPException(422, '原材料不存在')
        material_id = data.material_id
    else:
        material_id = conn.execute('INSERT INTO personal_materials(name,kind) VALUES(?,?)',
                                   (data.name.strip(), data.kind)).lastrowid
    number = conn.execute('SELECT count(*) FROM material_versions WHERE material_id=?', (material_id,)).fetchone()[0] + 1
    payload = content or data.text.encode('utf-8')
    ident = conn.execute('''INSERT INTO material_versions(material_id,title,text_snapshot,filename,media_type,content,sha256)
        VALUES(?,?,?,?,?,?,?)''', (material_id, data.title.strip() or f'{data.name.strip()} · v{number}',
        data.text, filename, media_type, payload, hashlib.sha256(payload).hexdigest())).lastrowid
    return {'id': ident, 'material_id': material_id}


@router.get('')
def materials():
    with connect() as conn:
        return [dict(row) for row in conn.execute('''SELECT v.id,v.material_id,v.title,v.filename,v.media_type,
            v.sha256,v.created_at,m.name,m.kind,length(v.content) AS size FROM material_versions v
            JOIN personal_materials m ON m.id=v.material_id ORDER BY v.id DESC''')]


@router.post('/versions', status_code=201)
def text_version(data: MaterialIn):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        return save_version(conn, data)


def validate_attachment(filename, content):
    filename = PureWindowsPath(filename).name
    extension = PureWindowsPath(filename).suffix.lower()
    formats = {'.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
               '.txt': 'text/plain', '.md': 'text/markdown'}
    if extension not in formats or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, '支持 PDF、DOCX、UTF-8 TXT 和 Markdown 文件')
    if not content:
        raise HTTPException(422, '附件为空，请选择文件或仅保存文字')
    if len(content) > MAX_ATTACHMENT:
        raise HTTPException(413, '材料文件最多 8 MB')
    text = ''
    if extension in ('.txt', '.md'):
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise HTTPException(422, '文本材料请使用 UTF-8 编码') from exc
        if len(text) > 50000:
            raise HTTPException(422, '文本材料最多 50000 字')
    elif extension == '.pdf' and not content.startswith(b'%PDF-'):
        raise HTTPException(422, '文件内容不是 PDF')
    elif extension == '.docx' and not content.startswith(b'PK'):
        raise HTTPException(422, '文件内容不是 DOCX')
    return filename, formats[extension], text


class MaterialAttachment(MaterialIn):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1, max_length=4 * ((MAX_ATTACHMENT + 2) // 3))


@router.post('/versions/attachment', status_code=201)
def attachment_version(data: MaterialAttachment):
    try:
        content = base64.b64decode(data.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, '附件编码不完整，请重新选择文件') from exc
    filename, media_type, extracted = validate_attachment(data.filename, content)
    if not data.text:
        data = data.model_copy(update={'text': extracted})
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        return save_version(conn, data, filename, content, media_type)


@router.post('/versions/file', status_code=201)
async def file_version(request: Request, name: str = Query(min_length=1, max_length=160),
                       filename: str = Query(min_length=1, max_length=200),
                       kind: Literal['resume', 'project', 'other'] = 'resume',
                       material_id: int | None = Query(default=None, gt=0)):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_ATTACHMENT:
            raise HTTPException(413, '材料文件最多 8 MB')
    filename, media_type, text = validate_attachment(filename, bytes(content))
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        return save_version(conn, MaterialIn(name=name, kind=kind, text=text, material_id=material_id),
                            filename, bytes(content), media_type)


@router.get('/versions/{version_id}')
def material_version(version_id: int):
    with connect() as conn:
        row = conn.execute('''SELECT v.id,v.material_id,v.title,v.text_snapshot,v.filename,v.media_type,v.sha256,
            v.created_at,m.name,m.kind FROM material_versions v JOIN personal_materials m ON m.id=v.material_id
            WHERE v.id=?''', (version_id,)).fetchone()
        if not row:
            raise HTTPException(404, '材料版本不存在')
        return dict(row)


@router.get('/versions/{version_id}/download')
def material_download(version_id: int):
    with connect() as conn:
        row = conn.execute('SELECT * FROM material_versions WHERE id=?', (version_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '材料版本不存在')
        name = row['filename'] or (row['title'] + '.txt')
        return Response(bytes(row['content']), media_type=row['media_type'], headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name, safe=''),
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


class CandidatePreview(BaseModel):
    text: str = Field(min_length=1, max_length=50000)


@router.post('/candidates/preview')
def candidate_preview(data: CandidatePreview):
    # Split the supplied material; no capability or achievement is inferred.
    parts = [part.strip() for part in re.split(r'\n\s*\n|\n(?=[•●\-\*]\s)', data.text) if part.strip()]
    candidates = []
    for part in parts:
        for start in range(0, len(part), 3500):
            text = part[start:start + 3500]
            candidates.append({'source_quote': text, 'description': text, 'skill': '', 'skills': [],
                               'review_status': 'documented', 'evidence_type': 'self_report'})
    if len(candidates) > 30:
        raise HTTPException(422, '候选经历超过 30 段，请缩小本次材料范围')
    return {'candidates': candidates, 'method': 'source_paragraphs', 'inferred_capabilities': False}


class Candidate(EvidenceData):
    source_quote: str = Field(min_length=1, max_length=4000)


class MaterialImport(MaterialIn):
    import_id: UUID
    candidates: list[Candidate] = Field(min_length=1, max_length=30)


@router.post('/import-evidence')
def import_material_evidence(data: MaterialImport):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT response_json FROM material_imports WHERE import_id=?', (str(data.import_id),)).fetchone()
        if old:
            return json.loads(old['response_json'])
        if any(candidate.source_quote not in data.text for candidate in data.candidates):
            raise HTTPException(422, '候选经历必须保留可定位的材料原文')
        version = save_version(conn, data)
        evidence_ids = []
        for candidate in data.candidates:
            values = candidate.model_dump(exclude={'source_quote'})
            values['material_version_id'] = version['id']
            ident = create_evidence(conn, EvidenceData(**values))
            evidence_ids.append(ident)
            conn.execute('INSERT INTO evidence_material_quotes(evidence_id,material_version_id,source_quote) VALUES(?,?,?)',
                         (ident, version['id'], candidate.source_quote))
        result = {**version, 'evidence_ids': evidence_ids}
        conn.execute('INSERT INTO material_imports(import_id,response_json) VALUES(?,?)',
                     (str(data.import_id), encoded(result)))
        return result
