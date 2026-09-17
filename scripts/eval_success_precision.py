"""R6-05 排序层落地后，「Success@5 / Precision@10」首次可测。

**此前为什么测不了**：本检索是**过滤式精确匹配**（返回全部满足条件的合同），不是排序式 top-N；
且 `LocateResult` 无 score/rank，结果按 `contract_id` 字典序返回 —— 「前 5 / 前 10」无定义。
2026-09-13 补上 R6-05 排序层（`app/search.py::locate_sort_key`：角色 → 格式 → 命中 → 金额 → 签订日），
两项指标才第一次有定义。**口径**（写死在本文件，改口径必须同时改这里）：

- **结果集** = `hit=True` 的记录，按 `locate_sort_key` 排序后的序列（＝页面上按顺序展示的"符合条件"）。
- **Success@5** = 前 5 条里**至少 1 条**相关。
- **Precision@10** = 前 10 条里相关条数 / 前 10 条实际条数（不足 10 条时按实际数，不补零）。

⚠️ **口径局限（必须随数字一起报出）**：金标准是按**更大的旧语料**标的，
本库可检索集更小；若某条命中不在金标准里，无法判定它是不是"其实也相关"，
本脚本一律按**不相关**计 —— 故 Precision 是**下界**，不是精确值。

安全：只读库；不调用任何网关。
"""
from __future__ import annotations

import io
import pathlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(__file__).resolve().parents[1]
# 评测基准（gold）**随仓库走**：2026-09-17 起优先读仓库内 `data/gold/`。
# 原因：R0 快照目录已按用户要求移出工作区归档，硬编码旧路径会让评测**直接跑不起来**
# （实测 FileNotFoundError）—— 退出门槛的评测不能依赖仓库外的目录。
_ARCHIVE_GOLD = pathlib.Path.home() / "Desktop" / "标书文库-旧系统归档" / "bid-ai-r0-snapshot" / "gold"
_CANDIDATES = [CLEAN / "data" / "gold", _ARCHIVE_GOLD, CLEAN.parent / "bid-ai-r0-snapshot" / "gold"]
GOLD = next((p for p in _CANDIDATES if (p / "expected-records-v1.json").exists()), None)
if GOLD is None:
    raise SystemExit("找不到评测基准 expected-records-v1.json；候选路径：" +
                     "；".join(str(p) for p in _CANDIDATES))

sys.path.insert(0, str(CLEAN))

from app.search import locate_by_product_amount  # noqa: E402

sys.path.insert(0, str(CLEAN / "scripts"))
from eval_gold_recall import QUERY_SPEC  # noqa: E402 —— 口径与 Recall 评测共用同一套查询定义

K = 5
N = 10

recs = json.load(io.open(GOLD / "expected-records-v1.json", encoding="utf-8"))["records"]
by_q: dict[str, list[dict]] = defaultdict(list)
for r in recs:
    by_q[r["query_id"]].append(r)

con = sqlite3.connect((CLEAN / "bid_ai_clean_reg.db").resolve().as_uri() + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row
norm = lambda p: (p or "").replace("\\", "/").strip("/").lower()
doc_by_id = {d["document_id"]: d for d in
             con.execute("SELECT document_id, relative_path FROM documents")}
by_rel = {norm(d["relative_path"]): d for d in doc_by_id.values()}

approved = {str(x["document_id"])[:12] for x in
            json.load(io.open(CLEAN / "data" / "approved_documents.json", encoding="utf-8"))
            if x.get("document_id")}
ctl_docs = {r[0] for r in con.execute(
    "SELECT DISTINCT document_id FROM contracts WHERE contract_id LIKE 'CTL-%'")}
queryable = {d["document_id"] for d in by_rel.values()
             if any(d["document_id"].startswith(p) for p in approved) and d["document_id"] in ctl_docs}


def resolve(sp: str):
    s = norm(sp)
    if not s:
        return None
    for rel, d in by_rel.items():
        if s.endswith(rel):
            return d
    return None


rows_out = []
for qid, (kws, thr, dfrom, dto, pexc) in QUERY_SPEC.items():
    exp = by_q.get(qid) or []
    if not exp:
        continue
    rel_docs = set()
    for r in exp:
        d = resolve(r["source_path"])
        if d and d["document_id"] in queryable and r["relevance"] == "relevant":
            rel_docs.add(d["document_id"])
    if not rel_docs:
        continue
    out = locate_by_product_amount(con, kws, thr, date_from=dfrom, date_to=dto, product_exclude=pexc)
    ranked = [r for r in out if r.hit]                 # 展示给用户的"符合条件"序列，已按 R6-05 排序
    top5, top10 = ranked[:K], ranked[:N]
    hits5 = [r for r in top5 if r.document_id in rel_docs]
    hits10 = [r for r in top10 if r.document_id in rel_docs]
    # **必须单列「金标准未标注」的条目**：金标准是按**更大的旧语料**标的（229 条里只有 192 条
    # relevant），本库命中的文件可能**它根本没标**。把这类混进"不相关"会让 Precision 系统性偏低。
    # 所以：既算下界，也把未标注条目**列出来**供人工判 —— 数字和可核对的东西一起给，不各说各话。
    unlabeled = [r for r in top10 if r.document_id not in rel_docs]
    rows_out.append({
        "query": qid, "returned": len(ranked),
        "success_at_5": bool(hits5),
        "p_at_10": (len(hits10) / len(top10)) if top10 else None,
        "n_at_10": len(top10), "rel_at_10": len(hits10),
        "rel_in_corpus": len(rel_docs),
        "unlabeled": [{"contract_number": r.contract_number, "amount": r.amount,
                       "product": (r.product or "")[:36], "file": r.file_name[:56]} for r in unlabeled],
        "top5_files": [r.file_name[:44] for r in top5],
    })
con.close()

print(f"=== Success@{K} / Precision@{N}（口径见文件头；Precision 为**下界**）===\n")
s_ok = 0
p_vals = []
for r in rows_out:
    s = "✓" if r["success_at_5"] else "✗"
    s_ok += bool(r["success_at_5"])
    if r["p_at_10"] is not None:
        p_vals.append(r["p_at_10"])
    p_txt = f"{r['p_at_10']:.0%}（{r['rel_at_10']}/{r['n_at_10']}）" if r["p_at_10"] is not None else "—"
    print(f"  {r['query']:<18} 返回 {r['returned']:>3} 条 | Success@{K} {s} | Precision@{N} {p_txt} "
          f"| 语料内相关文件 {r['rel_in_corpus']}")
    if not r["success_at_5"]:
        print(f"        前 5 条：{r['top5_files']}")
    if r["unlabeled"]:
        print(f"        ⚠ 前 {N} 条里有 {len(r['unlabeled'])} 条**金标准未标注** —— "
              "按「不相关」计入了上面的 Precision，需人工判：")
        for u in r["unlabeled"]:
            print(f"            {u['contract_number']}  ¥{u['amount']}  {u['product']}")
            print(f"               {u['file']}")

print()
print("=== 汇总 ===")
print(f"  Success@{K}: {s_ok}/{len(rows_out)} = **{s_ok/len(rows_out):.1%}**"
      if rows_out else "  无可判定查询")
avg = sum(p_vals) / len(p_vals) if p_vals else 0.0
print(f"  Precision@{N}（均值，**下界**）: **{avg:.1%}**  （可判定查询 {len(p_vals)} 个）")
n_unlab = sum(len(r["unlabeled"]) for r in rows_out)
n_top = sum(r["n_at_10"] for r in rows_out)
print(f"  其中 **金标准未标注** {n_unlab} 条 / 前{N}共 {n_top} 条 —— 逐条见上；")
print(f"  若这些经人工判定为相关，Precision@{N} 上界为 "
      f"**{(sum(r['rel_at_10'] for r in rows_out) + n_unlab) / n_top:.1%}**。")
print()
print(f"  R6 门槛「Success@5 ≥95%」：{'达标 ✓' if rows_out and s_ok/len(rows_out) >= 0.95 else '**未达标 ✗**'}")
print(f"  R6 门槛「Precision@10 ≥95%」："
      f"{'达标 ✓（按下界即已达标）' if avg >= 0.95 else '**下界未达标 ✗**，但含未标注条目，须先人工判定' if n_unlab else '**未达标 ✗**'}")
print()
print("  ⚠️ 样本量极小（可判定查询仅 "
      f"{len(rows_out)} 个），且金标准按更大的旧语料标注 → 这两个数字**只能当趋势看**，")
print("     不足以单独支撑门槛判定。需求方若要正式验收，需先扩充金标准查询集。")
