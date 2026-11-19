r"""MCP server (stdio) that lets your own AI agent use Zemu.

Only the Python standard library is used. Configure your agent to run:

    python tools/radar_mcp.py

The server talks to the running app through the local agent API (/api/agent/v1).
It finds the app in this order, on every call (the desktop port changes per start):
  1. RADAR_PRIVATE_RC_URL and RADAR_PRIVATE_RC_AGENT_TOKEN environment variables;
  2. agent-endpoint.json written by the running desktop app in its data folder
     (RADAR_PRIVATE_RC_DATA_DIR, or %LOCALAPPDATA%\InternshipRadarPrivateRC\data);
  3. the source-mode server at http://127.0.0.1:8785 with data/agent-token.
See docs/agent-api.md. This server never calls a model; writing results requires the
same validation as the app's manual import and a token from a matching preview.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVER_VERSION = '1.1.0'
DEFAULT_PROTOCOL = '2025-06-18'
REPO_DATA = Path(__file__).resolve().parents[1] / 'data'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def loopback_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('择木地址必须是本机 HTTP 地址')
    # Use the loopback IP even when the configured name is localhost.
    return f'http://127.0.0.1:{parsed.port if parsed.port is not None else 80}'


def matches_data_dir(base, folder, pid=None):
    # Check instance identity without sending a credential to a stale port.
    with OPENER.open(base + '/api/agent-access', timeout=2) as response:
        info = json.load(response)
    return (info['api_base'] == '/api/agent/v1'
            and Path(info['data_dir']).resolve() == folder.resolve()
            and (pid is None or info.get('pid', pid) == pid))


def connection():
    """Return (base_url, token) for the running app."""
    url, token = os.environ.get('RADAR_PRIVATE_RC_URL', '').strip(), os.environ.get('RADAR_PRIVATE_RC_AGENT_TOKEN', '').strip()
    if url or token:
        if not url or not token:
            raise ValueError('请同时设置 RADAR_PRIVATE_RC_URL 与 RADAR_PRIVATE_RC_AGENT_TOKEN')
        return loopback_url(url), token

    override = os.environ.get('RADAR_PRIVATE_RC_DATA_DIR')
    local = os.environ.get('LOCALAPPDATA')
    source_dirs = [Path(override), REPO_DATA] if override else [REPO_DATA]
    desktop_dirs = ([Path(override)] if override else [])
    if local:
        desktop_dirs.append(Path(local) / 'InternshipRadarPrivateRC' / 'data')
    for folder in dict.fromkeys(desktop_dirs + source_dirs):
        try:
            info = json.loads((folder / 'agent-endpoint.json').read_text(encoding='utf-8'))
            base = loopback_url(info['url'])
            if matches_data_dir(base, folder, info.get('pid')):
                token = (folder / 'agent-token').read_text(encoding='utf-8').strip()
                if token:
                    return base, token
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    for folder in dict.fromkeys(source_dirs):
        try:
            token = (folder / 'agent-token').read_text(encoding='utf-8').strip()
            base = 'http://127.0.0.1:8785'
            if token and matches_data_dir(base, folder):
                return base, token
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    raise RuntimeError('找不到正在运行的择木：请先打开应用，或设置 RADAR_PRIVATE_RC_URL 与 RADAR_PRIVATE_RC_AGENT_TOKEN')


def call_api(method, path, query=None, body=None, preview_token=None):
    base, token = connection()
    url = base + '/api/agent/v1' + path
    if query:
        url += '?' + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, '')})
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json'}
    if preview_token:
        headers['X-Radar-Preview'] = preview_token
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with OPENER.open(request, timeout=120) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise RuntimeError('择木接口发生重定向，请检查本机地址') from exc
        detail = exc.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail).get('detail', detail)
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f'择木返回 {exc.code}：{json.dumps(detail, ensure_ascii=False)}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'无法连接择木（{base}）：{exc.reason}。请确认应用正在运行') from exc


IDS = {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1, 'maxItems': 500}
TOOLS = [
    {'name': 'radar_info', 'description': '查看择木 agent 接口的说明、可用结果类型和处理规则。先调用它。',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'list_jobs', 'description': '列出本地岗位库中的岗位（默认不含 JD 全文）。',
     'inputSchema': {'type': 'object', 'properties': {
         'q': {'type': 'string', 'description': '在岗位名、企业名和 JD 中搜索'},
         'status': {'type': 'string', 'enum': ['', 'candidate', 'verified', 'uncertain', 'closed']},
         'priority': {'type': 'string', 'enum': ['', 'unrated', 'high', 'medium', 'low', 'skip']},
         'direction': {'type': 'string', 'maxLength': 80},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 100},
         'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
         'include_jd': {'type': 'boolean', 'default': False}}}},
    {'name': 'get_job', 'description': '读取单个岗位的完整资料、来源与技能。',
     'inputSchema': {'type': 'object', 'properties': {'job_id': {'type': 'integer', 'minimum': 1}}, 'required': ['job_id']}},
    {'name': 'export_jd_extraction_package',
     'description': '为指定岗位生成 JD 要求提取数据包。每个包自带 task_instructions；按说明逐包处理，结果保留原 package_id。',
     'inputSchema': {'type': 'object', 'properties': {'job_ids': IDS}, 'required': ['job_ids']}},
    {'name': 'export_career_analysis_package',
     'description': '生成求职分析数据包并返回对应提示词（prompt_text）。默认不含个人能力证据，只有用户明确同意时才传 evidence_ids。',
     'inputSchema': {'type': 'object', 'properties': {
         'job_ids': IDS, 'goal': {'type': 'string', 'enum': ['gap', 'direction', 'preparation'], 'default': 'gap'},
         'mode': {'type': 'string', 'enum': ['compact', 'full'], 'default': 'compact'},
         'evidence_ids': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'maxItems': 100},
         'include_ai_reviewed': {'type': 'boolean', 'default': False}}, 'required': ['job_ids']}},
    {'name': 'get_prompt', 'description': '读取提示词全文：jd-extraction、career-gap、career-direction、career-preparation、jd-review。',
     'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string', 'enum': [
         'jd-extraction', 'career-gap', 'career-direction', 'career-preparation', 'jd-review']}}, 'required': ['name']}},
    {'name': 'preview_result',
     'description': '预览一份 AI 结果（要求提取、求职分析结论、岗位优先级或要求复核），自动识别类型，只校验不写入。',
     'inputSchema': {'type': 'object', 'properties': {'result': {'description': '完整的结果 JSON 对象'}}, 'required': ['result']}},
    {'name': 'import_result',
     'description': '写入用户确认过的 AI 结果。提交时使用对应 preview_result 返回的令牌。岗位优先级会立即生效。',
     'inputSchema': {'type': 'object', 'properties': {
         'result': {'description': '与预览时相同的结果 JSON 对象'},
         'preview_token': {'type': 'string', 'description': '同一份结果预览返回的 preview_token'}},
         'required': ['result', 'preview_token']}},
]


def run_tool(name, args):
    if name == 'radar_info':
        return call_api('GET', '')
    if name == 'list_jobs':
        return call_api('GET', '/jobs', query={k: (str(v).lower() if isinstance(v, bool) else v) for k, v in args.items()})
    if name == 'get_job':
        return call_api('GET', f"/jobs/{int(args['job_id'])}")
    if name == 'export_jd_extraction_package':
        return call_api('POST', '/packages/jd-extraction', body={'job_ids': args['job_ids']})
    if name == 'export_career_analysis_package':
        return call_api('POST', '/packages/career-analysis', body=args)
    if name == 'get_prompt':
        return call_api('GET', '/prompts/' + urllib.parse.quote(args['name']))
    if name in ('preview_result', 'import_result'):
        commit = 'true' if name == 'import_result' else 'false'
        return call_api('POST', '/results', query={'commit': commit}, body=args['result'],
                        preview_token=args['preview_token'] if name == 'import_result' else None)
    raise ValueError('未知工具：' + name)


def handle(message):
    """Handle one JSON-RPC message; return the response dict, or None for notifications."""
    method, msg_id = message.get('method'), message.get('id')
    if msg_id is None:
        return None
    try:
        if method == 'initialize':
            requested = (message.get('params') or {}).get('protocolVersion') or DEFAULT_PROTOCOL
            result = {'protocolVersion': requested, 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'ai-internship-radar', 'version': SERVER_VERSION}}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            params = message.get('params') or {}
            try:
                value = run_tool(params.get('name'), params.get('arguments') or {})
                result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False, indent=2)}], 'isError': False}
            except Exception as exc:  # tool failures are reported to the agent, not as protocol errors
                result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
        else:
            return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': -32601, 'message': f'Method not found: {method}'}}
        return {'jsonrpc': '2.0', 'id': msg_id, 'result': result}
    except Exception as exc:
        return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': -32603, 'message': str(exc)}}


def main():
    stdin = sys.stdin.buffer
    out = sys.stdout.buffer
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode('utf-8'))
        except ValueError:
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        else:
            response = handle(message)
        if response is not None:
            out.write(json.dumps(response, ensure_ascii=False).encode('utf-8') + b'\n')
            out.flush()


if __name__ == '__main__':
    main()
