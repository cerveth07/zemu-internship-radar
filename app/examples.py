"""An opt-in, visibly labelled first-run example; never mixed into a populated job library."""
import json
import uuid
import sqlite3

from fastapi import APIRouter, HTTPException

from .analysis import PackageRequest, digest, fetch_records
from .career_workspace import EvidenceData, create_evidence
from .db import connect
from .requirements import source_hash

router = APIRouter(prefix='/api/examples')


def mark_example(conn, table, ident):
    conn.execute('INSERT OR IGNORE INTO example_records(table_name,record_id) VALUES(?,?)', (table, str(ident)))


def track_example_task(conn, finding_id, task_id):
    if conn.execute("SELECT 1 FROM example_records WHERE table_name='analysis_findings' AND record_id=?", (str(finding_id),)).fetchone():
        mark_example(conn, 'learning_tasks', task_id)


def register_legacy_examples(conn):
    """Use the old seed's event and fingerprint, never a user's display name alone."""
    jobs = conn.execute("SELECT DISTINCT j.id,j.company_id FROM jobs j JOIN job_history h ON h.job_id=j.id WHERE h.event='example_created'").fetchall()
    ids = {row['id'] for row in jobs}
    for row in jobs:
        mark_example(conn, 'jobs', row['id'])
        mark_example(conn, 'companies', row['company_id'])
    if not ids:
        return
    for finding in conn.execute("SELECT * FROM analysis_findings WHERE title='【示例】一份复盘支持三个运营岗位'").fetchall():
        if (finding['fingerprint'] != digest({'example_package': finding['package_id']})
                or not set(json.loads(finding['job_ids_json'])) <= ids):
            continue
        mark_example(conn, 'analysis_findings', finding['id'])
        mark_example(conn, 'analysis_packages', finding['package_id'])
        for ident in json.loads(finding['personal_evidence_ids_json']):
            mark_example(conn, 'profile_evidence', ident)
        if finding['adopted_task_id']:
            mark_example(conn, 'learning_tasks', finding['adopted_task_id'])
        for row in conn.execute('SELECT task_id FROM finding_action_tasks WHERE finding_id=?', (finding['id'],)):
            mark_example(conn, 'learning_tasks', row['task_id'])


def example_summary(conn):
    return {'active': bool(conn.execute('SELECT 1 FROM example_records LIMIT 1').fetchone()),
            'jobs': conn.execute("SELECT count(*) FROM jobs j JOIN example_records e ON e.table_name='jobs' AND e.record_id=CAST(j.id AS TEXT)").fetchone()[0]}


@router.get('')
def example_status():
    with connect() as conn:
        return example_summary(conn)


@router.post('/remove')
def remove_examples():
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        register_legacy_examples(conn)
        records = {}
        for row in conn.execute('SELECT * FROM example_records'):
            records.setdefault(row['table_name'], []).append(row['record_id'])
        removed = {}
        # A task or personal record that the user edited/completed becomes their
        # own content. New user records are never registered as examples.
        for table in ('learning_tasks', 'analysis_findings', 'jobs', 'profile_evidence', 'analysis_packages', 'companies'):
            count = 0
            for ident in records.get(table, []):
                if table in ('learning_tasks', 'profile_evidence'):
                    row = conn.execute(f'SELECT revision FROM {table} WHERE id=?', (ident,)).fetchone()
                    if row and row['revision'] > 1:
                        continue
                if table == 'companies' and (conn.execute('SELECT 1 FROM jobs WHERE company_id=?', (ident,)).fetchone()
                        or conn.execute('SELECT 1 FROM company_observations WHERE company_id=?', (ident,)).fetchone()):
                    continue
                try:
                    count += conn.execute(f'DELETE FROM {table} WHERE id=?', (ident,)).rowcount
                except sqlite3.IntegrityError:
                    # Keep source packages still used by a user's own analysis.
                    if table != 'analysis_packages':
                        raise
            removed[table] = count
        conn.execute('DELETE FROM example_records')
        return {'removed': removed, 'user_content_preserved': True}


@router.post('/install')
def install_examples():
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM jobs LIMIT 1').fetchone():
            raise HTTPException(409, '示例仅用于空岗位库；已有岗位不会被改写')
        retained = conn.execute("SELECT id,notes FROM companies WHERE name='【示例】山间文创'").fetchone()
        if retained and retained['notes'] == '演示资料，非真实招聘企业':
            company = retained['id']
        else:
            name = '【示例】山间文创' + (' · ' + uuid.uuid4().hex[:6] if retained else '')
            company = conn.execute('INSERT INTO companies(name,notes) VALUES(?,?)', (name, '演示资料，非真实招聘企业')).lastrowid
        jobs = []
        for title, city in [('内容运营实习', '北京'), ('用户运营实习', '杭州'), ('活动运营实习', '上海')]:
            ident = conn.execute('''INSERT INTO jobs(company_id,title,direction,city,jd_text,notes)
                VALUES(?,?,?,?,?,?)''', (company, '【示例】' + title, '产品运营', city,
                '【示例 JD，非真实招聘】熟悉 Excel，能整理活动数据并完成复盘。每周到岗四天，至少三个月。',
                '这是一条演示记录，请勿将其当作真实招聘信息。')).lastrowid
            jobs.append(ident)
            conn.execute("INSERT INTO job_history(job_id,event,payload_json) VALUES(?,'example_created','{}')", (ident,))
        evidence = create_evidence(conn, EvidenceData(skill='Excel', skills=['Excel', '活动运营'],
            description='【示例经历，不代表你的实际经历】在校园活动中整理报名数据，完成参与转化分析。',
            review_status='needs_check'))
        package_id = str(uuid.uuid4())
        records = fetch_records(conn, PackageRequest(job_ids=jobs))
        conn.execute('''INSERT INTO analysis_packages(id,filters_json,snapshot_json,included_ids_json,item_count)
            VALUES(?,?,?,?,?)''', (package_id, json.dumps({'include_private': False}),
            json.dumps({str(row['job_id']): digest(row) for row in records}), json.dumps(jobs), len(jobs)))
        requirements = []
        for ident in jobs:
            job = dict(conn.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone())
            req = conn.execute('''INSERT INTO job_requirements(job_id,package_id,requirement_type,name,strength,
                evidence,source_field,source_jd_hash,fingerprint,review_status)
                VALUES(?,?,'product_skill','Excel 数据整理','must','熟悉 Excel','jd_text',?,?,'confirmed')''',
                (ident, package_id, source_hash(job), digest({'example': ident}))).lastrowid
            requirements.append(req)
        assessments = [{'requirement_id': ident, 'personal_evidence_ids': [evidence], 'judgment': 'transferable',
                        'explanation': '【演示判断】示例活动中包含数据整理经历，可以在准备时补充实际应用场景。',
                        'next_action': '整理已有活动的一页复盘，写清数据口径、结果和局限。'} for ident in requirements]
        finding = conn.execute('''INSERT INTO analysis_findings(package_id,kind,title,content,job_ids_json,
            requirement_ids_json,personal_evidence_ids_json,assessments_json,fingerprint)
            VALUES(?,'learning_task','【示例】一份复盘支持三个运营岗位',?,?,?,?,?,?)''',
            (package_id, '这是一条预置演示分析，不是对你的真实能力评价。先查看参考资料，再采纳准备任务，完成后可登记实际成果。',
             json.dumps(jobs), json.dumps(requirements), json.dumps([evidence]),
             json.dumps(assessments, ensure_ascii=False), digest({'example_package': package_id}))).lastrowid
        register_legacy_examples(conn)
        return {'job_ids': jobs, 'finding_id': finding, 'is_example': True}
