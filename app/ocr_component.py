"""First-use installation of the pinned OCR worker in the Windows lite edition."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from http.client import HTTPException
from pathlib import Path, PureWindowsPath
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile


URL = 'https://github.com/cerveth07/zemu-internship-radar/releases/download/v1.0/Zemu-v1.0-OCR-Windows-x64.zip'
_install_lock = threading.Lock()


class ComponentError(RuntimeError):
    """A message that can be shown directly to the user."""


def manifest():
    if not getattr(sys, 'frozen', False):
        return None
    path = Path(sys._MEIPASS) / 'ocr-component.json'
    return json.loads(path.read_bytes()) if path.is_file() else None


def _destination(spec):
    base = Path(os.environ['LOCALAPPDATA']) / 'InternshipRadarPrivateRC' / 'components' / 'ocr'
    return base / spec['sha256']


def _ready(folder):
    return (folder / '.complete').is_file() and (folder / 'ZemuOCR.exe').is_file()


def installed(spec):
    return _ready(_destination(spec))


def _download(target, spec):
    digest = hashlib.sha256()
    count = 0
    request = Request(URL, headers={'User-Agent': 'Zemu/1.0 Windows OCR', 'Accept-Encoding': 'identity'})
    with urlopen(request, timeout=45) as response, target.open('xb') as output:
        if not response.geturl().startswith('https://'):
            raise ComponentError('OCR 组件需要安全下载，请检查网络后重新识别。')
        while chunk := response.read(256 * 1024):
            count += len(chunk)
            if count > spec['bytes']:
                break
            digest.update(chunk)
            output.write(chunk)
    if count != spec['bytes'] or digest.hexdigest() != spec['sha256']:
        raise ComponentError('OCR 组件下载不完整或校验失败。请重新识别以重试。')


def _unpack(archive, folder):
    with ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            name = PureWindowsPath(entry.filename)
            if (not name.parts or name.anchor or '..' in name.parts or ':' in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise BadZipFile('Unexpected component path')
            path = folder.joinpath(*name.parts)
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as src, path.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
    if not (folder / 'ZemuOCR.exe').is_file():
        raise BadZipFile('Missing OCR worker')


def ensure_installed(spec):
    folder = _destination(spec)
    with _install_lock:
        if _ready(folder):
            return folder
        try:
            folder.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=folder.parent, prefix='.install-') as staging:
                stage = Path(staging)
                archive, payload = stage / 'component.zip', stage / 'payload'
                _download(archive, spec)
                _unpack(archive, payload)
                (payload / '.complete').write_text(spec['sha256'], 'ascii')
                if folder.exists() and not _ready(folder):
                    folder.rename(stage / 'incomplete')
                try:
                    payload.rename(folder)
                except OSError:
                    if not _ready(folder):
                        raise
        except ComponentError:
            raise
        except (OSError, EOFError, HTTPException, BadZipFile) as exc:
            raise ComponentError('OCR 组件下载或安装失败。请检查 GitHub 连接和磁盘空间，然后重新识别。') from exc
    return folder


def recognize(path, spec):
    folder = ensure_installed(spec)
    with tempfile.TemporaryDirectory(prefix='zemu-ocr-') as staging:
        output = Path(staging) / 'result.json'
        try:
            subprocess.run([str(folder / 'ZemuOCR.exe'), str(path.resolve()), str(output)],
                           cwd=folder, env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
                           creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=True)
            return json.loads(output.read_bytes())
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ComponentError('本机 OCR 未能读取截图。请检查图片是否清晰，并确认已安装微软 Visual C++ x64 运行库。') from exc
