"""把 LLM 分类出的「我方已附材料」写入 material_facts。

约束：`material_facts.fact_type` 的枚举是
  finance_period | social_security_month | instrument | purchase_contract | invoice | instrument_photo
**没有「资质」类**。故只写能映射到这 6 个值的；其余（资质证书等）如实不写，
待 schema 扩展后再处理——**不硬塞进不合适的枚举值**。
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
from app.extract import extract_period  # noqa: E402

MAP = [("社会保险", "social_security_month"), ("社会保障", "social_security_month"),
       ("社保", "social_security_month"),
       ("发票", "invoice"), ("采购合同", "purchase_contract"),
       # ⚠️ 「照片」类**必须排在 `仪器`/`设备` 之前**：MAP 取第一个命中的关键词，
       # 而 `（1）代谢类产品仪器照片` 同时含「仪器」和「仪器照片」——排在后面会被
       # 归成 `instrument`，`instrument_photo` 则**永远产不出**（实测：该枚举曾 0 产出）。
       ("仪器照片", "instrument_photo"), ("设备照片", "instrument_photo"),
       ("仪器实拍", "instrument_photo"), ("设备实拍", "instrument_photo"),
       ("仪器", "instrument"), ("设备", "instrument"), ("测序仪", "instrument"),
       ("财务", "finance_period"), ("审计", "finance_period"), ("纳税", "finance_period"),
       ("税收", "finance_period"), ("资信", "finance_period"),
       # qualification（2026-09-11 新增枚举值，见 app/db.py 注释与计划 §5.4）
       ("资质", "qualification"), ("证书", "qualification"), ("执照", "qualification"),
       ("认证", "qualification"), ("许可", "qualification"), ("著作权", "qualification"),
       ("体系", "qualification"), ("荣誉", "qualification")]

res = json.load(io.open(CLEAN / "data" / "material_classified.json", encoding="utf-8"))
rows, unmapped = [], []
for x in res:
    k = next((v for kw, v in MAP if kw in x["text"]), None)
    (rows if k else unmapped).append({**x, "fact_type": k} if k else x)

# ⚠️ **执行守卫**（2026-09-18 加）：本文件原先**模块级直接跑主流程**，`import` 一下就会写库。
# 起因：为读一个纯函数而 importlib 执行了本文件 → 重跑了一遍抽取（幂等无损坏，但形状危险）。
# 本脚本**只能作为脚本跑**（`python scripts/xxx.py`）；要复用其中的函数请先加守卫。
def _main() -> int:
    con = dbm.connect()
    before = con.execute("SELECT COUNT(*) FROM material_facts").fetchone()[0]
    docs = [r[0] for r in con.execute("SELECT document_id FROM documents")]
    with con:
        for doc_id in docs:                      # 幂等：先删后插
            con.execute("DELETE FROM material_facts WHERE document_id=?", (doc_id,))
        for x in rows:
            # fact_value：从条目行里抽期间/年度（**确定性、本地**）。
            # 抽不到留 None —— 多数资质证书行本就不含期间，不臆造。
            con.execute("INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
                        "VALUES (?,?,?,?)",
                        (x["document_id"], x["fact_type"], extract_period(x["text"]), x["text"][:200]))
    after = con.execute("SELECT COUNT(*) FROM material_facts").fetchone()[0]
    con.close()

    print(f"material_facts {before} → {after}")
    print("类型分布:", dict(Counter(x["fact_type"] for x in rows)))
    print()
    print(f"映射不上、未写入的 {len(unmapped)} 条（多为资质证书，枚举里无对应值）：")
    for x in unmapped[:14]:
        print("   ·", x["text"][:64])

if __name__ == "__main__":
    raise SystemExit(_main())
