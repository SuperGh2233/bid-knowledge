"""把**业绩清单**抽取重跑到全部已解析的响应文件（PLAN-20260916-track-record-search §9.1）。

背景：`app/extract.py` 的业绩清单抽取（`extract_and_sync` → `LEDGER-*` 合同）从 R4 就实现了，
但 `scripts/r4_extract_all.py` 的范围写死为 `data/d1_checklist.json`（D1 的 29 份），
于是全库只产出 **29 行 / 4 份文件**。本脚本把同一抽取器跑遍**全部已解析的
`our_response` / `final_signed`** 文档，**不改任何抽取规则**。

实测预期（2026-09-16 离线预演，见计划 §5）：表头命中的文档 4 → **16 份**、行数 29 → **约 384 行**；
227 份含业绩表的候选里只有 16 份能命中表头锚点 —— **扩锚点不在本次范围**（计划 §4 Non-goals）。

安全：
  - 只写测试库 `bid_ai_clean_reg.db`（断言挡死正式库）；改前自动备份；
  - NAS 只读；**不调用任何外部网关**（纯本地确定性正则）；
  - 幂等可重跑：抽取器的状态语义保证「未命中表头不得清空」「同输入重放保留仍有效的子记录」。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))
os.environ.setdefault("BID_AI_CLEAN_DB", str(CLEAN / "bid_ai_clean_reg.db"))

import app.db as db_mod  # noqa: E402
import app.extract as E  # noqa: E402

DB = Path(os.environ["BID_AI_CLEAN_DB"])
assert DB.name.endswith("_reg.db") and DB.name != "bid_ai_clean.db", f"拒绝写非测试库：{DB}"
assert DB.exists(), f"库不存在：{DB}"

ROLES = ("our_response", "final_signed")


def main() -> int:
    bak = DB.with_name(f"{DB.stem}.bak-track-{time.strftime('%Y%m%d-%H%M%S')}.db")
    shutil.copy2(DB, bak)
    print(f"写前备份：{bak.name}（{bak.stat().st_size/1024/1024:.1f} MB）\n")

    con = db_mod.connect()
    con.row_factory = sqlite3.Row

    before_rows = con.execute(
        "SELECT COUNT(*) n FROM contracts WHERE contract_id LIKE 'LEDGER-%'").fetchone()["n"]
    before_docs = con.execute(
        "SELECT COUNT(DISTINCT document_id) n FROM contracts WHERE contract_id LIKE 'LEDGER-%'"
    ).fetchone()["n"]

    targets = list(con.execute(f"""
        SELECT d.document_id, d.relative_path, pa.text
        FROM documents d
        JOIN parse_artifacts pa ON pa.canonical_document_id = d.canonical_document_id
        WHERE d.document_role IN {ROLES}
        ORDER BY d.document_id
    """))
    print(f"目标：已解析的 {'/'.join(ROLES)} 文档 {len(targets)} 份\n")

    stats: Counter = Counter()
    for i, row in enumerate(targets, 1):
        try:
            st = E.extract_and_sync(con, row["document_id"], row["text"] or "")
            stats[st.get("status", "?")] += 1
        except Exception as exc:  # noqa: BLE001 —— 单份失败不中断整批，逐份记账
            stats[f"异常:{type(exc).__name__}"] += 1
        if i % 200 == 0:
            print(f"  …{i}/{len(targets)}")

    con.commit()

    after_rows = con.execute(
        "SELECT COUNT(*) n FROM contracts WHERE contract_id LIKE 'LEDGER-%'").fetchone()["n"]
    after_docs = con.execute(
        "SELECT COUNT(DISTINCT document_id) n FROM contracts WHERE contract_id LIKE 'LEDGER-%'"
    ).fetchone()["n"]
    with_amt = con.execute("""SELECT COUNT(*) n FROM contracts
        WHERE contract_id LIKE 'LEDGER-%' AND total_amount IS NOT NULL""").fetchone()["n"]
    with_party = con.execute("""SELECT COUNT(*) n FROM contracts
        WHERE contract_id LIKE 'LEDGER-%' AND COALESCE(party_a,'') <> ''""").fetchone()["n"]

    print("\n=== 逐状态统计 ===")
    for k, v in stats.most_common():
        print(f"  {k:<28} {v}")

    print("\n=== LEDGER 行（前 → 后）===")
    print(f"  行数      {before_rows:>4} → {after_rows}")
    print(f"  来源文件  {before_docs:>4} → {after_docs}")
    print(f"  有采购人  {with_party} / {after_rows}")
    print(f"  有金额    {with_amt} / {after_rows}")
    print("\n⚠️ 后续（计划 §9.2–9.5）：业绩行**独立闸 + 单列来源标签**进入检索；"
          "**不参与金额过滤**、**不并入 approved_documents.json**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
