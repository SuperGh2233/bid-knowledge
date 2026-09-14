"""只读材料定位 CLI（R4 试点）：python app/cli_search.py --product 单细胞 --min 50000

- 复用 app/search.locate_by_product_amount（金额门槛走 product_amount_status）。
- 只读；不改库/不发模型/不起服务。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.search import locate_by_product_amount  # noqa: E402

DB = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean\bid_ai_clean_reg.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True, help="产品关键词（命中类别）")
    ap.add_argument("--min", type=float, required=True, help="最小金额（元）")
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    results = locate_by_product_amount(con, (args.product,), args.min)
    print(f"query: 产品包含「{args.product}」 且 明细金额 ≥ {args.min:,.0f} 元")
    hits = [r for r in results if r.hit]
    print(f"正式命中 {len(hits)} 条")
    for r in results:
        mark = "HIT" if r.hit else "----"
        print(f"  [{mark}] {r.product}  amount={r.amount}  status={r.amount_status}")
        if r.hit:
            print(f"        项目={r.project_folder}")
            print(f"        文件={r.file_name}")
            print(f"        源路径={r.source_path}")
            print(f"        证据={r.detail_evidence[:140]}")
        else:
            print(f"        来源={r.file_name[:50]}（{r.amount_status}）")
    con.close()


if __name__ == "__main__":
    main()