"""R5 批次解析：按原生文字量选 12 份跨项目响应文件并解析（本地，不外发）。

选批规则（2026-09-10 用户批准）：
  1. our_response 且 .docx
  2. word/document.xml 未压缩大小 ≥ MIN_XML（≈4 万字符，经农科院/北医三院实测校准）
  3. 同尺寸（±2KB）视为同一文件的版本件/副本，只留一份
  4. 一项目一份，按体积降序取 TARGET 份

安全：NAS 只读；只写测试库；不 import OCR、不调用任何外部网关。

授权依据：用户批准"按原生文字量重筛批次"并"按这 12 份开工"。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

REG_DB = BASE / "bid_ai_clean_reg.db"
os.environ["BID_AI_CLEAN_DB"] = str(REG_DB)

import app.config as config  # noqa: E402
from app import db as db_mod  # noqa: E402
from app import parser as parser_mod  # noqa: E402

SOURCE_ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}
MIN_XML = 930_000      # ≈40,000 字符（RATIO=0.043 反推）
TARGET = 12
SIZE_TOL = 2_000
BATCH_JSON = BASE / "data" / "r5_batch_v1.json"


def guard() -> Path:
    db = Path(config.DB_PATH)
    if db.name == "bid_ai_clean.db" or not db.name.endswith("_reg.db"):
        raise SystemExit(f"拒绝运行：{db} 不是测试库")
    return db


def select(con) -> list[dict]:
    rows = []
    for d in con.execute(
        "SELECT document_id, source_root_id, project_folder, relative_path, document_role, "
        "parse_status, manual_override, canonical_document_id, file_ext, sha256 "
        "FROM documents WHERE document_role='our_response' AND lower(relative_path) LIKE '%.docx'"
    ):
        path = SOURCE_ROOTS[d["source_root_id"]].joinpath(*d["relative_path"].split("/"))
        try:
            with zipfile.ZipFile(path) as z:
                xml = z.getinfo("word/document.xml").file_size
        except Exception:  # noqa: BLE001
            continue
        if xml < MIN_XML:
            continue
        rows.append({**dict(d), "_xml": xml})

    seen_size: set[int] = set()
    dedup = []
    for r in sorted(rows, key=lambda x: -x["_xml"]):
        key = r["_xml"] // SIZE_TOL
        if key in seen_size:
            continue
        seen_size.add(key)
        dedup.append(r)

    picked, seen_proj = [], set()
    for r in dedup:
        if r["project_folder"] in seen_proj:
            continue
        seen_proj.add(r["project_folder"])
        picked.append(r)
        if len(picked) == TARGET:
            break
    return picked


def main() -> int:
    db = guard()
    con = db_mod.connect()
    try:
        batch = select(con)
        BATCH_JSON.parent.mkdir(exist_ok=True)
        BATCH_JSON.write_text(json.dumps(
            [{"document_id": b["document_id"], "project": b["project_folder"],
              "relative_path": b["relative_path"], "xml_bytes": b["_xml"]} for b in batch],
            ensure_ascii=False, indent=1), encoding="utf-8")

        print(f"目标库: {db}")
        print(f"批次  : {len(batch)} 份（跨 {len({b['project_folder'] for b in batch})} 个项目）")
        print(f"清单  : {BATCH_JSON}")
        print("-" * 78)

        stats = parser_mod.ParseStats()
        for i, doc in enumerate(batch, 1):
            res = parser_mod.process_native(con, doc, SOURCE_ROOTS)
            stats.processed += 1
            stats.files_read += res.files_read
            stats.native_parser_calls += int(res.native_parser_called)
            stats.cache_hits += int(res.cache_hit)
            stats.new_artifacts += int(res.new_artifact)
            stats.errors += int(bool(res.error))
            flag = "ERR" if res.error else ("命中" if res.cache_hit else "新解析")
            print(f"{i:2}. [{flag}] {doc['project_folder'][:40]}")
            if res.error:
                print(f"      ! {res.error}")
            elif res.cache_hit:
                print(f"      命中缓存  canonical={str(res.canonical_document_id)[:16]}…")
            else:
                print(f"      chars={res.chars:,}  canonical={str(res.canonical_document_id)[:16]}…")
        print("-" * 78)
        print(f"processed={stats.processed} files_read={stats.files_read} "
              f"parser_calls={stats.native_parser_calls} cache_hits={stats.cache_hits} "
              f"new_artifacts={stats.new_artifacts} errors={stats.errors}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
