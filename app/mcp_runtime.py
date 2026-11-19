"""Source and packaged MCP entry points; stdout is reserved for JSON-RPC."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


def config():
    args = ['--mcp'] if getattr(sys, 'frozen', False) else [str(Path(__file__).resolve().parents[1] / 'tools/radar_mcp.py')]
    return {'mcpServers': {'ai-internship-radar': {'command': sys.executable, 'args': args}}}


def restore_stdio():
    if os.name != 'nt':
        return
    import ctypes
    import io
    import msvcrt
    from ctypes import wintypes
    get_handle = ctypes.windll.kernel32.GetStdHandle
    get_handle.argtypes = [wintypes.DWORD]
    get_handle.restype = wintypes.HANDLE
    for name, code, mode, flags in [('stdin', -10, 'rb', os.O_RDONLY), ('stdout', -11, 'wb', os.O_WRONLY), ('stderr', -12, 'wb', os.O_WRONLY)]:
        if getattr(sys, name) is not None:
            continue
        handle = get_handle(code & 0xffffffff)
        if not handle or handle == ctypes.c_void_p(-1).value:
            if name != 'stderr':
                raise RuntimeError('MCP stdio requires a client with redirected stdin/stdout')
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
            continue
        descriptor = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
        stream = io.TextIOWrapper(os.fdopen(descriptor, mode, closefd=False), encoding='utf-8', write_through=True)
        setattr(sys, name, stream)


def serve():
    restore_stdio()
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tools/radar_mcp.py'), run_name='__main__')
    return 0


def check(base_url, folder):
    entry = config()['mcpServers']['ai-internship-radar']
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'clientInfo': {'name': 'radar-check', 'version': '1'}}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'radar_info', 'arguments': {}}}]
    env = dict(os.environ, RADAR_PRIVATE_RC_URL=base_url, RADAR_PRIVATE_RC_DATA_DIR=str(folder), PYTHONUTF8='1')
    try:
        env['RADAR_PRIVATE_RC_AGENT_TOKEN'] = (Path(folder) / 'agent-token').read_text(encoding='utf-8').strip()
    except OSError:
        return {'ok': False, 'message': '本机接口令牌尚未建立，请重新打开应用。'}
    try:
        process = subprocess.run([entry['command'], *entry['args']],
            input=''.join(json.dumps(value) + '\n' for value in requests), text=True, encoding='utf-8',
            capture_output=True, timeout=25, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        replies = {value['id']: value for line in process.stdout.splitlines() if line.strip()
                   for value in [json.loads(line)]}
        valid = process.returncode == 0 and all(ident in replies and 'result' in replies[ident] for ident in (1, 2, 3))
        valid = valid and not replies[3]['result'].get('isError', True)
        if not valid:
            return {'ok': False, 'message': 'MCP 握手或本机接口检测未通过，请确认应用保持运行。'}
        return {'ok': True, 'message': 'MCP 握手、工具列表及本机接口访问均已通过',
                'tool_count': len(replies[2]['result']['tools'])}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return {'ok': False, 'message': 'MCP 进程未能完成连接检测，请重新打开应用后重试。'}
