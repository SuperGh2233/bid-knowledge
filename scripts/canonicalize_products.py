"""回填 `contract_items.product_canonical`（产品族归一，可重跑）。

背景（2026-09-13 实测）：该列**全库 597/597 为 NULL**，导致同一产品跨合同写法不一时无法汇总 ——
`product_raw` 有 405 个不同取值，但那是「类别/服务名」原始串，不是「哪个产品」的粒度。
归一后 **405 → 约 60 个产品族**（单细胞 vs 空间转录组等**仍分开**，不会被并成一个平台）。

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
from app.extract import canonical_product  # noqa: E402

db = Path(config.DB_PATH)
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", f"拒绝写非测试库：{db}"

DRY = "--apply" not in sys.argv

con = db_mod.connect()
rows = list(con.execute("SELECT item_id, product_raw, product_canonical FROM contract_items"))
before = Counter(r["product_canonical"] for r in rows)
plan = []
for r in rows:
    canon = canonical_product(r["product_raw"])
    if canon != r["product_canonical"]:
        plan.append((r["item_id"], canon, r["product_raw"]))

raw_distinct = len({r["product_raw"] for r in rows if r["product_raw"]})
canon_distinct = len({canonical_product(r["product_raw"]) for r in rows} - {None})
null_canon = sum(1 for _i, c, _p in plan if c is None)

print(f"contract_items {len(rows)} 行")
print(f"  product_canonical 改前非空: {sum(v for k, v in before.items() if k)}")
print(f"  需更新 {len(plan)} 行（其中归为「未识别出产品名」{null_canon} 行 —— 噪声行，非「无产品」）")
print(f"  product_raw 去重 {raw_distinct} → product_canonical 去重 {canon_distinct}")
print()
print("=== 归一结果 top15 ===")
for k, v in Counter(canonical_product(r["product_raw"]) for r in rows if
                    canonical_product(r["product_raw"])).most_common(15):
    print(f"  {v:>4}  {k[:58]}")
if null_canon:
    print(f"\n=== 归为「未识别」（噪声行）的原始串样例 ===")
    for _i, c, p in plan:
        if c is None:
            print(f"  {p!r}")

if DRY:
    print("\n[dry-run] 未写库。加 --apply 生效。")
else:
    with con:
        for item_id, canon, _p in plan:
            con.execute("UPDATE contract_items SET product_canonical=? WHERE item_id=?", (canon, item_id))
    n = con.execute("SELECT COUNT(*) FROM contract_items WHERE product_canonical IS NOT NULL").fetchone()[0]
    print(f"\n✅ 已回填；product_canonical 非空 {n}/{len(rows)} 行")
con.close()
