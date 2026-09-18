"""把 LLM 分类的「我方已附材料」**补回** material_facts（不清表）。

背景：`r4_sync_classified_facts.py` 会先 `DELETE FROM material_facts WHERE document_id=?`
遍历**全库所有文档**再插入 —— 直接重跑会把 `extract_three_modules.py` 新提取的
353 条（付款凭证/完税/财务统计表那批）**全部抹掉**。
本脚本只**补写** LLM 分类里尚未入库的条目，不动其他来源。
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["BID_AI_CLEAN_DB"] = str(BASE / "bid_ai_clean_reg.db")

import app.config as config  # noqa: E402
from app import db as db_mod  # noqa: E402
from app.extract import extract_period  # noqa: E402

db = Path(config.DB_PATH)
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", f"拒绝写非测试库：{db}"

MAP = [("社会保险", "social_security_month"), ("社会保障", "social_security_month"),
       ("社保", "social_security_month"),
       ("发票", "invoice"), ("采购合同", "purchase_contract"),
       # 「照片」必须先于「仪器」（取第一个命中，否则 instrument_photo 永远产不出）
       ("仪器照片", "instrument_photo"), ("设备照片", "instrument_photo"),
       ("仪器实拍", "instrument_photo"), ("设备实拍", "instrument_photo"),
       ("仪器", "instrument"), ("设备", "instrument"), ("测序仪", "instrument"),
       ("财务", "finance_period"), ("审计", "finance_period"), ("纳税", "finance_period"),
       ("税收", "finance_period"), ("资信", "finance_period"),
       ("资质", "qualification"), ("证书", "qualification"), ("执照", "qualification"),
       ("认证", "qualification"), ("许可", "qualification"), ("著作权", "qualification"),
       ("体系", "qualification"), ("荣誉", "qualification")]

res = json.load(io.open(BASE / "data" / "material_classified.json", encoding="utf-8"))
# ⚠️ **执行守卫**（2026-09-18 加）：本文件原先**模块级直接跑主流程**，`import` 一下就会写库。
# 起因：为读一个纯函数而 importlib 执行了本文件 → 重跑了一遍抽取（幂等无损坏，但形状危险）。
# 本脚本**只能作为脚本跑**（`python scripts/xxx.py`）；要复用其中的函数请先加守卫。
def _main() -> int:
    con = db_mod.connect()

    # 已存在的 (document_id, fact_type, evidence_text) 去重键 —— 避免重复插入
    existing = {(r[0], r[1], r[2]) for r in con.execute(
        "SELECT document_id, fact_type, evidence_text FROM material_facts")}

    added = skipped = 0
    with con:
        for x in res:
            if x.get("kind") != "our_attachment":
                continue
            kind = next((v for kw, v in MAP if kw in x["text"]), None)
            if not kind:
                skipped += 1
                continue
            ev = x["text"][:200]
            if (x["document_id"], kind, ev) in existing:
                skipped += 1
                continue
            con.execute("INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
                        "VALUES (?,?,?,?)",
                        (x["document_id"], kind, extract_period(x["text"]), ev))
            existing.add((x["document_id"], kind, ev))
            added += 1
    con.close()
    print(f"补回 LLM 分类条目: 新增 {added} 条 / 跳过（已存在或无法映射）{skipped} 条")

    con = db_mod.connect()
    print()
    print("=== material_facts 最终覆盖 ===")
    tot = 0
    for r in con.execute("SELECT fact_type, COUNT(*) n, COUNT(DISTINCT document_id) docs "
                         "FROM material_facts GROUP BY 1 ORDER BY n DESC"):
        tot += r["n"]
        print(f"  {r['fact_type']:<24} {r['n']:>5} 条 / {r['docs']:>3} 份文档")
    print(f"  {'合计':<24} {tot:>5} 条")
    con.close()

if __name__ == "__main__":
    raise SystemExit(_main())
