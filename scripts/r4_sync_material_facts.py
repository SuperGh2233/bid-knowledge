"""把「现附上…」材料事实写入 material_facts。

**局限声明（必须随数据一起保留）**：
本切片**只做确定性部分，不判 source**——不知道一条声明是"我方已附材料"还是
"采购人要求/模板"。对抗复核查明：区分二者需要语义判断，未获 LLM 授权前不做。
故本表数据**不得当作"已核实附件"使用**。
详见 docs/material-facts-feasibility.md。
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))
os.environ["BID_AI_CLEAN_DB"] = str(CLEAN / "bid_ai_clean_reg.db")

from app import db as dbm  # noqa: E402
from app import extract as E  # noqa: E402

con = dbm.connect()
before = con.execute("SELECT COUNT(*) FROM material_facts").fetchone()[0]

docs = [dict(r) for r in con.execute(
    "SELECT d.document_id, d.relative_path, a.text FROM documents d "
    "JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id "
    "WHERE d.document_role IN ('our_response','final_signed') AND length(a.text)>2000")]

rows, per_doc = [], []
for d in docs:
    f = E.extract_material_facts(d["text"], d["document_id"])
    if f:
        rows.extend(f)
        per_doc.append((d["relative_path"].rsplit("/", 1)[-1][:56], len(f)))

with con:
    # 幂等：先删这些文档的旧事实再插（同 sync_contract_service_items 的做法）
    for d in docs:
        con.execute("DELETE FROM material_facts WHERE document_id=?", (d["document_id"],))
    for x in rows:
        con.execute(
            "INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
            "VALUES (?,?,?,?)",
            (x["document_id"], x["fact_type"], x["fact_value"], x["evidence_text"]))

after = con.execute("SELECT COUNT(*) FROM material_facts").fetchone()[0]
con.close()

print(f"material_facts {before} → {after}  （{len(per_doc)} 份文档产出）\n")
for name, n in per_doc:
    print(f"  ✔ {name}  {n} 条")
print()
print("类型分布:", dict(Counter(x["fact_type"] for x in rows)))
print()
print("=== 全部抽出的事实 ===")
for x in rows:
    print(f"  [{x['fact_type']:<22}] {x['fact_value']:<24} L{x['line']}")
print()
print("⚠️ 局限：本表**不判 source**（我方附件 vs 采购人要求/模板），不得当作『已核实附件』使用。")
