"""Opt-in adapters for *locally installed* existing job collectors.

Only a fixed loopback BossHunter service is contacted. No credentials, remote
hosts, anti-bot bypass, applicant messages or platform requests are made here.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from .company_profile import collected_evidence
from urllib.parse import urlsplit
from urllib.request import (build_opener, ProxyHandler, HTTPRedirectHandler, Request)

BOSSHUNTER_ORIGIN = 'http://127.0.0.1:8686'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ConnectorError(RuntimeError):
    def __init__(self, message, *, http_status=None):
        super().__init__(message)
        self.http_status = http_status


def safe_message(value) -> str:
    text = str(value or '')[:2000]
    text = re.sub(r'(?i)(bearer\s+)[^\s,;"\']+', r'\1[已隐藏]', text)
    text = re.sub(r'''(?i)((?:api[_-]?key|auth[_-]?token|password|cookie|authorization)["']?\s*[:=]\s*["']?)[^\s,;"']+''', r'\1[已隐藏]', text)
    return re.sub(r'\b(?:sk|sess)-[A-Za-z0-9_-]+', '[已隐藏]', text)


def upstream_error(data) -> str:
    if not isinstance(data, dict):
        return 'BossHunter 返回格式不兼容'
    parts = [safe_message(data.get(key)) for key in ('error', 'detail') if isinstance(data.get(key), str)]
    parts.extend(safe_message(value) for value in (data.get('messages') or []) if isinstance(value, str))
    for check in data.get('checks', []) if isinstance(data.get('checks'), list) else []:
        if isinstance(check, dict) and check.get('status') == 'error':
            parts.extend(safe_message(check.get(key)) for key in ('message', 'detail') if isinstance(check.get(key), str))
    return '；'.join(dict.fromkeys(parts))[:4000] or 'BossHunter 未能完成请求，请检查本机配置'


def public_preferences(data) -> dict:
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in ('keywords', 'cities', 'platform_order'):
        if isinstance(data.get(key), list):
            result[key] = [safe_message(v) for v in data[key][:30] if isinstance(v, str)]
    for key in ('recruitment_type', 'education'):
        if isinstance(data.get(key), str):
            result[key] = safe_message(data[key])
    if isinstance(data.get('allow_internship'), bool):
        result['allow_internship'] = data['allow_internship']
    if isinstance(data.get('max_pages'), dict):
        result['max_pages'] = {k: v for k, v in data['max_pages'].items() if k in ('boss', 'shixiseng', 'zhilian', '51job', 'liepin') and type(v) is int}
    return result


def public_task(data):
    if not isinstance(data, dict):
        return None
    return {key: safe_message(data.get(key)) if data.get(key) is not None else None
            for key in ('id', 'mode', 'status', 'error')}


def public_changes(data) -> list:
    if not isinstance(data, list):
        return []
    result = []
    for change in data[:100]:
        if not isinstance(change, dict):
            continue
        path = str(change.get('path') or '')
        if path.split('.')[-1] not in ('keywords', 'cities', 'target_cities', 'enabled', 'default_order', 'allow_internship', 'recruitment_type', 'max_pages'):
            continue
        item = {'path': safe_message(path)}
        for key in ('before', 'after'):
            value = change.get(key)
            if isinstance(value, list):
                item[key] = [safe_message(v) for v in value[:30] if isinstance(v, str)]
            elif value is None or type(value) in (int, bool):
                item[key] = value
            elif isinstance(value, str):
                item[key] = safe_message(value)
        result.append(item)
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ConnectorError('上游尝试重定向，已安全停止')


def request_bosshunter(path: str, *, post: dict | None = None):
    # Endpoint choices are hardcoded by our own code; never accept arbitrary URLs.
    if path not in ('/api/agent/state', '/api/agent/tools', '/api/agent/tasks',
                    '/api/agent/config/preview', '/api/agent/config/apply',
                    '/api/radar/capabilities', '/api/radar/search-options',
                    '/api/radar/search-options/preview', '/api/radar/search-options/apply',
                    '/api/radar/search/start',
                    '/api/workbench/preflight?mode=collect') and not re.fullmatch(
                        r'/api/radar/tasks/[A-Za-z0-9_-]{1,100}/results\?limit=\d{1,3}&offset=\d{1,9}', path
                    ) and not re.fullmatch(r'/api/workbench/task/[A-Za-z0-9_-]{1,100}/stop', path) and not (
        path.startswith('/api/jobs?') and path.removeprefix('/api/jobs?').replace('&','').replace('=','').isalnum()
    ):
        raise ConnectorError('未授权的上游接口')
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    data = json.dumps(post).encode('utf-8') if post is not None else None
    request = Request(BOSSHUNTER_ORIGIN + path, data=data, headers={'Content-Type':'application/json'} if data else {},
                      method='POST' if data is not None else 'GET')
    try:
        timeout = 30 if path in ('/api/radar/search/start', '/api/workbench/preflight?mode=collect') else 8
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ConnectorError('上游返回数据过大')
        return json.loads(body)
    except HTTPError as exc:
        try:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
            detail = upstream_error(json.loads(body)) if len(body) <= MAX_RESPONSE_BYTES else '上游返回数据过大'
        except (OSError, ValueError):
            detail = '请检查其运行状态及配置'
        raise ConnectorError('BossHunter：' + detail, http_status=exc.code) from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ConnectorError('无法访问本机 BossHunter：请检查其运行状态及配置') from exc


def normalize_bosshunter_job(job: dict) -> dict | None:
    if not isinstance(job, dict):
        return None
    platform = str(job.get('source_platform') or job.get('platform') or 'boss').strip().lower()
    if platform not in ('boss', 'boss_zhipin', 'zhipin'):
        return None
    raw_url = str(job.get('url') or '').strip()
    if raw_url.startswith('/job_detail/'):
        raw_url = 'https://www.zhipin.com' + raw_url
    split = urlsplit(raw_url)
    if split.scheme != 'https' or (split.hostname or '').lower() not in ('www.zhipin.com', 'zhipin.com') or split.username or split.password:
        return None
    title = str(job.get('title') or '').strip()[:180]
    company = str(job.get('company') or '').strip()[:140]
    jd = str(job.get('jd') or '').strip()
    if len(jd) > 100_000 or len(raw_url) > 2000:
        return None
    if not (title and company):
        return None
    return {
        'source_url': raw_url[:2000],
        'company_evidence': collected_evidence(job, raw_url),
        'raw_text': jd,
        'company': company,
        'title': title,
        'city': str(job.get('city') or '').strip()[:120],
        'salary': str(job.get('salary') or '').strip()[:200],
        'duration': str(job.get('duration') or '').strip()[:200],
        'onsite_days': str(job.get('onsite_days') or '').strip()[:200],
        'employment_type': str(job.get('employment_type') or '').strip()[:100],
        'eligibility': str(job.get('eligibility') or '').strip()[:1000],
        'posted_at': str(job.get('posted_at') or '').strip()[:100],
        'deadline': str(job.get('deadline') or '').strip()[:100],
        'observed_at': str(job.get('observed_at') or '')[:50],
        'reused_snapshot': job.get('reused_snapshot') is True,
        'last_detail_at': str(job.get('last_detail_at') or job.get('observed_at') or '')[:50],
        'jd_available': bool(jd) and job.get('jd_available') is not False,
    }


def split_workbench_text(text: str) -> list[dict]:
    """Parse the clipboard text format from internship-workbench/extension/collector.js.

    This does not trust or execute webpage markup; labels are merely draft hints.
    """
    if len(text) > 1_000_000:
        raise ValueError('文本超过 1MB')
    blocks = text.split('\n---\n')
    if len(blocks) > 100:
        raise ValueError('最多支持 100 个岗位')
    result = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        def label(name):
            return next((l.split('：',1)[1].strip() for l in lines if l.startswith(name+'：')), '')
        company, title, city = label('公司'), label('岗位'), label('城市')
        if not title or not company:
            continue
        # The extension outputs exactly one source URL as a standalone line.
        url = next((l.strip() for l in lines if l.strip().startswith(('https://','http://'))), '')
        if url:
            s = urlsplit(url)
            if s.scheme not in ('http','https') or not s.hostname or s.username or s.password:
                url = ''
        sep = next((i for i,v in enumerate(lines) if v.strip() == '岗位原文：'), -1)
        jd = '\n'.join(lines[sep+1:]).strip() if sep >= 0 else ''
        if len(jd) > 100_000 or len(url) > 2000:
            raise ValueError('JD 或来源链接超出上限；不会截断招聘原文')
        result.append({'company':company[:140], 'title':title[:180], 'city':city[:120],
                       'source_url':url, 'raw_text':jd})
    return result


def normalize_shixiseng_job(job: dict) -> dict | None:
    """Retain incomplete descriptions for manual review; accept only job pages."""
    if not isinstance(job, dict):
        return None
    raw_url = str(job.get('url') or '').strip()
    try:
        parsed = urlsplit(raw_url)
        valid = (parsed.scheme == 'https' and parsed.hostname == 'www.shixiseng.com'
                 and not parsed.username and not parsed.password and parsed.port in (None, 443)
                 and re.fullmatch(r'/intern/inn_[A-Za-z0-9_-]+', parsed.path))
    except ValueError:
        return None
    if not valid or len(raw_url) > 2000:
        return None
    title, company = str(job.get('title') or '').strip(), str(job.get('company') or '').strip()
    jd = str(job.get('jd') or '').strip()
    if not title or not company or len(jd) > 100_000 or re.search(r'[\ue000-\uf8ff\ufffd]', title + company):
        return None
    # Font obfuscation appears as private-use glyphs. Do not present unreadable
    # text as a complete JD; keep the original text for human inspection.
    missing = not jd or bool(re.search(r'[\ue000-\uf8ff\ufffd]', jd)) or job.get('jd_available') is False
    return {'source_url': raw_url, 'raw_text': jd, 'title': title[:180], 'company': company[:140],
            'company_evidence': collected_evidence(job, raw_url),
            **{key: str(job.get(key) or '').strip()[:200] for key in
               ('city', 'salary', 'duration', 'onsite_days', 'employment_type')},
            'eligibility': str(job.get('eligibility') or job.get('degree') or '')[:1000],
            'posted_at': str(job.get('posted_at') or '')[:100],
            'deadline': str(job.get('deadline') or '')[:100],
            'observed_at': str(job.get('observed_at') or '')[:50],
            'reused_snapshot': job.get('reused_snapshot') is True, 'last_detail_at': str(job.get('last_detail_at') or job.get('observed_at') or '')[:50], 'jd_available': not missing}
