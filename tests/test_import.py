import os
from fastapi.testclient import TestClient


def test_import_preview_dedup_and_history(tmp_path):
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(tmp_path / 'import.sqlite')
    from app.main import app
    sample = [
        {'company': '小型 AI 企业', 'title': 'Agent 产品实习生', 'direction': 'pm',
         'city': '北京', 'jd_text': 'JD 原文：负责 Agent 产品方案。',
         'source_platform': 'manual', 'source_url': 'https://example.com/one',
         'skills': ['Agent', '产品设计']},
        {'company': '另一家创业企业', 'title': 'AI 应用开发实习', 'direction': 'agent',
         'city': '上海', 'source_platform': 'manual', 'source_url': 'https://example.com/two',
         'skills': ['Python']},
    ]
    with TestClient(app) as client:
        preview = client.post('/api/import/jobs', json={'rows': sample, 'dry_run': True})
        assert preview.status_code == 200, preview.text
        assert preview.json()['counts'] == {'new': 2, 'duplicate': 0, 'review': 0}
        assert client.get('/api/dashboard').json()['jobs'] == 0
        created = client.post('/api/import/jobs', json={'rows': sample, 'dry_run': False})
        assert created.status_code == 200, created.text
        assert created.json()['counts']['new'] == 2
        assert client.get('/api/dashboard').json()['companies'] == 2
        assert client.get('/api/dashboard').json()['jobs'] == 2
        assert len(client.get('/api/skills').json()) == 3
        job_id = created.json()['results'][0]['existing_job_id']
        assert client.get(f'/api/jobs/{job_id}').json()['history'][0]['event'] == 'imported'
        repeated = client.post('/api/import/jobs', json={'rows': sample, 'dry_run': False})
        assert repeated.json()['counts'] == {'new': 0, 'duplicate': 2, 'review': 0}
        assert client.get('/api/dashboard').json()['jobs'] == 2
        similar = dict(sample[0], source_url='https://example.com/another')
        reviewed = client.post('/api/import/jobs', json={'rows': [similar], 'dry_run': False})
        assert reviewed.json()['counts'] == {'new': 0, 'duplicate': 0, 'review': 1}
        assert client.get('/api/dashboard').json()['jobs'] == 2
        # 修改 JD 留痕，重复保存不增加历史事件。
        before = len(client.get(f'/api/jobs/{job_id}').json()['history'])
        assert client.patch(f'/api/jobs/{job_id}', json={'jd_text':'更新后的原始 JD'}).json()['changed']
        assert len(client.get(f'/api/jobs/{job_id}').json()['history']) == before + 1
        assert client.patch(f'/api/jobs/{job_id}', json={'jd_text':'更新后的原始 JD'}).json()['changed'] is False
        assert len(client.get(f'/api/jobs/{job_id}').json()['history']) == before + 1


def test_import_batch_review_and_validation(tmp_path):
    os.environ['RADAR_PRIVATE_RC_DB_PATH'] = str(tmp_path / 'batch.sqlite')
    from app.main import app
    batch = [
        {'company':'公司甲', 'title':'AI PM 实习', 'city':'北京', 'source_url':'https://example.com/a'},
        {'company':'公司甲', 'title':'AI PM 实习', 'city':'北京', 'source_url':'https://example.com/b'},
        {'company':'公司乙', 'title':'Agent 实习', 'city':'上海'},
    ]
    with TestClient(app) as client:
        preview = client.post('/api/import/jobs', json={'rows': batch, 'dry_run':True})
        assert preview.json()['counts'] == {'new':2, 'duplicate':0, 'review':1}
        result = client.post('/api/import/jobs', json={'rows': batch, 'dry_run':False})
        assert result.json()['counts'] == preview.json()['counts']
        assert client.get('/api/dashboard').json()['jobs'] == 2
        invalid = client.post('/api/import/jobs', json={'rows':[
            {'company':'公司丙', 'title':'有效岗位'},
            {'company':'   ', 'title':'缺失企业'}], 'dry_run':False})
        assert invalid.status_code == 422
        assert client.get('/api/dashboard').json()['jobs'] == 2