"""从「合同对应付款凭证」台账提取收款凭证记录，并与 contracts 做归一匹配。

台账列（实测 `合同对应付款凭证.xlsx`）：
  序号 | 用户 | 项目 | 服务 | 合同有效金额(万元) | 签订日期 | 用户联系人 | 合同号 | 发票号 | 付款时间 | 收款人

「是否有收款凭证」的判据（**如实取自台账，不推断**）：
  · 收款人 = 「未到款」 → 无收款凭证
  · 付款时间 非空        → 有收款凭证（附付款时间）
  · 两者都没有           → 台账未记，标 unknown（**不猜**）

合同号匹配用**归一化**：实测 OCR 会把字母 O 读成数字 0（`ZOE…` vs `Z0E…`），
故归一掉 O/0、I/1、L/1 的差异 —— 实测靠这一步救回 2 份可关联合同。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["BID_AI_CLEAN_DB"] = str(BASE / "bid_ai_clean_reg.db")

import app.config as config  # noqa: E402
from app import db as db_mod  # noqa: E402

db = Path(config.DB_PATH)
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", f"拒绝写非测试库：{db}"

OUT = BASE / "data" / "payment_vouchers.json"


def unquote(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == "'" and s[-1] == "'" else s


def norm_num(s: str) -> str:
    """合同号归一：去非字母数字 + 统一 O/I/L 与 0/1 的 OCR 混淆。"""
    s = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    return s.replace("O", "0").replace("I", "1").replace("L", "1")


con = db_mod.connect()
files = list(con.execute(
    """SELECT d.document_id, d.relative_path, a.text FROM documents d
 JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id
 WHERE d.relative_path LIKE '%付款凭证%' AND length(a.text)>0"""))

records, seen = [], set()
# ⚠️ 单元格分隔符实测有两级：**行内**是 ` | `，**行间**是 `\n`。
# 原正则 lookahead 只写了 `(?=\s*\|\s*Sheet1!|$)` 且**没有 re.M** → `$` 只在全文末尾成立，
# 于是「每一行的最后一格」既不匹配 `|` 也不匹配 `$`，被系统性丢弃。
# 而 K 列（收款人，`未到款` 信号所在）恰好就是每行最后一格 —— 实测 K 列 11 格里只抓到 1 格，
# 直接导致 `未到款` 漏报（真值 no=3 被写成 no=1）。修法：lookahead 补上换行分支 + 加 re.M。
CELL_RE = re.compile(r"Sheet1!([A-Z]+)(\d+)=(.+?)(?=\s*\|\s*Sheet1!|\s*\n\s*Sheet1!|$)", re.M)
for f in files:
    cells: dict[str, str] = {}
    for m in CELL_RE.finditer(f["text"]):
        cells[f"{m.group(1)}{m.group(2)}"] = unquote(m.group(3))
    by_row: dict[int, dict] = {}
    for k, v in cells.items():
        col = re.match(r"[A-Z]+", k).group(0)
        rn = int(re.search(r"(\d+)$", k).group(1))
        by_row.setdefault(rn, {})[col] = v
    for rn in sorted(k for k in by_row if k >= 3):
        row = by_row[rn]
        num = (row.get("H") or "").strip()
        if not num:
            continue
        pay_time = (row.get("J") or "").strip()
        payee = (row.get("K") or "").strip()
        key = (num.upper(), row.get("C", ""), row.get("E", ""))
        if key in seen:
            continue
        seen.add(key)
        if "未到款" in payee:
            has = "no"
        elif pay_time:
            has = "yes"
        else:
            has = "unknown"          # 台账未记 → 不猜
        records.append({
            "contract_number": num, "project": (row.get("C") or "").strip(),
            "service": (row.get("D") or "").strip(),
            "amount_wan": (row.get("E") or "").strip(),
            "sign_date": (row.get("F") or "").strip(),
            "invoice_no": (row.get("I") or "").strip(),
            "pay_time": pay_time, "payee": payee, "has_payment": has,
            "source_file": f["relative_path"].rsplit("/", 1)[-1],
            "source_document_id": f["document_id"],
        })

# —— 与 contracts 归一匹配 ——
ct = {norm_num(r[0]): r[0] for r in con.execute(
    "SELECT contract_number FROM contracts WHERE contract_number IS NOT NULL AND contract_number != ''")}
matched = 0
for rec in records:
    hit = ct.get(norm_num(rec["contract_number"]))
    rec["matched_contract_number"] = hit
    if hit:
        matched += 1
con.close()

OUT.write_text(json.dumps(
    {"note": "收款凭证台账（源：合同对应付款凭证.xlsx）。has_payment 如实取自台账："
             "收款人含「未到款」→no；有付款时间→yes；都没有→unknown（不猜）。",
     "count": len(records), "matched": matched, "records": records},
    ensure_ascii=False, indent=1), encoding="utf-8")

print(f"提取收款凭证记录: {len(records)} 条")
print(f"  归一匹配上 contracts 的: {matched} 条")
print()
from collections import Counter
print("  收款状态分布:", dict(Counter(r["has_payment"] for r in records)))
print()
print("=== 记录样例 ===")
for r in records[:8]:
    print(f"  {r['contract_number']:<16} {r['project'][:18]:<20} {r['amount_wan']:>6}万 "
          f"收款={r['has_payment']:<8} 匹配={r['matched_contract_number'] or '—'}")
print(f"\n报告 → {OUT}")
