"""Offline company lists, explicit identity mappings and previewed imports."""
import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import connect

router = APIRouter(prefix='/api/company-lists', tags=['Company lists'])
ROOT = Path(__file__).resolve().parents[1]
BUILTIN_KEY = 'synthetic-employers-v1'
MAX_ROWS = 5000


def name_key(value):
    # Legal suffixes, brands and group relationships are never removed for auto matching.
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


@lru_cache(maxsize=1)
def builtin_data():
    data = json.loads((ROOT / 'static/datasets/synthetic-employers-v1.json').read_text('utf-8'))
    if len(data['entries']) != 100 or [r['position'] for r in data['entries']] != list(range(1, 101)):
        raise RuntimeError('内置企业名单文件不完整，请重新安装应用')
    return data


def ensure_builtin(conn, restore=False):
    existing = conn.execute('SELECT id FROM company_lists WHERE builtin_key=?', (BUILTIN_KEY,)).fetchone()
    state = conn.execute('SELECT state FROM company_list_defaults WHERE key=?', (BUILTIN_KEY,)).fetchone()
    if existing:
        return existing['id']
    if state and not restore:
        return None  # A deliberate deletion survives upgrades and restarts.
    data = builtin_data()
    ident = conn.execute('''INSERT INTO company_lists(name,builtin_key,publisher,source_url,report_url,published_on,description)
        VALUES(?,?,?,?,?,?,?)''', (data['name'], BUILTIN_KEY, data['publisher'], data['source_url'],
                                  data['report_url'], data['published_on'], data['description'])).lastrowid
    for row in data['entries']:
        insert_entry(conn, ident, row['position'], {**row, **{k: data[k] for k in
                     ('publisher', 'source_url', 'report_url', 'published_on')}}, origin='builtin')
    conn.execute('INSERT OR REPLACE INTO company_list_defaults VALUES(?,?)', (BUILTIN_KEY, 'installed'))
    return ident


def insert_entry(conn, list_id, position, row, origin='user'):
    return conn.execute('''INSERT INTO company_list_entries(list_id,position,company_name,name_key,aliases_json,
        business_brands,region,publisher,source_url,report_url,published_on,origin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
        (list_id, position, row['company_name'], name_key(row['company_name']), json.dumps(row.get('aliases', []), ensure_ascii=False),
         *(row.get(k, '') for k in ('business_brands', 'region', 'publisher', 'source_url', 'report_url', 'published_on')), origin)).lastrowid


def required_list(conn, ident):
    row = conn.execute('SELECT * FROM company_lists WHERE id=?', (ident,)).fetchone()
    if not row:
        raise HTTPException(404, '名单已不存在，请返回企业名单重新选择')
    return dict(row)


def company_context(conn):
    companies = [dict(r) for r in conn.execute('''SELECT c.id,c.name,count(j.id) AS job_count FROM companies c
        LEFT JOIN jobs j ON j.company_id=c.id GROUP BY c.id ORDER BY c.id''')]
    index = {}
    for row in companies:
        index.setdefault(name_key(row['name']), []).append(row)
    mappings = {row['name_key']: [] for row in conn.execute('SELECT name_key FROM company_name_mappings')}
    by_id = {r['id']: r for r in companies}
    for row in conn.execute('SELECT * FROM company_name_links'):
        if row['company_id'] in by_id:
            mappings[row['name_key']].append(by_id[row['company_id']])
    prepared = [(company, *company_terms(company['name'])) for company in companies]
    return companies, index, mappings, prepared


def entry_matches(entry, context):
    _, exact, mappings, _ = context
    key = entry['name_key']
    if key in mappings:
        return mappings[key], 'remembered' if mappings[key] else 'cancelled'
    matches = exact.get(key, [])
    return (matches, 'exact') if len(matches) == 1 else ([], 'unmatched')


def candidate_core(value):
    text = name_key(value)
    return re.sub(r'(?:股份有限公司|有限责任公司|集团有限公司|有限公司|股份公司|公司|集团|控股)$', '', text)


@lru_cache(maxsize=20000)
def company_terms(name):
    return name_key(name), candidate_core(name)


def candidate_matches(entry, context):
    key, core = entry['name_key'], candidate_core(entry['company_name'])
    aliases = [name_key(x) for x in json.loads(entry['aliases_json'])]
    brands = name_key(entry['business_brands'])
    for company, other, short in context[3]:
        reason, score = '', 0
        if other == key:
            reason, score = ('名称一致；存在多个对应或此前已取消关联，需自行选择', 4)
        elif other in aliases or short in aliases:
            reason, score = ('名单别名与企业名称相近；别名只作为候选线索', 3)
        elif len(short) >= 2 and short not in {'中国', '网络', '科技', '信息', '集团'} and short in brands:
            reason, score = ('主要业务和品牌字段包含该名称；品牌不等于招聘主体', 2)
        elif min(len(core), len(short)) >= 2 and (core in short or short in core):
            reason, score = ('企业名称部分相同或去掉公司后缀后相同，仅供选择', 2)
        elif min(len(core), len(short)) >= 4:
            matcher = SequenceMatcher(None, core, short)
            if matcher.real_quick_ratio() >= .72 and matcher.quick_ratio() >= .72 and matcher.ratio() >= .72:
                reason, score = ('企业名称相近，仅供选择', 1)
        if reason:
            yield {**company, 'reason': reason, '_score': score}


def entry_candidates(entry, context):
    found = list(candidate_matches(entry, context))
    found.sort(key=lambda r: (-r['_score'], r['id']))
    return [{k: v for k, v in r.items() if k != '_score'} for r in found[:8]]


def list_summary(conn, row, context=None):
    row = dict(row)
    context = context or company_context(conn)
    entries = conn.execute('SELECT * FROM company_list_entries WHERE list_id=? ORDER BY position', (row['id'],)).fetchall()
    ids, matched = set(), 0
    for entry in entries:
        matches, _ = entry_matches(entry, context)
        matched += bool(matches)
        ids.update(r['id'] for r in matches)
    return {**row, 'builtin': bool(row['builtin_key']), 'customized': bool(row['customized']),
            'entry_count': len(entries), 'matched_count': matched, 'company_ids': sorted(ids),
            'job_count': sum(c['job_count'] for c in context[0] if c['id'] in ids)}


def matched_company_ids(conn, ident):
    return list_summary(conn, required_list(conn, ident))['company_ids']


class ListName(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)

    @field_validator('name')
    @classmethod
    def strip_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('请填写名单名称')
        return value


class ListImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=120)
    target_id: int | None = Field(default=None, ge=1)
    format: Literal['text', 'csv'] = 'text'
    content: str = Field(max_length=2_000_000)


class ImportCommit(ListImport):
    preview_token: str = Field(min_length=64, max_length=64)


class MatchSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company_ids: list[int] = Field(max_length=100)


COLUMNS = {
    'company_name': ['企业名称', '公司名称', '企业', '公司', 'company_name', 'company', 'name'],
    'aliases': ['别名', 'aliases', 'alias'], 'business_brands': ['主要业务和品牌', '主要业务及品牌', '业务品牌', 'business_brands'],
    'region': ['地区', '所属地', '省份', 'region'], 'publisher': ['来源机构', '发布机构', 'publisher'],
    'source_url': ['来源链接', '来源', 'source_url'], 'report_url': ['报告链接', 'report_url'],
    'published_on': ['发布日期', 'published_on'],
}
HEADER_MAP = {name_key(label): key for key, labels in COLUMNS.items() for label in labels}


def validate_entry(row):
    row = {k: str(v).strip() for k, v in row.items()}
    if not row.get('company_name'):
        raise ValueError('企业名称不能为空')
    for key, maximum in [('company_name', 180), ('aliases', 4000), ('business_brands', 4000),
                          ('region', 120), ('publisher', 180), ('source_url', 2000), ('report_url', 2000)]:
        if len(row.get(key, '')) > maximum:
            raise ValueError(f'{COLUMNS[key][0]}超过 {maximum} 字')
    if re.search(r'[\x00-\x1f]', row['company_name']):
        raise ValueError('企业名称不能包含换行或控制字符')
    for key in ('source_url', 'report_url'):
        if row.get(key):
            try:
                url = urlsplit(row[key])
                if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or re.search(r'\s', row[key]):
                    raise ValueError()
            except ValueError:
                raise ValueError(COLUMNS[key][0] + '须为完整的 http 或 https 链接') from None
    if row.get('published_on'):
        value = row['published_on'].replace('/', '-').replace('年', '-').replace('月', '-').removesuffix('日')
        try:
            row['published_on'] = date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError('发布日期请使用 YYYY-MM-DD') from None
    aliases = list(dict.fromkeys(x.strip() for x in re.split(r'[,，;；、\n]', row.get('aliases', '')) if x.strip()))
    if len(aliases) > 30 or any(len(x) > 180 for x in aliases):
        raise ValueError('别名最多 30 个，每个最多 180 字')
    row['aliases'] = aliases
    return row


def parse_rows(data):
    text = data.content.lstrip('\ufeff')
    warnings, result = [], []
    if data.format == 'text':
        for line, raw in enumerate(text.splitlines(), 1):
            if not raw.strip():
                continue
            cells = [cell.strip() for cell in raw.split('\t')]
            if any(x.strip() for x in cells[1:]):
                result.append((line, {'company_name': cells[0]}, ('企业名称为空；' if not cells[0] else '') + '请只复制企业名称一列；多列资料请使用 CSV'))
            elif not result and name_key(cells[0]) in {name_key(x) for x in COLUMNS['company_name']}:
                continue
            else:
                result.append((line, {'company_name': cells[0]}, ''))
    else:
        try:
            reader = csv.reader(io.StringIO(text, newline=''), strict=True)
            raw_rows = [(reader.line_num, r) for r in reader]
        except csv.Error as error:
            return [], [], ['CSV 格式错误，请检查引号或重新导出：' + str(error)]
        raw_rows = [(line, row) for line, row in raw_rows if any(x.strip() for x in row)]
        if not raw_rows:
            return [], [], ['请先粘贴企业名称或选择包含企业的文件']
        header = [HEADER_MAP.get(name_key(x)) for x in raw_rows[0][1]]
        if 'company_name' not in header:
            if all(len(row) == 1 for _, row in raw_rows) and not any(header):
                result = [(line, {'company_name': row[0]}, '') for line, row in raw_rows]
            else:
                return [], [], ['CSV 第一行需包含“企业名称”列；只填这一列即可导入']
        elif len([x for x in header if x]) != len(set(x for x in header if x)):
            return [], [], ['CSV 包含重复的列名，请每种资料只保留一列']
        else:
            unknown = [label for label, key in zip(raw_rows[0][1], header) if not key and label.strip()]
            if unknown:
                warnings.append('以下列不参与导入：' + '、'.join(unknown))
            for line, row in raw_rows[1:]:
                if len(row) != len(header):
                    result.append((line, {}, '列数与表头不一致，请检查逗号和引号'))
                else:
                    result.append((line, {key: value for key, value in zip(header, row) if key}, ''))
    if len(result) > MAX_ROWS:
        return [], warnings, [f'每次最多导入 {MAX_ROWS} 家，请分批导入']
    return result, warnings, []


def prepare_import(conn, data):
    target = required_list(conn, data.target_id) if data.target_id else None
    rows, warnings, errors = parse_rows(data)
    if not target and not data.name.strip():
        errors.append('请填写名单名称')
    seen = {r['name_key']: '已有名单' for r in conn.execute('SELECT name_key FROM company_list_entries WHERE list_id=?', (data.target_id,))}
    preview, added, duplicates = [], [], 0
    for line, raw, issue in rows:
        try:
            if issue:
                raise ValueError(issue)
            row = validate_entry(raw)
            key = name_key(row['company_name'])
            if key in seen:
                duplicates += 1
                preview.append({'line': line, **row, 'status': 'duplicate', 'message': '名称重复，保留首次内容（' + seen[key] + '），本次跳过'})
            else:
                seen[key] = f'第 {line} 行'
                added.append(row)
                preview.append({'line': line, **row, 'status': 'new', 'message': '可新增'})
        except ValueError as error:
            message = f'第 {line} 行：{error}'
            errors.append(message)
            preview.append({'line': line, 'company_name': raw.get('company_name', ''), 'status': 'error', 'message': str(error)})
    if not rows and not errors:
        errors.append('请先粘贴企业名称或选择包含企业的文件')
    if not added and not errors:
        errors.append('没有可新增的企业；重复项不会改动原名单')
    payload = data.model_dump(exclude={'preview_token'})
    token = hashlib.sha256(json.dumps({'input': payload, 'target_revision': target['revision'] if target else None},
                                     ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {'valid': not errors, 'preview_token': token, 'rows': preview, 'errors': errors, 'warnings': warnings,
            'new_count': len(added), 'duplicate_count': duplicates, 'total': len(rows)}, added


@router.get('')
def lists():
    with connect() as conn:
        context = company_context(conn)
        result = [list_summary(conn, row, context) for row in conn.execute('SELECT * FROM company_lists ORDER BY builtin_key IS NULL,id')]
        builtin = next((row['id'] for row in result if row['builtin_key'] == BUILTIN_KEY), None)
        return {'lists': result, 'builtin_id': builtin}


@router.get('/builtin.csv')
def original_csv():
    return FileResponse(ROOT / 'static/templates/synthetic-employers-v1.csv', media_type='text/csv; charset=utf-8',
                        filename='synthetic-employers-v1.csv')


@router.get('/template.csv')
def blank_template():
    return FileResponse(ROOT / 'static/templates/company-list-template.csv', media_type='text/csv; charset=utf-8',
                        filename='company-list-template.csv')


@router.post('/builtin/restore')
def restore_builtin():
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        return {'id': ensure_builtin(conn, restore=True)}


@router.post('/import/preview')
def import_preview(data: ListImport):
    with connect() as conn:
        return prepare_import(conn, data)[0]


@router.post('/import/commit', status_code=201)
def import_commit(data: ImportCommit):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        preview, rows = prepare_import(conn, data)
        if preview['preview_token'] != data.preview_token:
            raise HTTPException(409, '内容或目标名单已变化，请重新预览后导入')
        if not preview['valid']:
            raise HTTPException(422, '；'.join(preview['errors'][:5]) + '。本次未保存任何企业，请修改后重新预览')
        ident = data.target_id or conn.execute('INSERT INTO company_lists(name) VALUES(?)', (data.name.strip(),)).lastrowid
        position = conn.execute('SELECT coalesce(max(position),0) FROM company_list_entries WHERE list_id=?', (ident,)).fetchone()[0]
        for offset, row in enumerate(rows, 1):
            insert_entry(conn, ident, position + offset, row)
        conn.execute("UPDATE company_lists SET revision=revision+1,customized=CASE WHEN builtin_key IS NOT NULL THEN 1 ELSE customized END,updated_at=datetime('now') WHERE id=?", (ident,))
        return {'id': ident, 'added': len(rows), 'duplicates_skipped': preview['duplicate_count']}


@router.post('', status_code=201)
def create_list(data: ListName):
    with connect() as conn:
        return {'id': conn.execute('INSERT INTO company_lists(name) VALUES(?)', (data.name,)).lastrowid}


@router.get('/{list_id}')
def list_detail(list_id: int, q: str = '', status: Literal['', 'matched', 'pending', 'unmatched', 'cancelled'] = '',
                page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    with connect() as conn:
        row = required_list(conn, list_id)
        context, entries = company_context(conn), []
        counts = {'matched': 0, 'pending': 0, 'unmatched': 0, 'cancelled': 0}
        for item in conn.execute('SELECT * FROM company_list_entries WHERE list_id=? ORDER BY position', (list_id,)):
            item = dict(item)
            matches, method = entry_matches(item, context)
            state = ('matched' if matches else 'cancelled' if method == 'cancelled'
                     else 'pending' if next(candidate_matches(item, context), None) else 'unmatched')
            counts[state] += 1
            item.update(aliases=json.loads(item.pop('aliases_json')), matches=matches, match_method=method, candidates=[],
                        match_status=state,
                        job_count=sum(c['job_count'] for c in matches))
            query = q.strip().casefold()
            if status and state != status or query and query not in ' '.join([item['company_name'], item['business_brands'], item['region'], *item['aliases']]).casefold():
                continue
            entries.append(item)
        total = len(entries)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        entries = entries[(page - 1) * page_size:page * page_size]
        for item in entries:
            item['candidates'] = entry_candidates({**item, 'aliases_json': json.dumps(item['aliases'])}, context) if item['match_status'] == 'pending' else []
        return {**list_summary(conn, row, context), 'entries': entries, 'page': page, 'page_size': page_size,
                'filtered_count': total, 'pages': pages,
                'order_note': builtin_data()['order_note'] if row['builtin_key'] else '按名单录入顺序展示',
                'pending_count': counts['pending'], 'unmatched_count': counts['unmatched'], 'cancelled_count': counts['cancelled']}


@router.patch('/{list_id}')
def rename_list(list_id: int, data: ListName):
    with connect() as conn:
        required_list(conn, list_id)
        conn.execute("UPDATE company_lists SET name=?,revision=revision+1,updated_at=datetime('now') WHERE id=?", (data.name, list_id))
        return {'ok': True}


@router.delete('/{list_id}')
def delete_list(list_id: int):
    with connect() as conn:
        row = required_list(conn, list_id)
        if row['builtin_key']:
            conn.execute('INSERT OR REPLACE INTO company_list_defaults VALUES(?,?)', (BUILTIN_KEY, 'deleted'))
        conn.execute('DELETE FROM company_lists WHERE id=?', (list_id,))
        return {'ok': True}


@router.delete('/{list_id}/entries/{entry_id}')
def remove_entry(list_id: int, entry_id: int):
    with connect() as conn:
        required_list(conn, list_id)
        if not conn.execute('DELETE FROM company_list_entries WHERE list_id=? AND id=?', (list_id, entry_id)).rowcount:
            raise HTTPException(404, '名单条目已不存在，请刷新名单')
        conn.execute("UPDATE company_lists SET revision=revision+1,customized=CASE WHEN builtin_key IS NOT NULL THEN 1 ELSE customized END,updated_at=datetime('now') WHERE id=?", (list_id,))
        return {'ok': True}


@router.put('/{list_id}/entries/{entry_id}/matches')
def save_matches(list_id: int, entry_id: int, data: MatchSelection):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        required_list(conn, list_id)
        entry = conn.execute('SELECT * FROM company_list_entries WHERE list_id=? AND id=?', (list_id, entry_id)).fetchone()
        if not entry:
            raise HTTPException(404, '名单条目已不存在，请刷新名单')
        ids = data.company_ids
        if len(set(ids)) != len(ids) or any(isinstance(i, bool) or i < 1 for i in ids):
            raise HTTPException(422, '请选择有效且不重复的企业')
        for ident in ids:
            if not conn.execute('SELECT 1 FROM companies WHERE id=?', (ident,)).fetchone():
                raise HTTPException(409, '所选企业已不存在，请刷新后重试')
        conn.execute('INSERT OR REPLACE INTO company_name_mappings VALUES(?,?)', (entry['name_key'], entry['company_name']))
        conn.executemany('INSERT INTO company_name_links VALUES(?,?)', [(entry['name_key'], ident) for ident in ids])
        return {'ok': True, 'company_ids': ids}
