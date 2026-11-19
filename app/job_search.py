"""Homepage entry into the existing, owned capture workflow.

The form never creates a second queue: search runs, results and review decisions
remain in capture's tables. GET is read-only; an explicit refresh POST ingests
available results for the search the user started.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import boss_runtime, capture, search_runs, shixiseng_runtime
from .db import connect

router = APIRouter(prefix='/api/job-search', tags=['Homepage job search'])
_start_lock = threading.Lock()
LABELS = {'boss': 'BOSS 直聘', 'shixiseng': '实习僧'}


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['boss', 'shixiseng'] = 'boss'
    keywords: list[str] = Field(min_length=1, max_length=100)
    city: str = Field(default='北京', min_length=1, max_length=60)
    # One or more cities. ``city`` stays accepted for older clients; when both are
    # sent, ``cities`` wins. Every keyword is searched in every city.
    cities: list[str] = Field(default_factory=list, max_length=20)
    max_pages: int = Field(default=1, ge=1, le=3, strict=True)
    max_jobs: int = Field(default=30, ge=1, le=60, strict=True)
    browser_mode: Literal['chrome_use', 'managed'] = 'managed'

    @field_validator('keywords')
    @classmethod
    def normalize_keywords(cls, values):
        result, seen = [], set()
        for value in values:
            keyword = value.strip()
            if not keyword:
                continue
            if len(keyword) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in keyword):
                raise ValueError('每个关键词最多 80 字，请将不同关键词分行填写')
            key = keyword.casefold()
            if key not in seen:
                seen.add(key)
                result.append(keyword)
        if not 1 <= len(result) <= 9:
            raise ValueError('每次请填写 1–9 个关键词；重复项会自动合并')
        return result

    @field_validator('city')
    @classmethod
    def normalize_city(cls, value):
        city = value.strip()
        if not city or any(char in ',，;；' or ord(char) < 32 or ord(char) == 127 for char in city):
            raise ValueError('请填写一个城市，例如北京')
        return city

    @field_validator('cities')
    @classmethod
    def normalize_cities(cls, values):
        result, seen = [], set()
        for value in values:
            city = value.strip()
            if not city:
                continue
            if len(city) > 60 or any(char in ',，;；、' or ord(char) < 32 or ord(char) == 127 for char in city):
                raise ValueError('每个城市单独选择或填写，例如北京；最多 60 字')
            if city not in seen:
                seen.add(city)
                result.append(city)
        return result

    @model_validator(mode='after')
    def validate_scope(self):
        if not self.cities:
            self.cities = [self.city]
        self.city = self.cities[0]
        if len(self.cities) > 9:
            raise ValueError('每次最多选择 9 个城市')
        if len(','.join(self.cities)) > 100:
            raise ValueError('所选城市名称合计过长，请分两次检索')
        combinations = len(self.keywords) * len(self.cities)
        if combinations > 9:
            raise ValueError(f'关键词 × 城市共 {combinations} 组，每次最多 9 组，请减少关键词或城市')
        if combinations * self.max_pages > 12:
            raise ValueError('每次最多检索 12 页列表（关键词 × 城市 × 每词页数），请减少关键词、城市或每词页数')
        return self

    @property
    def list_pages(self):
        return len(self.keywords) * len(self.cities) * self.max_pages

    def preferences(self):
        return {'keywords': self.keywords, 'cities': self.cities,
                'platform_order': [self.platform], 'max_pages': {self.platform: self.max_pages},
                'max_jobs': self.max_jobs, 'max_list_attempts': self.list_pages,
                'collection_mode': 'collect', 'force_refresh': True,
                'allow_internship': True, 'internship_only': True,
                'recruitment_type': 'internship',
                **({'browser_mode': self.browser_mode} if self.platform == 'boss' else {})}


class StartInput(SearchInput):
    confirmed: Literal[True]
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,64}$')


class ConfirmAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: Literal[True]


def _preferences(data: SearchInput):
    preferences = data.preferences()
    if data.platform == 'boss':
        try:
            boss_runtime.validate_preferences(preferences)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, 'BOSS 采集组件不完整，请更新完整桌面应用') from exc
    return preferences


def _readiness(data: SearchInput):
    return (boss_runtime.readiness(browser_mode=data.browser_mode) if data.platform == 'boss'
            else shixiseng_runtime.readiness())


@router.post('/preview')
def preview_search(data: SearchInput):
    preferences = _preferences(data)
    return {'preferences': preferences,
            'summary': {'keyword_count': len(data.keywords), 'city': '、'.join(data.cities),
                        'cities': data.cities, 'platform_label': LABELS[data.platform],
                        'list_pages': data.list_pages, 'max_jobs': data.max_jobs},
            'warnings': [], 'ready': _readiness(data)}


@router.get('/cities')
def search_cities():
    """Read-only city suggestions: common choices plus BOSS's supported city table."""
    try:
        boss = list(boss_runtime.supported_cities())
    except (OSError, RuntimeError, ValueError):
        boss = []
    return {'common': ['北京', '上海', '深圳', '杭州', '广州', '成都', '南京', '武汉', '西安', '苏州'],
            'boss': [city for city in boss if city != '全国']}


def _home_run(row):
    run = search_runs.public_run(row)
    return run if run and run['preferences'].get('entrypoint') == 'home' else None


def _request_run(request_id):
    with connect() as conn:
        rows = conn.execute('SELECT * FROM capture_search_runs WHERE instr(preferences_json, ?) > 0 ORDER BY id DESC',
                            (request_id,)).fetchall()
    for row in rows:
        run = _home_run(row)
        if run and run['preferences'].get('request_id') == request_id:
            return run
    return None


def _home_runs():
    with connect() as conn:
        rows = conn.execute('SELECT * FROM capture_search_runs ORDER BY id DESC').fetchall()
    return [run for row in rows if (run := _home_run(row))]


def _latest_home_run():
    runs = _home_runs()
    active = next((run for run in runs if run['status'] in search_runs.ACTIVE), None)
    pending = next((run for run in runs if run['sync_offset'] < run['total']), None)
    return active or pending or (runs[0] if runs else None)


def _receive_results(run):
    if run.get('task_id') and run['sync_offset'] < run['total']:
        response = search_runs.sync_results(run['id'], search_runs.SyncResults(confirmed=True, limit=500))
        return response['search_run']
    return run


def _owned_run(search_id):
    run = search_runs.stored_run(search_id)
    if run['preferences'].get('entrypoint') != 'home':
        raise HTTPException(404, '这不是首页发起的检索，请在采集收件箱查看')
    return run


def _result(run, error=''):
    if run is None:
        return {'request_id': None, 'batch_id': None, 'search_run': None,
                'batch': None, 'items': [], 'has_more': False, 'error': error}
    records = capture.list_items(run['batch_id'], run['id'])
    items = []
    for item in records[:30]:
        draft = item['draft']
        items.append({'id': item['id'], 'title': draft.get('title', ''),
                      'company': draft.get('company', ''), 'city': draft.get('city', ''),
                      'source_url': item['source_url'], 'review_status': item['status'],
                      'jd_available': bool(item['raw_text'].strip()) and draft.get('jd_available') is not False,
                      'known_job_id': item['known_job_id'], 'source_state': item['source_state']})
    return {'request_id': run['preferences'].get('request_id'), 'batch_id': run['batch_id'],
            'search_run': run, 'batch': capture.get_batch(run['batch_id']),
            'items': items, 'has_more': len(records) > len(items), 'error': error}


@router.get('/current')
def current_search():
    return _result(_latest_home_run())


@router.post('/start')
def start_home_search(data: StartInput):
    preferences = _preferences(data)
    fingerprint = hashlib.sha256(json.dumps(
        {'platform': data.platform, **preferences}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with _start_lock:
        previous = _request_run(data.request_id)
        if previous:
            if previous['preferences'].get('request_fingerprint') != fingerprint:
                raise HTTPException(409, '该请求的检索条件已改变，请重新开始检索')
            return _result(previous)
        # Do not hide a finished run before its results reach the inbox. This
        # also recovers older unfinished synchronizations after a restart.
        for current in _home_runs():
            if current['status'] not in search_runs.ACTIVE and current['sync_offset'] >= current['total']:
                continue
            current = search_runs.refresh_run(current['id'])
            if current['status'] in search_runs.ACTIVE:
                raise HTTPException(409, '已有检索正在进行，请在首页查看进度或先停止')
            _receive_results(current)
        search_runs.ensure_idle(data.platform)
        ready = _readiness(data)
        if ready.get('ok') is not True:
            raise HTTPException(503, '；'.join(ready.get('messages') or ['采集环境尚未就绪']))
        batch = capture.BatchIn(
            label=(LABELS[data.platform] + ' · ' + '、'.join(data.cities) + ' · ' + '、'.join(data.keywords))[:160],
            platform=data.platform, keyword='\n'.join(data.keywords), city=','.join(data.cities))
        preferences.update(entrypoint='home', request_id=data.request_id, request_fingerprint=fingerprint)
        result = search_runs.start_search(None, preferences, True, platform=data.platform,
                                          connector='native' if data.platform == 'boss' else None, new_batch=batch)
        return _result(result['search_run'])


@router.post('/{search_id}/refresh')
def refresh_home_search(search_id: int, body: ConfirmAction):
    run = _owned_run(search_id)
    error = ''
    try:
        run = _receive_results(search_runs.refresh_run(search_id))
    except HTTPException as exc:
        run = search_runs.stored_run(search_id)
        error = str(exc.detail)
    return _result(run, error)


@router.post('/{search_id}/stop')
def stop_home_search(search_id: int, body: ConfirmAction):
    _owned_run(search_id)
    search_runs.stop_search(search_id, search_runs.ConfirmStop(confirmed=True))
    return refresh_home_search(search_id, body)


@router.post('/{search_id}/resume')
def resume_home_search(search_id: int, body: ConfirmAction):
    _owned_run(search_id)
    search_runs.resume_search(search_id, search_runs.ConfirmStop(confirmed=True))
    return refresh_home_search(search_id, body)
