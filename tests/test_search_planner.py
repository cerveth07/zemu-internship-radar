import concurrent.futures
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RADAR_PRIVATE_RC_DB_PATH', str(tmp_path / 'planner.sqlite'))
    with TestClient(app) as value:
        yield value


def family(fid='B01', **changes):
    return {'id': fid, 'route': 'Agent', 'name': fid, 'priority': 'P0',
            'representative': 'Agent 产品', 'aliases': ['智能体产品'],
            'jd_features': ['MCP'], 'qualification': ['学历需核验'], 'risk': ['人工标注'], **changes}


def save(client, families=None, **changes):
    return client.post('/api/planner/library/import', json={'families': families or [family()], 'expected_version': 0, **changes})


def request(**changes):
    return {'family_ids': ['B01'], 'cities': ['北京', '上海'], 'platforms': ['boss', 'shixiseng'],
            'list_page_budget': 3, 'detail_attempt_budget': 5, 'time_budget_minutes': 10,
            'pages_per_query': 2, 'expected_version': 1, **changes}


def test_markdown_actual_shapes_and_distinct_rules(client):
    text = '''## P0 核心\n| 岗位分类 | 岗位名称检索词（分别搜索） | JD 高价值识别词 |\n|---|---|---|\n| B01 Agent 产品 | Agent 产品、智能体产品 | MCP、Tool Calling |\n## P1 重点\n| 编号 | 岗位分类 | 岗位名称检索词 | JD 高价值识别词 |\n|---|---|---|---|\n| D01 | FDE | FDE实习、前向部署工程师 | PoC |\n## 识别\n| 识别维度 | 高价值关键词 | 检索含义 |\n|---|---|---|\n| 工程开发 | Python、API | 判断 |\n| 处理方式 | 关键词或条件 | 雷达动作 |\n|---|---|---|\n| 硬排除 | 毕业时间确认不满足 | 复核 |\n| 强降权 | 主要职责为人工标注 | 降权 |'''
    preview = client.post('/api/planner/import-preview', json={'format': 'auto', 'text': text})
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data['counts']['families'] == 2
    assert data['families'][0]['representative'] == 'Agent 产品'
    assert data['families'][0]['aliases'] == ['智能体产品']
    assert data['families'][1]['priority'] == 'P1'
    assert data['global_qualification'] == ['毕业时间确认不满足']
    assert data['global_risk'] == ['主要职责为人工标注']
    assert client.get('/api/planner/library').json()['families'] == []
    data['expected_version'] = 0
    assert client.post('/api/planner/library/import', json=data).status_code == 200
    assert client.get('/api/planner/library').json()['global_jd_features'] == ['Python', 'API']


def test_json_csv_and_validation(client):
    payload = json.dumps([family()], ensure_ascii=False)
    assert client.post('/api/planner/import-preview', json={'text': payload}).json()['counts']['search_terms'] == 2
    csv = 'id,route,name,representative,aliases,enabled\nB01,Agent,产品,Agent 产品,智能体产品、智能体平台,true\n'
    data = client.post('/api/planner/import-preview', json={'format': 'csv', 'text': csv}).json()
    assert data['families'][0]['aliases'] == ['智能体产品', '智能体平台']
    for text in ['{}', '[1]', json.dumps([family(), family()]), json.dumps([family(representative='a,b')])]:
        assert client.post('/api/planner/import-preview', json={'text': text}).status_code == 422


def test_version_conflict_edit_and_preserve_global_rules(client):
    assert save(client, global_risk=['职责风险']).status_code == 200
    assert save(client).status_code == 409
    assert client.put('/api/planner/families/B01', json={'expected_version': 1, 'family': family(representative='AI 产品')}).status_code == 200
    assert client.post('/api/planner/library/import', json={'expected_version': 2, 'families': [family('B02')]}).status_code == 200
    assert client.get('/api/planner/library').json()['global_risk'] == ['职责风险']
    assert client.delete('/api/planner/families/B01?expected_version=2').status_code == 409
    assert client.delete('/api/planner/families/B01?expected_version=3').json()['version'] == 4


def test_dedup_budget_unknown_capabilities_and_snapshot(client):
    assert save(client, [family(), family('B02')]).status_code == 200
    body = request(family_ids=['B01', 'B02'])
    preview = client.post('/api/planner/plans/preview', json=body).json()
    assert len(preview['actions']) == 3 and len(preview['uncovered']) == 1
    assert all(a['family_ids'] == ['B01', 'B02'] for a in preview['actions'])
    assert sum(a['list_pages'] for a in preview['actions']) <= 3
    assert sum(a['detail_attempts'] for a in preview['actions']) <= 5
    assert not preview['capabilities']['strict_global_budget']
    assert all('MCP' not in a['keyword'] and '人工标注' not in a['keyword'] for a in preview['actions'])
    stored = client.post('/api/planner/plans', json=body).json()
    assert stored['actions'] == preview['actions']
    client.put('/api/planner/families/B01', json={'expected_version': 1, 'family': family(representative='不同词')})
    assert client.get(f"/api/planner/plans/{stored['id']}").json()['actions'] == preview['actions']
    assert client.post('/api/planner/plans', json=body).status_code == 409


def test_prepare_atomic_reuse_no_collector(client):
    save(client)
    plan = client.post('/api/planner/plans', json=request()).json()
    path = f"/api/planner/plans/{plan['id']}/actions/{plan['actions'][0]['id']}/prepare"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post(path), range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()['batch_id'] for r in responses}) == 1
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM capture_batches').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM capture_search_runs').fetchone()[0] == 0
    assert client.post(path.replace(plan['actions'][0]['id'], 'unknown')).status_code == 404
    assert client.get(f"/api/planner/plans/{plan['id']}").json()['actions'][0]['status'] == 'prepared'


@pytest.mark.parametrize('changes', [{'cities': ['北京,上海']}, {'cities': ['']}, {'list_page_budget': 0},
                                     {'detail_attempt_budget': 601}, {'platforms': ['unknown']}, {'pages_per_query': 4}])
def test_plan_limits(client, changes):
    save(client)
    assert client.post('/api/planner/plans/preview', json=request(**changes)).status_code == 422


def test_family_coverage_and_singular_query_limits(client):
    save(client, [family(), family('B02', representative='模型评测')])
    body = request(family_ids=['B01', 'B02'], list_page_budget=2)
    actions = client.post('/api/planner/plans/preview', json=body).json()['actions']
    assert [a['family_ids'] for a in actions] == [['B01'], ['B02']]
    assert client.post('/api/planner/import-preview', json={'text': json.dumps([family(representative='a' * 121)])}).status_code == 422
    for city in ['不限', '远程', '全部', '全国']:
        assert client.post('/api/planner/plans/preview', json=request(cities=[city])).status_code == 422
    assert client.post('/api/planner/plans/preview', json=request(cities=['全国'], platforms=['shixiseng'])).status_code == 200


def test_csv_missing_cells_and_p2_features(client):
    for text in ['id,route,name,representative,enabled\nB01,Agent,产品,Agent产品\n',
                 'id,route,name,representative,unused\nB01,Agent,产品,Agent产品,x\n']:
        assert client.post('/api/planner/import-preview', json={'format': 'csv', 'text': text}).status_code == 422
    text = '## P2 探索\n| 编号 | 岗位分类 | 岗位名称检索词 | 重点识别 |\n|---|---|---|---|\n| E01 | 机器人 | 机器人产品、具身产品 | VLA、RL |'
    response = client.post('/api/planner/import-preview', json={'text': text})
    assert response.status_code == 200
    assert response.json()['families'][0]['jd_features'] == ['VLA', 'RL']
