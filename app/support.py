"""User-facing diagnostics contain metadata only, never JD text or credentials."""
import os
import platform
import re
import shutil
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from . import VERSION
from .db import connect, SCHEMA_VERSION

router = APIRouter(prefix='/api/support', tags=['Support'])


def diagnostic_report(log_path=None):
    with connect() as db:
        schema = db.execute('PRAGMA user_version').fetchone()[0]
        integrity = db.execute('PRAGMA quick_check').fetchone()[0]
    errors = []
    if log_path and Path(log_path).is_file():
        with Path(log_path).open('rb') as stream:
            stream.seek(max(0, Path(log_path).stat().st_size - 65536))
            lines = stream.read().decode('utf-8', 'replace').splitlines()
        for line in lines:
            # Raw log messages can contain private text. Export only bounded error metadata.
            match = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,\d]* (ERROR|WARNING)\b', line)
            if match:
                errors.append({'time': match[1], 'level': match[2]})
    from .shixiseng_runtime import node_path
    try:
        node_available = bool(shutil.which(str(node_path())))
    except RuntimeError:
        node_available = False
    return {'app_version': VERSION, 'system': platform.system(), 'python': platform.python_version(),
            'environment_checks': {'wsl_executable_available': bool(shutil.which('wsl.exe')),
                                   'shixiseng_node_available': node_available},
            'schema_version': schema, 'expected_schema': SCHEMA_VERSION,
            'database_ok': integrity == 'ok', 'error_log_summary': errors[-30:],
            'privacy': '仅包含版本、系统、数据库检查与错误发生时间；不包含日志原文、路径、岗位、联系人或 Cookie。'}


@router.get('/diagnostics')
def diagnostics():
    base = os.getenv('LOCALAPPDATA')
    log = Path(base) / 'InternshipRadarPrivateRC/logs/desktop.log' if base else None
    return JSONResponse(diagnostic_report(log), headers={
        'Content-Disposition': 'attachment; filename="radar-diagnostics.json"'})
