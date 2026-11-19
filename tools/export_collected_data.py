"""Archive a local radar SQLite database as gzipped JSONL files, one per table.

The input database is opened read-only. The output contains all table data.
Keep the resulting files in a private local directory.

Usage from the source checkout:
    python tools/export_collected_data.py --out data/exports/local
    python tools/export_collected_data.py --db path/to/radar.db --out data/exports/other
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path

# Tables that are bookkeeping for a single machine and carry no collected data.
SKIP_PREFIXES = ("sqlite_",)


def default_db() -> Path:
    source_db = Path(__file__).resolve().parents[1] / "data" / "radar.db"
    return Path(os.getenv("RADAR_PRIVATE_RC_DB_PATH", str(source_db)))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export(db_path: Path, out_dir: Path) -> dict:
    db_path = db_path.expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(f"数据库不存在：{db_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    tables = [name for name in tables if not name.startswith(SKIP_PREFIXES)]

    if any(not name.isidentifier() for name in tables):
        conn.close()
        raise ValueError("无法将非标准表名用作归档文件名")

    manifest = {
        "exported_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_database": db_path.name,
        "source_size_bytes": db_path.stat().st_size,
        "source_modified_at": dt.datetime.fromtimestamp(db_path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        "format": "每张表一个 <table>.jsonl.gz，UTF-8，每行一条记录（JSON 对象）",
        "tables": {},
        "total_rows": 0,
        "total_bytes": 0,
    }
    for table in tables:
        target = out_dir / f"{table}.jsonl.gz"
        rows = 0
        with gzip.open(target, "wt", encoding="utf-8", compresslevel=6) as handle:
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                rows += 1
        size = target.stat().st_size
        manifest["tables"][table] = {"rows": rows, "bytes": size, "sha256": sha256(target)}
        manifest["total_rows"] += rows
        manifest["total_bytes"] += size
        print(f"{table:32} {rows:6} rows {size / 1024:9.1f} KB")
    conn.close()
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"合计 {manifest['total_rows']} 行，{manifest['total_bytes'] / 1024 / 1024:.2f} MB（gzip）")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_db())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    export(args.db, args.out)


if __name__ == "__main__":
    main()
