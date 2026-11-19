"""User-reviewed evidence, finding decisions and preparation outcomes."""
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import connect

router = APIRouter(prefix='/api')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class EvidenceData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    skill: str = Field(default='', max_length=120)
    skills: list[str] = Field(default_factory=list, max_length=30)
    proficiency: Literal['learning', 'basic', 'working', 'strong'] = 'working'
    evidence_type: Literal['self_report', 'project', 'internship', 'course', 'other'] = 'self_report'
    description: str = Field(default='', max_length=4000)
    source_url: str = Field(default='', max_length=2000)
    review_status: Literal['needs_check', 'documented'] = 'documented'
    material_version_id: int | None = Field(default=None, gt=0)

    @field_validator('skill')
    @classmethod
    def skill_name(cls, value):
        return value.strip()

    @field_validator('skills')
    @classmethod
    def skill_names(cls, values):
        result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 120 for value in result):
            raise ValueError('每项能力名称最多 120 字')
        return result

    @model_validator(mode='after')
    def has_content(self):
        if not self.skill and self.skills:
            self.skill = self.skills[0]
        if not (self.skill or self.description.strip() or self.source_url.strip() or self.material_version_id):
            raise ValueError('请填写能力或经历描述，标签可以留空')
        return self


def evidence_record(row):
    item = dict(row)
    item['skills'] = json.loads(item.get('skills_json') or '[]') or ([item['skill']] if item['skill'] else [])
    return item


def create_evidence(conn, data):
    if data.material_version_id and not conn.execute(
            'SELECT 1 FROM material_versions WHERE id=?', (data.material_version_id,)).fetchone():
        raise HTTPException(422, '材料版本不存在')
    values = data.model_dump(exclude={'skills'})
    values['skills_json'] = encoded(list(dict.fromkeys(value for value in [data.skill, *data.skills] if value)))
    return conn.execute('INSERT INTO profile_evidence(' + ','.join(values) + ') VALUES(' +
                        ','.join('?' for _ in values) + ')', tuple(values.values())).lastrowid


class EvidenceEdit(EvidenceData):
    expected_revision: int = Field(gt=0)


@router.patch('/evidence/{evidence_id}')
def edit_evidence(evidence_id: int, request: EvidenceEdit):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT * FROM profile_evidence WHERE id=?', (evidence_id,)).fetchone()
        if old is None:
            raise HTTPException(404, '个人经历不存在')
        if old['revision'] != request.expected_revision:
            raise HTTPException(409, '这条参考资料已在其他窗口修改，请保留本次内容并重新打开核对')
        data = request.model_dump(exclude={'expected_revision', 'skills'})
        if request.material_version_id and not conn.execute('SELECT 1 FROM material_versions WHERE id=?',
                                                            (request.material_version_id,)).fetchone():
            raise HTTPException(422, '材料版本不存在')
        data['skills_json'] = encoded(list(dict.fromkeys(value for value in [request.skill, *request.skills] if value)))
        old_values = dict(old)
        old_values['skills_json'] = encoded(evidence_record(old)['skills'])
        if all(old_values[key] == value for key, value in data.items()):
            return {'id': evidence_id, 'revision': old['revision'], 'changed': False}
        conn.execute('INSERT INTO evidence_revisions(evidence_id,revision,snapshot_json) VALUES(?,?,?)',
                     (evidence_id, old['revision'], encoded(evidence_record(old))))
        conn.execute('UPDATE profile_evidence SET ' + ','.join(key + '=?' for key in data) +
                     ',revision=revision+1 WHERE id=?', (*data.values(), evidence_id))
        # Legacy exports did not contain multi-skill metadata; an explicit edit still
        # invalidates every current finding that cites this record.
        from .analysis import finding_record
        for finding in conn.execute("SELECT * FROM analysis_findings WHERE review_state='current'").fetchall():
            item = finding_record(finding)
            item['original_assessments'] = item['assessments']
            overrides = {value['requirement_id']: value for value in json.loads(finding['assessment_overrides_json'])}
            item['assessments'] = [overrides.get(value['requirement_id'], value) for value in item['assessments']]
            if evidence_id in linked_evidence_ids(item):
                conn.execute("UPDATE analysis_findings SET review_state='needs_review',revision=revision+1 WHERE id=?", (finding['id'],))
        return {'id': evidence_id, 'revision': old['revision'] + 1, 'changed': True}


@router.get('/evidence/{evidence_id}/history')
def evidence_history(evidence_id: int):
    with connect() as conn:
        return [{**dict(row), 'snapshot': json.loads(row['snapshot_json'])} for row in conn.execute(
            'SELECT * FROM evidence_revisions WHERE evidence_id=? ORDER BY revision DESC', (evidence_id,))]


def linked_evidence_ids(item):
    original = item.get('original_assessments', item['assessments'])
    ids = set(item['personal_evidence_ids']) - {ident for value in original for ident in value['personal_evidence_ids']}
    return ids | {ident for value in item['assessments'] for ident in value['personal_evidence_ids']}


def finding_basis(conn, item):
    """Only explicitly linked current sources enter a user's review acknowledgement."""
    from .analysis import digest
    from .requirements import source_hash
    evidence_ids = linked_evidence_ids(item)
    requirements = set(item['requirement_ids'])
    for assessment in item['assessments']:
        evidence_ids.update(assessment['personal_evidence_ids'])
        requirements.add(assessment['requirement_id'])
    evidence = {}
    for ident in sorted(evidence_ids):
        row = conn.execute('SELECT * FROM profile_evidence WHERE id=?', (ident,)).fetchone()
        evidence[str(ident)] = digest(evidence_record(row)) if row else None
    reqs = {}
    for ident in sorted(requirements):
        row = conn.execute('SELECT * FROM job_requirements WHERE id=?', (ident,)).fetchone()
        reqs[str(ident)] = digest(dict(row)) if row else None
    jobs = {}
    for ident in item['job_ids']:
        row = conn.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
        jobs[str(ident)] = source_hash(dict(row)) if row else None
    return {'evidence': evidence, 'requirements': reqs, 'jobs': jobs}


def decorate_finding(conn, item):
    original = item['assessments']
    item['original_assessments'] = original
    overrides = json.loads(item.get('assessment_overrides_json') or '[]')
    if overrides:
        by_req = {row['requirement_id']: row for row in overrides}
        item['assessments'] = [by_req.get(row['requirement_id'], row) for row in original]
    acknowledged = json.loads(item.get('review_snapshot_json') or '{}')
    if acknowledged:
        changed = acknowledged != finding_basis(conn, item)
    else:
        from .career_export import compare_career_snapshot
        changed = bool(compare_career_snapshot(conn, item['package_id'])['career_stale_token'])
        # Legacy analyses also need a check against their original job snapshot.
        if not changed and item['job_ids']:
            from .analysis import PackageRequest, digest, fetch_records
            package = conn.execute('SELECT snapshot_json,filters_json FROM analysis_packages WHERE id=?',
                                   (item['package_id'],)).fetchone()
            if package:
                present = [ident for ident in item['job_ids'] if conn.execute('SELECT 1 FROM jobs WHERE id=?', (ident,)).fetchone()]
                current = fetch_records(conn, PackageRequest(job_ids=present,
                    include_private=bool(json.loads(package['filters_json']).get('include_private')))) if present else []
                hashes = json.loads(package['snapshot_json'])
                changed = len(present) != len(item['job_ids']) or any(hashes.get(str(row['job_id'])) != digest(row) for row in current)
    item['source_changed'] = changed
    item['review_status'] = 'needs_review' if item['review_state'] == 'current' and changed else item['review_state']
    item['action_tasks'] = {str(row['requirement_id']): row['task_id'] for row in conn.execute(
        'SELECT requirement_id,task_id FROM finding_action_tasks WHERE finding_id=?', (item['id'],))}
    requirement_ids = sorted(set(item['requirement_ids']) | {row['requirement_id'] for row in item['assessments']})
    item['requirement_details'] = [dict(row) for row in conn.execute(
        'SELECT id,job_id,name,evidence,source_field FROM job_requirements WHERE id IN (' +
        ','.join('?' for _ in requirement_ids) + ')', requirement_ids)] if requirement_ids else []
    item['job_details'] = [dict(row) for row in conn.execute(
        'SELECT j.id,j.title,c.name AS company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id IN (' +
        ','.join('?' for _ in item['job_ids']) + ')', item['job_ids'])] if item['job_ids'] else []
    return item


class FindingReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(gt=0)
    state: Literal['current', 'needs_review', 'rejected']
    note: str = Field(default='', max_length=4000)
    assessments: list[dict] | None = Field(default=None, max_length=100)


@router.post('/analysis/findings/{finding_id}/review')
def review_finding(finding_id: int, request: FindingReview):
    from .analysis import RequirementAssessment, finding_record
    from pydantic import ValidationError
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM analysis_findings WHERE id=?', (finding_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '分析结论不存在')
        if row['revision'] != request.expected_revision:
            raise HTTPException(409, '分析取舍已在其他窗口修改，请重新打开核对')
        item = decorate_finding(conn, finding_record(row))
        assessments = json.loads(row['assessment_overrides_json'])
        if request.assessments is not None:
            try:
                assessments = [RequirementAssessment(**value).model_dump() for value in request.assessments]
            except ValidationError as exc:
                raise HTTPException(422, str(exc)) from exc
            ids = [value['requirement_id'] for value in assessments]
            allowed = {value['requirement_id'] for value in item['original_assessments']}
            if len(ids) != len(set(ids)) or not set(ids) <= allowed:
                raise HTTPException(422, '只能修订本条分析已有的逐项判断')
            for value in assessments:
                for ident in value['personal_evidence_ids']:
                    if not conn.execute('SELECT 1 FROM profile_evidence WHERE id=?', (ident,)).fetchone():
                        raise HTTPException(422, '修订引用的个人经历不存在')
            edits = {value['requirement_id']: value for value in assessments}
            item['assessments'] = [edits.get(value['requirement_id'], value) for value in item['original_assessments']]
        basis = finding_basis(conn, item)
        conn.execute('''INSERT INTO finding_revisions(finding_id,revision,state,note,assessments_json,basis_json)
            VALUES(?,?,?,?,?,?)''', (finding_id, row['revision'], request.state, request.note,
                                    encoded(assessments), encoded(basis)))
        conn.execute('''UPDATE analysis_findings SET review_state=?,review_note=?,assessment_overrides_json=?,
            review_snapshot_json=?,revision=revision+1 WHERE id=?''',
            (request.state, request.note, encoded(assessments), encoded(basis), finding_id))
        return {'id': finding_id, 'revision': row['revision'] + 1}


@router.get('/analysis/findings/{finding_id}/history')
def finding_history(finding_id: int):
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            'SELECT * FROM finding_revisions WHERE finding_id=? ORDER BY revision DESC', (finding_id,))]


@router.post('/analysis/findings/{finding_id}/assessments/{requirement_id}/task')
def action_to_task(finding_id: int, requirement_id: int):
    from .analysis import finding_record
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT task_id FROM finding_action_tasks WHERE finding_id=? AND requirement_id=?',
                           (finding_id, requirement_id)).fetchone()
        if old:
            return {'id': old['task_id'], 'already_adopted': True}
        row = conn.execute('SELECT * FROM analysis_findings WHERE id=?', (finding_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '分析结论不存在')
        item = decorate_finding(conn, finding_record(row))
        if item['review_status'] == 'rejected':
            raise HTTPException(409, '这条建议已设为不采用；可先恢复使用，或直接新建任务')
        assessment = next((a for a in item['assessments'] if a['requirement_id'] == requirement_id), None)
        if assessment is None:
            raise HTTPException(404, '逐项行动不存在')
        requirement = conn.execute('SELECT * FROM job_requirements WHERE id=?', (requirement_id,)).fetchone()
        if not requirement:
            raise HTTPException(422, '原要求已删除，请重新分析')
        task = conn.execute('''INSERT INTO learning_tasks(title,related_skill,source_job_id,priority,notes)
            VALUES(?,?,?,?,?)''', (assessment['next_action'][:200], requirement['name'], requirement['job_id'],
            item['priority'], assessment['next_action'] + '\n来源：分析 #' + str(finding_id) +
            '，要求 #' + str(requirement_id) + '\n' + assessment['explanation'])).lastrowid
        conn.execute('INSERT INTO finding_action_tasks(finding_id,requirement_id,task_id) VALUES(?,?,?)',
                     (finding_id, requirement_id, task))
        from .examples import track_example_task
        track_example_task(conn, finding_id, task)
        return {'id': task, 'already_adopted': False}


def _rows_by_id(conn, sql, ids, chunk=500):
    wanted = sorted(i for i in ids if i is not None)
    found = {}
    for start in range(0, len(wanted), chunk):
        part = wanted[start:start + chunk]
        for row in conn.execute(sql.format(','.join('?' * len(part))), part):
            found[row['id']] = dict(row)
    return found


def learning_records(conn, task_id=None):
    rows = [dict(row) for row in conn.execute('SELECT * FROM learning_tasks' +
        (' WHERE id=?' if task_id is not None else '') + ' ORDER BY id DESC',
        (task_id,) if task_id is not None else ())]
    if not rows:
        return rows
    records = {row['id']: row for row in rows}
    jobs = {ident: {row['source_job_id']} if row['source_job_id'] else set() for ident, row in records.items()}
    reqs = {ident: set() for ident in records}
    for finding in conn.execute('SELECT adopted_task_id,job_ids_json,requirement_ids_json FROM analysis_findings WHERE adopted_task_id IS NOT NULL'):
        ident = finding['adopted_task_id']
        if ident in records:
            jobs[ident].update(json.loads(finding['job_ids_json']))
            reqs[ident].update(json.loads(finding['requirement_ids_json']))
    for action in conn.execute('SELECT a.task_id,a.requirement_id,r.job_id FROM finding_action_tasks a LEFT JOIN job_requirements r ON r.id=a.requirement_id'):
        if action['task_id'] in records:
            if action['job_id']:
                jobs[action['task_id']].add(action['job_id'])
            reqs[action['task_id']].add(action['requirement_id'])
    # Only the jobs and requirements the tasks link to; scanning every
    # requirement made each task-list read grow with the whole library.
    job_map = _rows_by_id(conn, 'SELECT j.id,j.title,c.name AS company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id IN ({})',
                          set().union(*jobs.values()))
    req_map = _rows_by_id(conn, 'SELECT id,job_id,name,evidence FROM job_requirements WHERE id IN ({})',
                          set().union(*reqs.values()))
    outcomes = {}
    for row in conn.execute('SELECT task_id,evidence_id FROM learning_outcomes ORDER BY id'):
        outcomes.setdefault(row['task_id'], []).append(row['evidence_id'])
    for ident, row in records.items():
        row['linked_jobs'] = [job_map[j] for j in sorted(jobs[ident]) if j in job_map]
        row['linked_requirements'] = [req_map[r] for r in sorted(reqs[ident]) if r in req_map]
        row['outcome_evidence_ids'] = outcomes.get(ident, [])
    return rows


class TaskCompletion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    completion_id: UUID
    expected_revision: int = Field(gt=0)
    evidence: EvidenceData | None = None


@router.post('/learning/{task_id}/complete')
def complete_task(task_id: int, request: TaskCompletion):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        token = str(request.completion_id)
        previous = conn.execute('SELECT * FROM learning_completions WHERE completion_id=?', (token,)).fetchone()
        if previous:
            if previous['task_id'] != task_id:
                raise HTTPException(409, '完成凭证已用于另一个任务')
            return json.loads(previous['response_json'])
        task = conn.execute('SELECT * FROM learning_tasks WHERE id=?', (task_id,)).fetchone()
        if task is None:
            raise HTTPException(404, '准备任务不存在')
        if task['revision'] != request.expected_revision:
            raise HTTPException(409, '准备任务已在其他窗口修改，请重新打开核对')
        evidence_id = create_evidence(conn, request.evidence) if request.evidence is not None else None
        if evidence_id:
            conn.execute('INSERT INTO learning_outcomes(task_id,evidence_id) VALUES(?,?)', (task_id, evidence_id))
        conn.execute("UPDATE learning_tasks SET status='done',revision=revision+1 WHERE id=?", (task_id,))
        result = {'id': task_id, 'evidence_id': evidence_id, 'revision': task['revision'] + 1}
        conn.execute('INSERT INTO learning_completions(completion_id,task_id,response_json) VALUES(?,?,?)',
                     (token, task_id, encoded(result)))
        return result
