"""按修好的日期抽取重算 `contracts.contract_date`（只动这一列，可重跑）。

背景（2026-09-13 实测）：98 份 CTL 合同里只有 **15 份**拿到了精确签署日，79 份退化成
「合同编号年月」代理值 —— 而复核发现编号年月与真实签署月**一致率仅约 53%**，
拿它做「2024年12月之后」这类时间过滤会错。

**根因不是模式漏了，是格式漏了**：扫描件 OCR 把签署页日期渲染成 `2024.9.20` / `2023.5.18`
（点分隔），而抽取器只认「YYYY年M月D日」。原文实测：
  `YOE2024090883` → `签订日期：2024.9.20`
  `YOE2023040198` → `日 期：2023.5.18`
  `YLM2024100053` → `日 期： 2025.02.19`（编号年月 2024-10，**差了 4 个月**）
日期一直就在正文里，只是没被读出来。

配套改动：`app/extract.py::extract_contract_date` 增加点/横线分隔模式，
并把「取文末第一个命中」改为**取最晚签署日**（合同自较晚签署日生效；一份合同常有甲乙两个日期）。

安全：NAS 只读；只写测试库；不调用任何网关。
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["BID_AI_CLEAN_DB"] = str(BASE / "bid_ai_clean_reg.db")

import app.config as config  # noqa: E402
from app import db as db_mod  # noqa: E402
from app.extract import extract_contract_date  # noqa: E402

db = Path(config.DB_PATH)
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", f"拒绝写非测试库：{db}"

DRY = "--apply" not in sys.argv

con = db_mod.connect()
rows = list(con.execute("""
    SELECT c.contract_id, c.contract_number, c.contract_date, a.text
    FROM contracts c
    JOIN documents d ON d.document_id = c.document_id
    LEFT JOIN parse_artifacts a ON a.canonical_document_id = d.canonical_document_id
    WHERE c.contract_id LIKE 'CTL-%'"""))

before, after = Counter(), Counter()
changes = []
for r in rows:
    old = (r["contract_date"] or "")
    new, src = extract_contract_date(r["text"] or "", r["contract_number"] or "")
    new = new or ""
    before["完整日" if len(old) == 10 else "仅年月" if len(old) == 7 else "空"] += 1
    after["完整日" if len(new) == 10 else "仅年月" if len(new) == 7 else "空"] += 1
    if new != old:
        changes.append((r["contract_id"], r["contract_number"], old, new))

print(f"CTL 合同 {len(rows)} 份")
print(f"  改前精度：{dict(before)}")
print(f"  改后精度：{dict(after)}")
print(f"  需要更新 {len(changes)} 份")
gained = [c for c in changes if len(c[3]) == 10 and len(c[2]) != 10]
lost = [c for c in changes if len(c[2]) == 10 and len(c[3]) != 10]
print(f"    新增精确签署日 {len(gained)} 份 / 精确日变粗 {len(lost)} 份")
for cn, num, o, n in gained[:10]:
    print(f"      {num}: {o or '(空)'} → {n}")

if DRY:
    print("\n[dry-run] 未写库。加 --apply 生效。")
else:
    with con:
        for cid, _num, _o, n in changes:
            con.execute("UPDATE contracts SET contract_date=? WHERE contract_id=?", (n or None, cid))
    print(f"\n✅ 已更新 {len(changes)} 份合同的 contract_date")
con.close()
