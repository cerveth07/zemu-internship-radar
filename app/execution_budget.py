"""Reserve Shixiseng plan allowances using existing owned run records."""
import json
import time
from datetime import datetime, timezone

from fastapi import HTTPException

from . import discovery


def epoch(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def run_usage(conn, row):
    preferences = json.loads(row['preferences_json'])
    state = json.loads(row['platform_state_json'])
    reserved = preferences.get('_execution_budget', {})
    if not reserved and state.get('ledger_reported') is not True:
        # Older executions have no cost proof: retain their whole saved allowance.
        owner = conn.execute('SELECT a.action_json,p.snapshot_json FROM planner_actions a JOIN planner_plans p ON p.id=a.plan_id WHERE a.batch_id=?', (row['batch_id'],)).fetchone()
        if owner:
            action = json.loads(owner['action_json'])
            reserved = {'list_pages': action['list_pages'], 'detail_attempts': action['detail_attempts'],
                        'time_seconds': json.loads(owner['snapshot_json'])['budgets']['time_minutes']*60}
    if state.get('no_access') is True:
        return 0, 0, 0.0
    costs = discovery.receipt_costs(conn, row['id'])
    complete = (row['status'] in {'completed', 'completed_with_shortage', 'completed_with_errors', 'stopped', 'failed'}
                and state.get('ledger_reported') is True and costs['unknown_attempts'] == 0)
    pages = costs['list_attempts'] if complete else max(costs['list_attempts'], reserved.get('list_pages', 0))
    details = costs['detail_attempts'] if complete else max(costs['detail_attempts'], reserved.get('detail_attempts', 0))
    start, end = epoch(row['created_at']), epoch(row['finished_at'])
    elapsed = max(0, end - start) if start is not None and end is not None else reserved.get('time_seconds', 0)
    return pages, details, elapsed


def action_budget(conn, batch_id):
    row = conn.execute('''SELECT a.*,p.snapshot_json FROM planner_actions a
        JOIN planner_plans p ON p.id=a.plan_id WHERE a.batch_id=?''', (batch_id,)).fetchone()
    if row is None:
        return None
    action, snapshot = json.loads(row['action_json']), json.loads(row['snapshot_json'])
    if action['platform'] != 'shixiseng':
        return None
    rows = conn.execute('SELECT * FROM capture_search_runs WHERE batch_id=?', (batch_id,)).fetchall()
    usage = [run_usage(conn, item) for item in rows]
    page_left = max(0, action['list_pages'] - sum(item[0] for item in usage))
    detail_left = max(0, action['detail_attempts'] - sum(item[1] for item in usage))
    all_rows = conn.execute('''SELECT r.* FROM capture_search_runs r JOIN planner_actions a ON a.batch_id=r.batch_id
        WHERE a.plan_id=? AND r.platform='shixiseng' ''', (row['plan_id'],)).fetchall()
    time_limit = snapshot['budgets']['time_minutes'] * 60
    time_left = max(0, time_limit - sum(run_usage(conn, item)[2] for item in all_rows))
    return {'plan_id': row['plan_id'], 'action_id': row['action_id'], 'action': action,
            'remaining_list_pages': page_left, 'remaining_detail_attempts': detail_left,
            'remaining_time_seconds': time_left, 'scope': 'shixiseng'}


def constrain(conn, batch_id, preferences):
    """Read at preview; repeat inside BEGIN IMMEDIATE before inserting the run."""
    result = dict(preferences)
    result.pop('_execution_budget', None)
    result.pop('deadline_at', None)
    budget = action_budget(conn, batch_id)
    if budget is None:
        return result
    action, mode = budget['action'], preferences.get('collection_mode', 'collect')
    if preferences['keywords'] != [action['keyword']] or preferences['cities'] != [action['city']]:
        raise HTTPException(409, '计划批次的查询条件已改变，请重新建立检索计划')
    remaining_time = budget['remaining_time_seconds']
    if remaining_time < 1:
        raise HTTPException(409, '该计划的实习僧执行时间额度已用尽；未知访问仍占用额度，请先核对任务')
    pages = 0 if mode == 'hydrate' else min(preferences['max_pages']['shixiseng'], budget['remaining_list_pages'])
    details = 0 if mode == 'discover' else min(preferences['max_jobs'], budget['remaining_detail_attempts'])
    if mode != 'hydrate' and pages == 0:
        raise HTTPException(409, '本计划查询的列表额度已用尽；已有候选可选择补齐详情')
    if mode != 'discover' and details == 0:
        raise HTTPException(409, '本计划查询的详情额度已用尽；需继续读取时请建立新计划')
    result['max_pages'] = {'shixiseng': pages or 1}
    result['max_list_attempts'] = pages
    result['max_jobs'] = details or 1
    result['deadline_at'] = time.time() + remaining_time
    result['_execution_budget'] = {'plan_id': budget['plan_id'], 'action_id': budget['action_id'],
                                   'list_pages': pages, 'detail_attempts': details, 'time_seconds': remaining_time}
    return result
