"""Read the existing evidence, findings and actions associated with one job."""
import json

from .analysis import PackageRequest, digest, fetch_records, finding_record
from .career_export import compare_career_snapshot
from .requirements import source_hash
from .career_workspace import decorate_finding, evidence_record, learning_records


def job_dossier(conn, job):
    job_id = job['id']
    findings = [decorate_finding(conn, finding_record(row)) for row in conn.execute("""
        SELECT f.* FROM analysis_findings f
        WHERE EXISTS (SELECT 1 FROM json_each(f.job_ids_json) WHERE value=?)
           OR EXISTS (SELECT 1 FROM json_each(f.requirement_ids_json) ids
               JOIN job_requirements r ON r.id=ids.value WHERE r.job_id=?)
        ORDER BY f.id DESC
    """, (job_id, job_id))]
    referenced_requirements = {ident for finding in findings for ident in finding['requirement_ids']}
    requirements = []
    current_hash = source_hash(job)
    for row in conn.execute('SELECT * FROM job_requirements WHERE job_id=? ORDER BY id DESC', (job_id,)):
        if row['review_status'] == 'rejected' and row['id'] not in referenced_requirements:
            continue
        item = dict(row)
        item['alternatives'] = json.loads(item.pop('alternatives_json'))
        item['stale'] = item['source_jd_hash'] != current_hash
        requirements.append(item)

    evidence_ids = sorted({ident for finding in findings for ident in finding['personal_evidence_ids']})
    evidence_ids = sorted(set(evidence_ids) | {ident for finding in findings for item in finding['assessments'] for ident in item['personal_evidence_ids']})
    evidence = []
    for start in range(0, len(evidence_ids), 400):
        part = evidence_ids[start:start + 400]
        evidence.extend(evidence_record(row) for row in conn.execute(
            'SELECT * FROM profile_evidence WHERE id IN (' + ','.join('?' for _ in part) + ') ORDER BY id', part))

    tasks = [task for task in learning_records(conn) if any(link['id'] == job_id for link in task['linked_jobs'])]
    return {
        'requirements': requirements,
        'findings': findings,
        'personal_evidence': evidence,
        'missing_personal_evidence_ids': sorted(set(evidence_ids) - {item['id'] for item in evidence}),
        'learning_tasks': sorted(tasks, key=lambda item: (item['status'] == 'done', -item['id'])),
    }
