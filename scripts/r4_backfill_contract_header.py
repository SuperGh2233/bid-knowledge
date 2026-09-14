"""R4 合同头字段回填：把 contracts 的 编号／甲方／乙方／合同日期／合同总额 重算为目标状态。

**纯本地**：只读 `parse_artifacts` 里已存的正文 + `documents.relative_path`；
不碰 NAS、不做 OCR、不调用任何 LLM/Embedding 网关。**可反复重跑（幂等）**。

为什么需要它：合同头字段此前是用**临时命令**回填的——仓库里没有任何脚本能重建它们，
所以抽错了也修不掉、`r4_extract_ocr.py` 重跑还会因不传上下文而写不进这些字段。
配套修复：`sync_contract_service_items` 的 `_snap()` 快照语义（有上下文即覆盖，
取不到写 None），旧写法 `contract_number=COALESCE(?, contract_number)` 会让
误抓值永远无法被修正（实测 `party_a='供方（乙方）'`）。

安全：只写测试库 `bid_ai_clean_reg.db`；写前自动备份；无明细的文件**跳过不写**（不清空 items）。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))
os.environ["BID_AI_CLEAN_DB"] = str(CLEAN / "bid_ai_clean_reg.db")

from app import db as db_mod  # noqa: E402
from app import extract as E  # noqa: E402

DB = Path(os.environ["BID_AI_CLEAN_DB"])
assert DB.name.endswith("_reg.db") and DB.name != "bid_ai_clean.db", "拒绝写非测试库"

FIELDS = ("contract_number", "party_a", "party_b", "contract_date", "total_amount")


def _backup() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = DB.with_name(f"{DB.stem}.bak-hdrbackfill-{stamp}.db")
    shutil.copy2(DB, dst)
    return dst


bak = _backup()
print(f"已备份 → {bak.name}\n" + "=" * 92)

con = db_mod.connect()
rows = [dict(r) for r in con.execute(
    "SELECT c.contract_id, c.contract_number, c.party_a, c.party_b, c.contract_date,"
    "       c.total_amount, d.document_id, d.relative_path,"
    "       a.text, a.sha256, a.parser_version "
    "FROM contracts c JOIN documents d ON d.document_id = c.document_id "
    "LEFT JOIN parse_artifacts a ON a.canonical_document_id = d.canonical_document_id "
    "WHERE c.contract_id LIKE 'CTL-%' ORDER BY c.contract_id")]

print(f"CTL 合同 {len(rows)} 份\n")
t0 = time.time()
stats, changes, skipped = Counter(), [], []

for i, r in enumerate(rows, 1):
    name = r["relative_path"].rsplit("/", 1)[-1]
    if not r["text"]:
        skipped.append((name, "无 parse_artifact 正文"))
        stats["跳过:无正文"] += 1
        continue
    records = E.parse_contract_service_table(r["text"])
    details = [x for x in records if x.get("row_type") in ("detail", "product_subtotal")]
    if not details:
        # 不清空 items：无明细就整条跳过（宁可不动，也不把已入库明细删掉）
        skipped.append((name, "重解析无服务明细"))
        stats["跳过:无明细"] += 1
        continue

    before = {k: r[k] for k in FIELDS}
    E.sync_contract_service_items(con, r["document_id"], records,
                                  source_sha256=r["sha256"], parser_version=r["parser_version"],
                                  native_text=r["text"], filename=name)
    con.commit()
    after = dict(con.execute(
        "SELECT contract_number, party_a, party_b, contract_date, total_amount "
        "FROM contracts WHERE contract_id=?", (r["contract_id"],)).fetchone())
    stats["已回填"] += 1
    for k in FIELDS:
        if str(before[k]) != str(after[k]):
            changes.append({"file": name, "field": k, "before": before[k], "after": after[k]})

con.close()
print(f"=== 结果：{dict(stats)}  用时 {time.time()-t0:.0f}s ===\n")

if changes:
    print(f"字段变化 {len(changes)} 处：")
    for c in changes:
        print(f"  [{c['field']}] {c['before']!r} → {c['after']!r}")
        print(f"        {c['file'][:78]}")
else:
    print("无字段变化（已是目标状态）")

if skipped:
    print(f"\n跳过 {len(skipped)} 份：")
    for f, why in skipped[:20]:
        print(f"  · {why:<18} {f[:66]}")

# 回填后复查：填充率
con = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row
print("\n=== 回填后 CTL 字段填充率 ===")
total = con.execute("SELECT COUNT(*) FROM contracts WHERE contract_id LIKE 'CTL-%'").fetchone()[0]
for f in FIELDS:
    n = con.execute(f"SELECT COUNT(*) FROM contracts WHERE contract_id LIKE 'CTL-%' "
                    f"AND {f} IS NOT NULL AND {f}!=''").fetchone()[0]
    print(f"  {f:<18} {n:>3}/{total}")
con.close()

io.open(CLEAN / "data" / "hdr_backfill.json", "w", encoding="utf-8").write(
    json.dumps({"stats": dict(stats), "changes": changes,
                "skipped": [{"file": f, "why": w} for f, w in skipped]},
               ensure_ascii=False, indent=1))
print(f"\n报告 → data/hdr_backfill.json")
