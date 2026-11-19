"""Public company evidence and local headcount boundaries."""

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlsplit, parse_qsl, urlencode

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

Headcount = Annotated[int, Field(ge=1, le=1_000_000_000)]


class CompanyEvidenceFields(BaseModel):
    sector_source_url: str = Field(default='', max_length=2000)
    sector_observed_on: str = Field(default='', max_length=10)
    headcount_min: Annotated[int, Field(ge=0, le=1_000_000_000)] | None = None
    headcount_max: Headcount | None = None
    headcount_text: str = Field(default='', max_length=120)
    headcount_source_url: str = Field(default='', max_length=2000)
    headcount_observed_on: str = Field(default='', max_length=10)

    @field_validator('sector_source_url', 'headcount_source_url')
    @classmethod
    def validate_source(cls, value):
        value = value.strip()
        if value:
            url = urlsplit(value)
            if (url.scheme not in ('https', 'http') or not url.hostname
                    or url.username is not None or url.password is not None or re.search(r'\s', value)):
                raise ValueError('请填写不含账号凭据的公开网页链接')
        return value

    @field_validator('sector_observed_on', 'headcount_observed_on')
    @classmethod
    def validate_date(cls, value):
        if value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('记录日期须使用 YYYY-MM-DD')
        if value:
            date.fromisoformat(value)
        return value


def recorded_headcount(company):
    return company.get('headcount_min') is not None or company.get('headcount_max') is not None


def headcount_matches(company, lower=None, upper=None, include_missing=True):
    if lower is None and upper is None:
        return recorded_headcount(company) or include_missing
    if not recorded_headcount(company):
        return include_missing
    return ((upper is None or company.get('headcount_min') is None or company['headcount_min'] <= upper)
            and (lower is None or company.get('headcount_max') is None or company['headcount_max'] >= lower))


def scale_label(company):
    """Group by numeric bounds; keep the platform's original text on the record."""
    if not recorded_headcount(company):
        return '待录入'
    lower, upper = company.get('headcount_min'), company.get('headcount_max')
    if lower is not None and upper is not None:
        return f'{lower}–{upper}人' if lower != upper else f'{lower}人'
    return f'{lower}人以上' if lower is not None else f'{upper}人以下'


def normalize_evidence(changes, current=None):
    """Validate the merged facts and keep each citation tied to its value."""
    current = current or {}
    if 'sector' in changes:
        changes['sector'] = (changes['sector'] or '').strip()
        if changes['sector'] == '待核实':
            changes['sector'] = ''
    bounds = ('headcount_min', 'headcount_max')
    lower, upper = (changes.get(key, current.get(key)) for key in bounds)
    bounds_changed = any(key in changes and changes[key] != current.get(key) for key in bounds)
    if 'headcount_text' in changes:
        changes['headcount_text'] = changes['headcount_text'].strip()
    text_changed = 'headcount_text' in changes and changes['headcount_text'] != current.get('headcount_text', '')
    if bounds_changed and not text_changed:
        changes['headcount_text'] = ''
    if text_changed and changes['headcount_text']:
        if parsed := parse_headcount(changes['headcount_text']):
            if not bounds_changed:
                lower, upper, _ = parsed
                changes.update(headcount_min=lower, headcount_max=upper)
            elif (lower, upper) != parsed[:2]:
                raise HTTPException(422, '人数区间原文与填写的人数上下限不一致')
        elif not bounds_changed or lower is None and upper is None:
            raise HTTPException(422, '无法识别规模文字，请填写本次人数上下限，或改成“1000人”“800–1200人”等格式')
    if lower is not None and upper is not None and lower > upper:
        raise HTTPException(422, '企业人数下限不能大于上限')
    for prefix, fields, has_value in (
        ('sector', ('sector',), bool(changes.get('sector', current.get('sector')))),
        ('headcount', ('headcount_min', 'headcount_max', 'headcount_text'), lower is not None or upper is not None),
    ):
        source_key, date_key = prefix + '_source_url', prefix + '_observed_on'
        value_changed = any(key in changes and changes[key] != current.get(key) for key in fields)
        if not has_value or (value_changed and source_key not in changes):
            changes.update({source_key: '', date_key: ''})
        elif source_key in changes:
            if not changes[source_key]:
                changes[date_key] = ''
            elif (value_changed or changes[source_key] != current.get(source_key)) and not changes.get(date_key):
                changes[date_key] = date.today().isoformat()
        elif date_key in changes and not current.get(source_key):
            changes[date_key] = ''
    return changes


def parse_headcount(text):
    """Read exact platform ranges, preserving their original display text."""
    if not isinstance(text, str):
        return None
    value = text.strip()
    if not 0 < len(value) <= 120:
        return None
    normalized = re.sub(r'[\s,，]', '', unicodedata.normalize('NFKC', value))
    normalized = re.sub(r'^(?:公司规模|企业规模|人员规模|规模)[:：]?', '', normalized)
    number = r'(\d{1,9}(?:\.\d{1,4})?)'
    def people(amount, unit=''):
        result = Decimal(amount) * (10000 if unit else 1)
        return int(result) if result == int(result) else -1
    if match := re.fullmatch(number + r'(万?)[-–—~～至]' + number + r'(万?)人', normalized):
        a, unit_a, b, unit_b = match.groups()
        lower, upper = people(a, unit_a or unit_b), people(b, unit_b)
    elif match := re.fullmatch(number + r'(万?)人(?:以上|及以上|\+)', normalized):
        lower, upper = people(*match.groups()), None
    elif match := re.fullmatch(number + r'(万?)\+人', normalized):
        lower, upper = people(*match.groups()), None
    elif match := re.fullmatch(number + r'(万?)人(?:以下|及以下)', normalized):
        lower, upper = None, people(*match.groups())
    elif match := re.fullmatch(r'少于' + number + r'(万?)人', normalized):
        lower, upper = None, people(*match.groups()) - 1
    elif match := re.fullmatch(number + r'(万?)人', normalized):
        lower = upper = people(*match.groups())
    else:
        return None
    if (lower is not None and not 0 <= lower <= 1_000_000_000
            or upper is not None and not 1 <= upper <= 1_000_000_000
            or lower is not None and upper is not None and lower > upper):
        return None
    return lower, upper, value


def valid_company_source(value, platform, *, company_only=False):
    """Only canonical public pages on the same platform can supply a collected fact."""
    if not isinstance(value, str) or len(value) > 2000:
        return ''
    try:
        parsed = urlsplit(value)
        host = 'www.zhipin.com' if platform == 'boss' else 'www.shixiseng.com'
        if (parsed.scheme != 'https' or parsed.hostname not in {host, host.removeprefix('www.')}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            return ''
        company = r'/gongsi/[A-Za-z0-9_~-]{1,128}\.html' if platform == 'boss' else r'/com/com_[A-Za-z0-9_-]{1,128}'
        job = r'/job_detail/[A-Za-z0-9_~-]{1,128}\.html' if platform == 'boss' else r'/intern/inn_[A-Za-z0-9_-]{1,128}'
        if re.fullmatch(company, parsed.path) or not company_only and re.fullmatch(job, parsed.path):
            return 'https://' + host + parsed.path
        if not company_only and platform == 'boss' and parsed.path in {'/web/geek/jobs', '/web/geek/job'}:
            query = urlencode([(key, val) for key, val in parse_qsl(parsed.query) if key in {'query', 'city', 'page', 'jobType'}])
            return 'https://' + host + parsed.path + ('?' + query if query else '')
    except ValueError:
        pass
    return ''


def collected_evidence(job, source_url):
    """Accept only explicit fields reported from a recognized job detail page."""
    evidence = {}
    platform = 'boss' if urlsplit(source_url).hostname in {'zhipin.com', 'www.zhipin.com'} else 'shixiseng'
    def citation(field):
        source = valid_company_source(job.get(field + '_source_url') or source_url, platform)
        observed = str(job.get(field + '_observed_at') or job.get('last_detail_at') or job.get('observed_at') or '')[:10]
        try:
            observed = date.fromisoformat(observed).isoformat()
        except ValueError:
            observed = date.today().isoformat()
        return source, observed
    sector = job.get('company_sector')
    sector_source, sector_date = citation('company_sector')
    if (isinstance(sector, str) and 0 < len(sector.strip()) <= 180
            and not re.search(r'[\x00-\x1f\ue000-\uf8ff\ufffd]', sector.strip())
            and sector.strip() not in {'暂无', '未知', '不详', '其他', '不限', '-'} and sector_source):
        evidence.update(sector=sector.strip(), sector_source_url=sector_source, sector_observed_on=sector_date)
    headcount_source, headcount_date = citation('company_headcount')
    if (size := parse_headcount(job.get('company_headcount'))) and headcount_source:
        lower, upper, text = size
        evidence.update(headcount_min=lower, headcount_max=upper, headcount_text=text,
                        headcount_source_url=headcount_source, headcount_observed_on=headcount_date)
    return {'company': str(job.get('company') or '').strip()[:140], **evidence} if evidence else {}


def fill_company_evidence(conn, company_id, draft, confirmed_name):
    """Fill missing public facts on confirmation, preserving existing evidence."""
    evidence = draft.get('company_evidence')
    if not evidence or evidence.get('company', '').casefold() != confirmed_name.casefold():
        return
    current = conn.execute('SELECT * FROM companies WHERE id=?', (company_id,)).fetchone()
    changes = {}
    if evidence.get('sector_source_url') and not current['sector_source_url']:
        if not current['sector'] or current['sector'] == evidence['sector']:
            changes.update({key: evidence[key] for key in ('sector', 'sector_source_url', 'sector_observed_on')})
    if evidence.get('headcount_source_url') and not current['headcount_source_url']:
        bounds = ('headcount_min', 'headcount_max')
        if all(current[key] is None for key in bounds) or all(current[key] == evidence[key] for key in bounds):
            changes.update({key: evidence[key] for key in (*bounds, 'headcount_text', 'headcount_source_url', 'headcount_observed_on')})
    if changes:
        columns = ','.join(f'{key}=?' for key in changes)
        conn.execute(f'UPDATE companies SET {columns} WHERE id=?', (*changes.values(), company_id))
