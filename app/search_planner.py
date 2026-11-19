"""Editable keyword families and bounded, explicit search preparation.

The planner does not execute collectors or claim their strict budget support.
"""
import csv
import hashlib
import io
import json
import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .db import connect

router = APIRouter(prefix='/api/planner', tags=['planner'])
RULE_FIELDS = ('global_jd_features', 'global_qualification', 'global_risk')
ROUTES = {'A': 'Enterprise AI', 'B': 'Agent / AI DevTools', 'C': 'Eval / Data Strategy',
          'D': 'FDE / Applied AI', 'E': 'Physical AI', 'F': '技术产品与风险对冲', 'X': '高门槛方向观察'}


def clean(value, limit=160):
    if not isinstance(value, str):
        raise ValueError('文本字段必须是字符串')
    value = value.strip()
    if len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f'文本须为单行且不超过 {limit} 字符')
    return value


def terms(value):
    if isinstance(value, str):
        value = re.split(r'[、;；\n]', value)
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError('每类词最多 100 个，使用列表或顿号分隔')
    return list(dict.fromkeys(clean(item) for item in value if clean(item)))


class Family(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    route: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    priority: Literal['P0', 'P1', 'P2', 'P3'] = 'P2'
    representative: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    jd_features: list[str] = Field(default_factory=list, max_length=100)
    qualification: list[str] = Field(default_factory=list, max_length=100)
    risk: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @field_validator('route', 'name', 'representative')
    @classmethod
    def text(cls, value):
        value = clean(value)
        if not value:
            raise ValueError('字段不能为空')
        return value

    @field_validator('representative')
    @classmethod
    def singular_query(cls, value):
        if re.search(r'[,，;；]', value):
            raise ValueError('代表词不能包含搜索分隔符')
        return value

    @field_validator('aliases', 'jd_features', 'qualification', 'risk', mode='before')
    @classmethod
    def word_list(cls, value):
        return terms(value)


class ImportText(BaseModel):
    format: Literal['auto', 'json', 'csv', 'markdown'] = 'auto'
    text: str = Field(min_length=1, max_length=300000)
    name: str = Field(default='', max_length=160)


class LibraryImport(BaseModel):
    families: list[Family] = Field(max_length=500)
    expected_version: int = Field(ge=0)
    global_jd_features: list[str] = Field(default_factory=list, max_length=100)
    global_qualification: list[str] = Field(default_factory=list, max_length=100)
    global_risk: list[str] = Field(default_factory=list, max_length=100)

    @field_validator(*RULE_FIELDS, mode='before')
    @classmethod
    def rule_list(cls, value):
        return terms(value)


class FamilyEdit(BaseModel):
    family: Family
    expected_version: int = Field(ge=0)


class PlanRequest(BaseModel):
    family_ids: list[str] = Field(min_length=1, max_length=500)
    cities: list[str] = Field(min_length=1, max_length=10)
    platforms: list[Literal['boss', 'shixiseng']] = Field(min_length=1, max_length=2)
    list_page_budget: int = Field(default=20, ge=1, le=200)
    detail_attempt_budget: int = Field(default=30, ge=1, le=600)
    time_budget_minutes: int = Field(default=30, ge=1, le=240)
    pages_per_query: int = Field(default=1, ge=1, le=3)
    expected_version: int | None = Field(default=None, ge=0)

    @field_validator('family_ids', 'cities', mode='before')
    @classmethod
    def unique_text(cls, value):
        if not isinstance(value, list):
            raise ValueError('请使用列表')
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError('列表不能为空白项')
        if any(item.strip() in {'不限', '全部', '远程', '不限城市', '任意', '不限地点', '所有', '全部城市', '全国各地'} for item in value):
            raise ValueError('请填写具体城市；不限范围请使用连接器支持的“全国”，远程不是城市')
        if any(re.search(r'[,，;；]', item) for item in value):
            raise ValueError('城市或岗位族须为单项，不能包含搜索分隔符')
        return list(dict.fromkeys(clean(item, 64) for item in value))

    @field_validator('platforms')
    @classmethod
    def unique_platforms(cls, value):
        return list(dict.fromkeys(value))


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def library(conn):
    row = conn.execute('SELECT * FROM planner_library WHERE id=1').fetchone()
    return {'version': row['version'], 'families': [json.loads(r['family_json']) for r in
            conn.execute('SELECT family_json FROM planner_families ORDER BY id')],
            **{key: json.loads(row['rules_json']).get(key, []) for key in RULE_FIELDS}}


@router.get('/library')
def get_library():
    with connect() as conn:
        return library(conn)


def validate_families(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise ValueError('导入应包含 1 至 500 个岗位族')
    families = [Family.model_validate(row).model_dump() for row in rows]
    if len({f['id'] for f in families}) != len(families):
        raise ValueError('岗位族 ID 重复；请先修改预览')
    return families


def markdown_import(text):
    rows, rules, priority, headers = [], {key: [] for key in RULE_FIELDS}, 'P2', []
    for line in text.splitlines():
        if line.startswith('#'):
            match = re.search(r'\bP[0-3]\b', line)
            if match:
                priority = match.group()
            headers = []
        if not line.strip().startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if all(re.fullmatch(r'[:\-\s]+', cell) for cell in cells):
            continue
        if any('岗位分类' in cell or '岗位名称检索词' in cell or cell in ('识别维度', '处理方式') for cell in cells):
            headers = cells
            continue
        if not headers or len(cells) != len(headers):
            continue
        fields = dict(zip(headers, cells))
        search_key = next((key for key in headers if '岗位名称检索词' in key), None)
        if search_key:
            name = fields.get('岗位分类', '')
            source_id = fields.get('编号', '')
            match = re.match(r'^([A-Z]\d{2})\s+(.+)$', name)
            if match:
                source_id, name = match.groups()
            if not source_id or not name:
                raise ValueError('岗位表缺少编号或分类名称')
            words = terms(fields[search_key])
            if not words:
                raise ValueError(f'{source_id} 缺少搜索词')
            jd_key = next((key for key in headers if key.startswith('JD ') or key == '重点识别'), None)
            rows.append({'id': source_id, 'route': ROUTES.get(source_id[0], '待确认路线'), 'name': name,
                         'priority': priority, 'representative': words[0], 'aliases': words[1:],
                         'jd_features': terms(fields[jd_key]) if jd_key else []})
        elif '识别维度' in fields:
            rules['global_jd_features'].extend(terms(fields.get('高价值关键词', '')))
        elif '处理方式' in fields:
            condition = fields.get('关键词或条件', '')
            target = 'global_qualification' if fields['处理方式'] == '硬排除' else 'global_risk'
            if condition:
                rules[target].append(condition)
    return rows, rules


@router.post('/import-preview')
def import_preview(body: ImportText):
    try:
        text = body.text.lstrip('\ufeff').strip()
        fmt = body.format
        if fmt == 'auto':
            fmt = 'json' if text.startswith(('[', '{')) else 'markdown' if '|' in text else 'csv'
        rules = {key: [] for key in RULE_FIELDS}
        if fmt == 'json':
            data = json.loads(text)
            rows = data.get('families') if isinstance(data, dict) else data
            if isinstance(data, dict):
                rules = {key: terms(data.get(key, [])) for key in RULE_FIELDS}
        elif fmt == 'csv':
            rows = list(csv.DictReader(io.StringIO(text)))
            for row in rows:
                if any(key not in Family.model_fields or value is None for key, value in row.items()):
                    raise ValueError('CSV 含未知列、缺失单元格或未正确引用的逗号；请核对列名和行长度')
                if 'enabled' in row:
                    token = row['enabled'].strip().lower()
                    if token not in ('true', 'false', '1', '0', ''):
                        raise ValueError('enabled 须为 true/false 或 1/0')
                    row['enabled'] = token not in ('false', '0')
        else:
            rows, rules = markdown_import(text)
        families = validate_families(rows)
        rules = {key: terms(value) for key, value in rules.items()}
    except (ValueError, TypeError, csv.Error) as exc:
        raise HTTPException(422, f'词库格式错误：{str(exc)[:500]}') from exc
    counts = {'families': len(families), 'search_terms': len({word for f in families for word in
              [f['representative'], *f['aliases']]}), 'jd_features': sum(len(f['jd_features']) for f in families) + len(rules['global_jd_features']),
              'qualification': sum(len(f['qualification']) for f in families) + len(rules['global_qualification']),
              'risk': sum(len(f['risk']) for f in families) + len(rules['global_risk'])}
    warnings = ['仅导入实际表格数据；代表词默认取该行首个搜索词，请在保存后核对。',
                '规则独立保存，风险词不作为全局硬排除；首轮不自动搜索全部别名。']
    if fmt == 'markdown':
        warnings.append('路线按岗位编号前缀分组，可编辑；正文描述及未提供的下载文件不计入导入。')
        if '主要用途' in text:
            warnings.append('主要用途列是方向说明，未作为 JD 识别规则导入；请保留原文供参考。')
    return {'families': families, **rules, 'counts': counts, 'warnings': warnings, 'format': fmt}


def lock_version(conn, expected):
    conn.execute('BEGIN IMMEDIATE')
    if conn.execute('SELECT version FROM planner_library WHERE id=1').fetchone()['version'] != expected:
        raise HTTPException(409, '词库已被修改，请刷新后重新确认')


@router.post('/library/import')
def save_import(body: LibraryImport):
    try:
        families = validate_families([f.model_dump() for f in body.families])
    except ValueError as exc:
        raise HTTPException(422, str(exc)[:500]) from exc
    with connect() as conn:
        lock_version(conn, body.expected_version)
        existing = {row['id'] for row in conn.execute('SELECT id FROM planner_families')}
        if len(existing | {family['id'] for family in families}) > 500:
            raise HTTPException(422, '词库最多保存 500 个岗位族，请先删除不需要的岗位族')
        for family in families:
            conn.execute('INSERT INTO planner_families(id,family_json) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET family_json=excluded.family_json,updated_at=datetime(\'now\')', (family['id'], dump(family)))
        rules = {key: library(conn)[key] for key in RULE_FIELDS}
        rules.update({key: getattr(body, key) for key in RULE_FIELDS if key in body.model_fields_set})
        conn.execute('UPDATE planner_library SET version=version+1,rules_json=?,updated_at=datetime(\'now\') WHERE id=1',
                     (dump(rules),))
        return library(conn)


@router.put('/families/{family_id}')
def edit_family(family_id: str, body: FamilyEdit):
    if family_id != body.family.id:
        raise HTTPException(422, '不能通过编辑更改岗位族 ID')
    with connect() as conn:
        lock_version(conn, body.expected_version)
        if not conn.execute('SELECT 1 FROM planner_families WHERE id=?', (family_id,)).fetchone():
            raise HTTPException(404, '岗位族不存在')
        conn.execute('UPDATE planner_families SET family_json=?,updated_at=datetime(\'now\') WHERE id=?', (dump(body.family.model_dump()), family_id))
        conn.execute('UPDATE planner_library SET version=version+1,updated_at=datetime(\'now\') WHERE id=1')
        return library(conn)


@router.delete('/families/{family_id}')
def delete_family(family_id: str, expected_version: int):
    with connect() as conn:
        lock_version(conn, expected_version)
        if not conn.execute('DELETE FROM planner_families WHERE id=?', (family_id,)).rowcount:
            raise HTTPException(404, '岗位族不存在')
        conn.execute('UPDATE planner_library SET version=version+1,updated_at=datetime(\'now\') WHERE id=1')
        return library(conn)


def preview_plan(body, current):
    if body.expected_version is not None and body.expected_version != current['version']:
        raise HTTPException(409, '词库已变更，请重新预览计划')
    by_id = {family['id']: family for family in current['families']}
    if any(fid not in by_id or not by_id[fid]['enabled'] for fid in body.family_ids):
        raise HTTPException(422, '所选岗位族不存在或已停用')
    families = sorted((by_id[fid] for fid in body.family_ids), key=lambda f: (f['priority'], f['id']))
    if '全国' in body.cities and 'boss' in body.platforms:
        raise HTTPException(422, 'BOSS 计划请填写具体城市；目前全国范围仅支持实习僧')
    if len(families) * len(body.cities) * len(body.platforms) > 2000:
        raise HTTPException(422, '单个计划最多 2000 个代表词组合，请缩小岗位族或城市范围')
    candidates = {}
    # Round-robin cities/platforms before adding depth; no synonym expansion in P0-A.
    for city in body.cities:
        for platform in body.platforms:
            for family in families:
                key = (family['representative'], city, platform)
                if key not in candidates:
                    candidates[key] = {'keyword': key[0], 'city': city, 'platform': platform, 'family_ids': []}
                candidates[key]['family_ids'].append(family['id'])
    available = min(len(candidates), body.list_page_budget, body.detail_attempt_budget)
    actions, uncovered = [], []
    detail_left, page_left = body.detail_attempt_budget, body.list_page_budget
    for index, candidate in enumerate(candidates.values()):
        if index >= available:
            uncovered.append({**candidate, 'reason': '本轮发现/详情分配额度不足，未准备此组合'})
            continue
        slots = available - index
        pages = min(body.pages_per_query, max(1, page_left - (slots - 1)))
        details = min(60, max(1, detail_left // slots))
        action_id = hashlib.sha256(dump(candidate).encode()).hexdigest()[:24]
        actions.append({**candidate, 'id': action_id, 'status': 'pending', 'list_pages': pages,
                        'detail_attempts': details, 'batch_id': None, 'collection_status': None, 'latest_search_run_id': None,
                        'collection_preferences': {'keywords': [candidate['keyword']], 'cities': [candidate['city']],
                                                   'max_pages': pages, 'max_jobs': details}})
        detail_left -= details
        page_left -= pages
    return {'library_version': current['version'], 'families': families, 'request': body.model_dump(),
            'actions': actions, 'uncovered': uncovered,
            'remaining_aliases': [{'family_id': f['id'], 'aliases': f['aliases']} for f in families],
            'budgets': {'list_pages': body.list_page_budget, 'detail_attempts': body.detail_attempt_budget,
                        'time_minutes': body.time_budget_minutes, 'allocated_list_pages': sum(a['list_pages'] for a in actions),
                        'allocated_detail_attempts': sum(a['detail_attempts'] for a in actions)},
            'capabilities': {'automatic_execution': False, 'strict_global_budget': body.platforms == ['shixiseng'],
                             'boss': {'strict_detail_attempts': False}, 'shixiseng': {'strict_detail_attempts': True}},
            'warnings': ['实习僧按计划限制每项查询的累计列表与详情尝试；BOSS 额度仍仅用于规划。',
                         '实习僧累计执行时间用尽后不再启动新动作；已发出的访问按有限超时结束。',
                         '计划仍需逐条确认；未覆盖组合与别名待探索，尚无跨轮自动轮转。']}


@router.post('/plans/preview')
def plan_preview(body: PlanRequest):
    return preview_plan(body, get_library())


@router.post('/plans')
def create_plan(body: PlanRequest):
    if body.expected_version is None:
        raise HTTPException(422, '保存计划须提供 expected_version，确认预览所用词库版本')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        snapshot = preview_plan(body, library(conn))
        plan_id = conn.execute('INSERT INTO planner_plans(snapshot_json) VALUES(?)', (dump(snapshot),)).lastrowid
        for action in snapshot['actions']:
            conn.execute('INSERT INTO planner_actions(plan_id,action_id,action_json) VALUES(?,?,?)',
                         (plan_id, action['id'], dump(action)))
        return stored_plan(conn, plan_id)


def stored_plan(conn, plan_id):
    row = conn.execute('SELECT * FROM planner_plans WHERE id=?', (plan_id,)).fetchone()
    if not row:
        raise HTTPException(404, '计划不存在')
    result = {'id': row['id'], 'created_at': row['created_at'], **json.loads(row['snapshot_json'])}
    states = {r['action_id']: dict(r) for r in conn.execute('SELECT action_id,status,batch_id FROM planner_actions WHERE plan_id=?', (plan_id,))}
    run_rows = conn.execute('''SELECT r.id,r.batch_id,r.status FROM capture_search_runs r
      JOIN planner_actions a ON a.batch_id=r.batch_id WHERE a.plan_id=?
      AND r.id=(SELECT max(newer.id) FROM capture_search_runs newer WHERE newer.batch_id=r.batch_id)''', (plan_id,)).fetchall()
    runs = {row['batch_id']: row for row in run_rows}
    for action in result['actions']:
        action.update({'status': states[action['id']]['status'], 'batch_id': states[action['id']]['batch_id']})
        run = runs.get(action['batch_id'])
        action['collection_status'] = run['status'] if run else None
        action['latest_search_run_id'] = run['id'] if run else None
        if run and action['platform'] == 'shixiseng':
            from .discovery import receipt_costs
            costs = {'list_attempts': 0, 'detail_attempts': 0, 'cache_hits': 0, 'unknown_attempts': 0}
            for previous in conn.execute('SELECT id,platform_state_json FROM capture_search_runs WHERE batch_id=?', (action['batch_id'],)).fetchall():
                previous_state = json.loads(previous['platform_state_json'])
                if previous_state.get('ledger_reported') is not True and previous_state.get('no_access') is not True:
                    action['costs_unverified'] = True
                for key, count in receipt_costs(conn, previous['id']).items():
                    costs[key] = costs.get(key, 0) + count
            action['runtime_costs'] = costs
    result['execution_capabilities'] = {'shixiseng_plan_allowances': True, 'boss_strict_costs': False, 'automatic_execution': False}
    result['runtime_warnings'] = ['实习僧按已保存计划的每项额度累计计数；未知访问保留预留额度。BOSS 暂不支持严格成本约束。', '实习僧已发现候选可补齐详情，未执行的列表页仍需另行规划；计划不会自动连续执行。']
    return result


@router.get('/plans')
def list_plans():
    with connect() as conn:
        return {'plans': [stored_plan(conn, row['id']) for row in conn.execute('SELECT id FROM planner_plans ORDER BY id DESC LIMIT 50').fetchall()]}


@router.get('/plans/{plan_id}')
def get_plan(plan_id: int):
    with connect() as conn:
        return stored_plan(conn, plan_id)


@router.post('/plans/{plan_id}/actions/{action_id}/prepare')
def prepare_action(plan_id: int, action_id: str):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        plan = stored_plan(conn, plan_id)
        row = conn.execute('SELECT * FROM planner_actions WHERE plan_id=? AND action_id=?', (plan_id, action_id)).fetchone()
        if not row:
            raise HTTPException(404, '计划中没有此搜索动作')
        action = json.loads(row['action_json'])
        batch_id = row['batch_id']
        if batch_id is None:
            label = f"计划#{plan_id} · {action['keyword']} · {action['city']}"[:160]
            batch_id = conn.execute('INSERT INTO capture_batches(label,platform,keyword,city) VALUES(?,?,?,?)',
                                    (label, action['platform'], action['keyword'], action['city'])).lastrowid
            conn.execute("UPDATE planner_actions SET status='prepared',batch_id=? WHERE plan_id=? AND action_id=?", (batch_id, plan_id, action_id))
        action.update({'status': 'prepared', 'batch_id': batch_id})
        return {'plan_id': plan_id, 'batch_id': batch_id, 'action': action, 'warnings': plan['warnings']}
