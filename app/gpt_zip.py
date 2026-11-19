"""Read-only ZIP expansion for user-selected GPT JSON result files.

Each extracted result is still validated by its existing dry-run import API.
No ZIP entry is executed, persisted or implicitly treated as an approved result.
"""
from __future__ import annotations

import io
import json
import zipfile

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/gpt-import", tags=["Manual GPT result import"])
MAX_ZIP = 16 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 80
MAX_SINGLE = 8 * 1024 * 1024


@router.post("/expand-zip")
async def expand_json_zip(request: Request):
    # Limit request body before parsing untrusted ZIP metadata or inflating any entry.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_ZIP:
            raise HTTPException(413, "ZIP 文件超过 16 MB，请拆分导入")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        raise HTTPException(422, "不是有效的 ZIP 文件") from None
    with archive:
        members = [item for item in archive.infolist()
                   if not item.is_dir()
                   and item.filename.lower().endswith(".json")
                   and item.filename.replace("\\", "/").split("/")[-1].lower()
                   not in ("manifest.json", "batch-manifest.json")
                   and not item.filename.startswith("__MACOSX/")]
        if not members:
            raise HTTPException(422, "ZIP 内没有 AI 结果 JSON")
        if len(members) > MAX_FILES:
            raise HTTPException(413, "ZIP 内 JSON 超过 80 个，请拆分导入")
        if any(item.file_size > MAX_SINGLE for item in members) or sum(
                item.file_size for item in members) > MAX_TOTAL:
            raise HTTPException(413, "ZIP 内解压内容过大，请拆分导入")
        entries = []
        for item in members:
            if item.flag_bits & 1:
                raise HTTPException(422, "不支持加密 ZIP 文件")
            try:
                with archive.open(item) as handle:
                    raw = handle.read(MAX_SINGLE + 1)
                if len(raw) > MAX_SINGLE:
                    raise HTTPException(413, "ZIP 内单个 JSON 超过 8 MB")
                parsed = json.loads(raw.decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError, OSError):
                entries.append({"name": item.filename, "error": "不是有效 JSON 或文件读取失败"})
                continue
            entries.append({"name": item.filename, "data": parsed})
        return {"entries": entries}
