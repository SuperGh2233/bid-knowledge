"""解析**本身就有文字层**的 PDF（纯本地，零外发）。

背景：未解析的我方响应/最终版里，598 份是 pdf/jpg/png。抽样 40 份显示其中 **20% 的 PDF
本来就有文字层** —— 这些**不需要 OCR**，本地用 fitz 就能抽。

与 `r5_parse_more.py`（docx）互补；纯扫描件**跳过**，留给已授权的 `ocr_batch.py`。

安全：NAS 只读；只写测试库；不 import OCR、不调用任何网关。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["BID_AI_CLEAN_DB"] = str(BASE / "bid_ai_clean_reg.db")

import app.config as config  # noqa: E402
from app import db as db_mod  # noqa: E402
from app import parser as parser_mod  # noqa: E402

ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 200
MIN_TEXT = 500          # 文字层总字数下限（低于此视为扫描件）

db = Path(config.DB_PATH)
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", f"拒绝写非测试库：{db}"

con = db_mod.connect()
# ⚠️ 同 r5_parse_more.py：角色集取自 app/parser.py::PARSE_SET_ROLES，不得写死。
# 2026-09-13 实测：写死 3 角色会让扩容停在声明层，需求一三类材料的正文读不出来。
PARSE_ROLES = tuple(parser_mod.PARSE_SET_ROLES)
_ROLE_PH = ",".join("?" * len(PARSE_ROLES))
rows = [dict(r) for r in con.execute(
    "SELECT document_id, source_root_id, project_folder, relative_path, document_role, "
    "       parse_status, manual_override, canonical_document_id, file_ext, sha256 "
    "FROM documents d WHERE d.document_role IN (" + _ROLE_PH + ") "
    "  AND canonical_document_id IS NULL "
    # 同 r5_parse_more.py：扩容角色的状态是 NULL（已登记未入队），必须一并收。
    "  AND (parse_status IS NULL OR parse_status='pending') "
    "  AND lower(relative_path) LIKE '%.pdf' ORDER BY file_size DESC", PARSE_ROLES)]

import fitz  # noqa: E402

native, scan, bad = [], 0, 0
for r in rows:
    root = ROOTS.get(r["source_root_id"])
    p = root.joinpath(*r["relative_path"].split("/")) if root else None
    if not (p and p.exists()):
        bad += 1
        continue
    try:
        d = fitz.open(str(p))
        total = sum(len((d[i].get_text() or "").strip()) for i in range(d.page_count))
        d.close()
    except Exception:  # noqa: BLE001
        bad += 1
        continue
    if total >= MIN_TEXT:
        native.append(r)
    else:
        scan += 1

print(f"未解析 .pdf 共 {len(rows)} 份 → **有文字层 {len(native)} 份**（本地可解析）／"
      f"纯扫描 {scan} 份（留给 OCR）／不可读 {bad} 份")
print("-" * 78)

done = err = 0
for i, r in enumerate(native[:LIMIT], 1):
    res = parser_mod.process_native(con, r, ROOTS)
    if res.error:
        err += 1
        print(f"{i:3}. [ERR] {r['relative_path'].rsplit('/', 1)[-1][:52]}  {res.error[:60]}")
    else:
        done += 1
        n = res.chars if res.chars is not None else -1
        print(f"{i:3}. [OK ] chars={n:>8,}  {r['relative_path'].rsplit('/', 1)[-1][:50]}")
con.close()
print("-" * 78)
print(f"成功 {done} 份，失败 {err} 份（纯本地，未调用任何网关）")
