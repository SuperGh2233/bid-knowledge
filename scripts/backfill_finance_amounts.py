"""回填 `finance_amount`（凭证「合计金额」）—— 2026-09-17 需求方第二次对接的需求1。

**需求原文**：「社保召回的信息有问题，希望能支持例如『纳税社保总金额』这样更灵活的检索方式」。

**判据（保守，与 `app.extract.find_voucher_total` 同一份实现，不在此重写）**：
只认凭证**自己的合计行**（`金额合计`/`价税合计`/…），且正文必须含税务/社保机构痕迹；
取 `¥` 紧邻数或大写行换行后的数。**不扫全文找数字、不按明细求和**（那是猜，违反宁缺毋滥）。

**实测覆盖面**（2026-09-17 只读复核）：158 份候选文档 → **19 份**可抽出；
全部来自 `qualification_evidence`（完税证明 / 社保完税凭证）。
⚠️ **社保缴费记录表（`社会保险费缴费记录`）没有合计行 → 一律抽不到**，
如实不产出，**不按明细求和**（同一份表里养老/医疗/失业/工伤不可加总成「社保总额」）。

安全：只写 `bid_ai_clean_reg.db`（正式库禁写，脚本层拒绝）；改前备份；NAS 只读；零外发；幂等可重跑。
用法：`python scripts/backfill_finance_amounts.py [--dry-run]`
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
CLEAN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLEAN))
os.environ["BID_AI_CLEAN_DB"] = str(CLEAN / "bid_ai_clean_reg.db")

import app.db as db_mod  # noqa: E402
from app.extract import find_voucher_total  # noqa: E402

DB = Path(os.environ["BID_AI_CLEAN_DB"])
# 红线同 refresh_catalog / backfill_missing_contracts：正式库一律拒绝
assert DB.name.endswith("_reg.db") and DB.name != "bid_ai_clean.db", f"拒绝写非测试库：{DB}"

# 候选：**文件名或正文**命中税务/社保词（两条都要 —— 实测有文件名不显但正文是完税证明的）
PATH_KWS = ["完税", "纳税", "税收", "社保", "缴费", "税", "凭证", "审计", "财务"]
TEXT_KWS = ["税收完税证明", "完税证明", "社会保险费缴费记录", "电子税务局", "税务机关"]
DRY = "--dry-run" in sys.argv


def main() -> int:
    con = db_mod.connect()
    con.row_factory = sqlite3.Row
    path_clause = " OR ".join(["d.relative_path LIKE ?"] * len(PATH_KWS))
    text_clause = " OR ".join(["pa.text LIKE ?"] * len(TEXT_KWS))
    rows = list(con.execute(
        f"""SELECT d.document_id, d.relative_path, d.document_role, pa.text
            FROM documents d JOIN parse_artifacts pa
              ON pa.canonical_document_id = d.canonical_document_id
            WHERE length(pa.text) > 0 AND (({path_clause}) OR ({text_clause}))""",
        [f"%{k}%" for k in PATH_KWS] + [f"%{k}%" for k in TEXT_KWS]))
    print(f"候选文档：{len(rows)} 份")

    found = []
    for r in rows:
        got = find_voucher_total(r["text"] or "")
        if got:
            found.append((r, got[0], got[1]))
    print(f"可抽出「合计金额」：{len(found)} 份")
    for r, amount, how in found:
        print(f"  {amount:>14} ({how})  [{r['document_role']}] "
              f"{r['relative_path'].rsplit('/', 1)[-1][:56]}")
    print("按角色：", dict(Counter(r["document_role"] for r, _, _ in found)))

    if DRY:
        print("\n--dry-run：未写库。")
        con.close()
        return 0

    # 备份名**必须**是 `…bid_ai_clean_reg.bak-<标签>.db` 这一形态 —— `.gitignore` 的
    # `*.bak-*.db` 只匹配这一种；写成 `bid_ai_clean_reg.db.bak-<标签>` 会**逃过忽略规则**
    # （实测：那样命名的备份在 `git status` 里是未跟踪文件，一不小心就提交进库）。
    bak = DB.with_name(f"{DB.stem}.bak-finamt-{time.strftime('%Y%m%d-%H%M%S')}.db")
    shutil.copy2(DB, bak)
    print(f"\n已备份 → {bak.name}")
    n = 0
    with con:
        # 幂等：只清自己这一类（别的类别一行不动 —— 同 extract_three_modules 的教训）
        con.execute("DELETE FROM material_facts WHERE fact_type='finance_amount'")
        for r, amount, how in found:
            role = r["document_role"]
            name = r["relative_path"].rsplit("/", 1)[-1]
            t = r["text"]
            i = t.find(amount)
            ctx = t[max(0, i - 60): i + len(amount) + 20].replace("\n", " ").strip()
            con.execute(
                "INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
                "VALUES (?,?,?,?)",
                (r["document_id"], "finance_amount", amount.replace(",", ""),
                 f"[{role}] {name[:120]}｜合计金额（判据：{how}）：…{ctx}…"))
            n += 1
    con.close()
    print(f"写入 finance_amount：{n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())