"""R4 结构化提取批处理：对 D1 的 29 份文件做**本地确定性**提取。

两类提取，都不调用 LLM/Embedding/OCR：
  1) 业绩清单 → LEDGER-* 合同（extract_and_sync）
  2) 合同服务明细 → CTL-* 合同 + contract_items（parse_contract_service_table + sync_contract_service_items）

安全：
  - 只写测试库 bid_ai_clean_reg.db；
  - 只在**解析出真实明细行**时才建 CTL，避免空合同污染检索；
  - NAS 只读；全程不调用任何外部网关。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))
os.environ["BID_AI_CLEAN_DB"] = str(CLEAN / "bid_ai_clean_reg.db")

from app import db as db_mod  # noqa: E402
from app import extract as E  # noqa: E402

checklist = json.load(io.open(CLEAN / "data" / "d1_checklist.json", encoding="utf-8"))
print(f"D1 文件：{len(checklist)} 份\n")

db = Path(os.environ["BID_AI_CLEAN_DB"])
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", "拒绝写非测试库"

con = db_mod.connect()
art = {r["canonical_document_id"]: dict(r) for r in con.execute(
    "SELECT canonical_document_id, sha256, text, parser_version FROM parse_artifacts")}

before = {
    "contracts": con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0],
    "items": con.execute("SELECT COUNT(*) FROM contract_items").fetchone()[0],
}

ledger_stats, ctl_rows = Counter(), []
for i, rec in enumerate(checklist, 1):
    doc_id = rec["document_id"]
    row = con.execute("SELECT canonical_document_id, relative_path FROM documents WHERE document_id=?",
                      (doc_id,)).fetchone()
    if not row or not row[0] or row[0] not in art:
        continue
    a = art[row[0]]
    text = a["text"] or ""
    # 1) 业绩清单
    try:
        st = E.extract_and_sync(con, doc_id, text)
        ledger_stats[st.get("status", "?")] += 1
    except Exception as exc:  # noqa: BLE001
        ledger_stats[f"异常:{type(exc).__name__}"] += 1
    # 2) 合同服务明细（**只在解析出明细行时才建 CTL**）
    try:
        records = E.parse_contract_service_table(text)
        details = [r for r in records if r.get("row_type") in ("detail", "product_subtotal")]
        if details:
            # **必须传 native_text / filename**：合同编号、甲乙方、合同总额、合同日期
            # 都从正文/文件名取。不传 → 这些字段一律 None，且（旧写法 COALESCE 下）
            # 已提取的值永远清不掉。文件名以 documents.relative_path 为唯一来源。
            out = E.sync_contract_service_items(con, doc_id, records,
                                                source_sha256=a["sha256"],
                                                parser_version=a["parser_version"],
                                                native_text=text,
                                                filename=row[1].rsplit("/", 1)[-1])
            ctl_rows.append((i, rec["file"][:44], len(details), out.get("contract_total")))
    except Exception as exc:  # noqa: BLE001
        ctl_rows.append((i, rec["file"][:44], -1, f"异常:{type(exc).__name__}"))

after = {
    "contracts": con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0],
    "items": con.execute("SELECT COUNT(*) FROM contract_items").fetchone()[0],
}
con.close()

print("=== 业绩清单提取（LEDGER）===")
for k, v in ledger_stats.most_common():
    print(f"  {k:<20} {v}")
print(f"\n=== 合同服务明细提取（CTL）——仅列出解析出明细的文件 ===")
if ctl_rows:
    for i, f, n, tot in ctl_rows:
        print(f"  #{i:<3} 明细{n:>4}行 合同总额={tot}  {f}")
else:
    print("  （无）")
print(f"\ncontracts {before['contracts']} → {after['contracts']}   "
      f"contract_items {before['items']} → {after['items']}")
