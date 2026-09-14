"""批量入库：对已 OCR 的 contract_evidence 提取服务明细 → contracts/contract_items。

守卫：**只在解析出 detail 行时才建 CTL**（此前盲目建 CTL 曾造成 789 行垃圾并回滚）。
本地确定性提取，不调用任何外部网关。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))
os.environ["BID_AI_CLEAN_DB"] = str(CLEAN / "bid_ai_clean_reg.db")

from app import db as db_mod  # noqa: E402
from app import extract as E  # noqa: E402

con = db_mod.connect()
before = {"c": con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0],
          "i": con.execute("SELECT COUNT(*) FROM contract_items").fetchone()[0]}

docs = [dict(r) for r in con.execute(
    "SELECT d.document_id, d.relative_path, a.text, a.sha256, a.parser_version "
    "FROM documents d JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id "
    "WHERE d.document_role='contract_evidence' AND a.content_format='scanned_ocr' "
    "ORDER BY d.document_id")]

print(f"待处理 OCR 合同 {len(docs)} 份\n" + "=" * 90)
t0 = time.time()
made, nodetail, invoice_like = [], [], []
for i, d in enumerate(docs, 1):
    text = d["text"] or ""
    records = E.parse_contract_service_table(text)
    details = [x for x in records if x["row_type"] in ("detail", "product_subtotal")]
    name = d["relative_path"].rsplit("/", 1)[-1]
    if not details:
        (invoice_like if ("发票" in name or name.lower().startswith(("dzfp", "fp"))) else nodetail).append(name)
        continue
    # **必须传 native_text / filename**：合同编号、甲乙方、合同总额、合同日期
    # 都从正文/文件名取；不传则这些字段一律 None。
    out = E.sync_contract_service_items(con, d["document_id"], records,
                                        source_sha256=d["sha256"], parser_version=d["parser_version"],
                                        native_text=text, filename=name)
    made.append({"file": name, "details": len(details), "total": out.get("contract_total"),
                 "chars": len(text), "sha": d["sha256"][:16], "document_id": d["document_id"]})
con.close()

after = {"c": 0, "i": 0}
con = sqlite3.connect((CLEAN / "bid_ai_clean_reg.db").resolve().as_uri(), uri=True)
after["c"] = con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
after["i"] = con.execute("SELECT COUNT(*) FROM contract_items").fetchone()[0]
con.close()

made.sort(key=lambda x: -x["details"])
print(f"\n=== 建 CTL 的合同：{len(made)} 份 ===")
for m in made[:25]:
    print(f"  明细{m['details']:>4}行  总额={m['total']}  {m['file'][:62]}")
if len(made) > 25:
    print(f"  ... 其余 {len(made)-25} 份")
print(f"\n=== 无服务明细：{len(nodetail)} 份（发票类 {len(invoice_like)}）===")
for f in nodetail[:6]:
    print(f"  · {f[:70]}")
print(f"\ncontracts {before['c']} → {after['c']}   contract_items {before['i']} → {after['i']}")
print(f"用时 {time.time()-t0:.0f}s")
io.open(CLEAN / "data" / "ocr_contracts.json", "w", encoding="utf-8").write(
    json.dumps(made, ensure_ascii=False, indent=1))
