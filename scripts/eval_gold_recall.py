"""R6 指标评估（修正版）：用冻结金标准在 clean 库上算真实 Recall + 负向对照。

**关键修正（前两版都错了）**：`expected-records-v1.json` 的 229 条里，
`relevance` 有 `relevant`(192) 与 **`irrelevant`(37)** 两类，且与 `manual_verified` 一一对应。
`irrelevant` 是**负向对照**（不该被返回）；把它算进召回分母，会把「正确地没返回」误判成漏检
—— 前两版正犯此错（v1 得 10.3%、v2 得 51.4%，**都不可用**）。

正确口径：
  召回分母 = **`relevance='relevant'`** 且落在**可检索集**（白名单 ∩ 有 CTL 合同）内的期望记录
  召回分子 = 其中被返回的
  假阳     = **`relevance='irrelevant'`** 且在可检索集内、**却被返回**的（R6-08：不得放宽条件造命中）

两级取交集缺一不可：金标准是给更大的旧语料标的，本库没有的文件不该计入分母（覆盖度问题）。

安全：只读库；不调用任何网关。
"""
from __future__ import annotations

import io
import pathlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
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

import sqlite3  # noqa: E402

from app.search import locate_by_product_amount  # noqa: E402

META = ("代谢", "代谢组", "全谱代谢", "LC-MS", "LC-MS/MS", "双平台代谢")
SINGLE = ("单细胞", "10x", "10X", "10x Genomics")
# 第 5 元素 = **产品排除**（合同级）。G05 的冻结原文是「代谢合同，**不要蛋白组和宏基因组**」，
# 不施加排除就等于换了一道查询 —— 实测会把它按金额命中的多组学合同误报成「负向对照误返」。
PROT_EXC = ("蛋白", "蛋白组", "蛋白质组", "DIA", "Olink", "宏基因组")

QUERY_SPEC: dict[str, tuple[tuple[str, ...], float, str | None, str | None, tuple[str, ...]]] = {
    "G01": (META, 500_000, None, None, ()),
    "G03": (META, 10_000, None, None, ()),
    "G05": (META, 20_000, None, None, PROT_EXC),
    "R0ADD-BOUNDARY-01": (META, 500_000, None, None, ()),
    "R0NEW-AMT-01": (META, 20_000, None, None, ()),
    "R0NEW-ALIAS-01": (META, 20_000, None, None, ()),
    "R0NEW-DATE-01": (META, 0, "2024-12", None, ()),
    "G06": (SINGLE, 0, None, None, ()),
}

recs = json.load(io.open(GOLD / "expected-records-v1.json", encoding="utf-8"))["records"]
by_q: dict[str, list[dict]] = defaultdict(list)
for r in recs:
    by_q[r["query_id"]].append(r)

con = sqlite3.connect((CLEAN / "bid_ai_clean_reg.db").resolve().as_uri() + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row
norm = lambda p: (p or "").replace("\\", "/").strip("/").lower()
by_rel = {norm(d["relative_path"]): dict(d) for d in
          con.execute("SELECT document_id, relative_path FROM documents")}
approved = {str(x["document_id"])[:12] for x in
            json.load(io.open(CLEAN / "data" / "approved_documents.json", encoding="utf-8"))
            if x.get("document_id")}
ctl_docs = {r[0] for r in con.execute(
    "SELECT DISTINCT document_id FROM contracts WHERE contract_id LIKE 'CTL-%'")}
queryable = {d["document_id"] for d in by_rel.values()
             if any(d["document_id"].startswith(p) for p in approved) and d["document_id"] in ctl_docs}
print(f"可检索文档（白名单 ∩ 有 CTL 合同）：{len(queryable)} 份")
print(f"金标准：relevant {sum(1 for r in recs if r['relevance']=='relevant')} / "
      f"irrelevant {sum(1 for r in recs if r['relevance']=='irrelevant')}\n")


def resolve(sp: str) -> dict | None:
    s = norm(sp)
    if not s:
        return None
    for rel, d in by_rel.items():
        if s.endswith(rel):
            return d
    return None


rows_out = []
TP = FP = FN = 0
for qid, (kws, thr, dfrom, dto, pexc) in QUERY_SPEC.items():
    exp = by_q.get(qid) or []
    if not exp:
        continue
    pos, neg = {}, {}
    for r in exp:
        d = resolve(r["source_path"])
        if d is None or d["document_id"] not in queryable:
            continue
        (pos if r["relevance"] == "relevant" else neg)[r["expected_record_id"]] = (r, d)
    if not pos and not neg:
        rows_out.append({"query": qid, "note": "金标准记录均不在可检索集 → 无法判定"})
        continue
    out = locate_by_product_amount(con, kws, thr, date_from=dfrom, date_to=dto,
                                   product_exclude=pexc)
    hit = {r.document_id for r in out if r.hit}
    tp = [k for k, (_r, d) in pos.items() if d["document_id"] in hit]
    fn = [k for k in pos if k not in tp]
    fp = [k for k, (_r, d) in neg.items() if d["document_id"] in hit]
    TP += len(tp); FN += len(fn); FP += len(fp)
    rows_out.append({"query": qid, "relevant_queryable": len(pos), "retrieved": len(tp),
                     "recall": round(len(tp) / len(pos), 4) if pos else None,
                     "irrelevant_queryable": len(neg), "false_positives": len(fp),
                     "missed": [pos[k][0].get("contract_number") for k in fn],
                     "fp_contracts": [neg[k][0].get("contract_number") for k in fp]})
con.close()

print("=== 逐 query ===")
for r in rows_out:
    if r.get("recall") is None:
        print(f"  {r['query']:<18} {r.get('note','')}")
        continue
    print(f"  {r['query']:<18} 相关可检索 {r['relevant_queryable']:>3} → 召回 {r['retrieved']:>3} "
          f"= **Recall {r['recall']:.1%}**；负向对照 {r['irrelevant_queryable']:>2} 条，误返 {r['false_positives']}")
    if r["missed"]:
        print(f"        漏: {r['missed'][:6]}")
    if r["fp_contracts"]:
        print(f"        ⚠ 误返: {r['fp_contracts'][:6]}")

den = TP + FN
print()
print("=== 汇总 ===")
print(f"  相关记录（可检索集内）: {den}   召回: {TP}   → **整体 Recall {TP/den:.1%}**" if den else "  无可判定记录")
if den:
    print(f"  R6 门槛「应召回文件 Recall ≥90%」：{'达标 ✓' if TP/den >= 0.90 else '**未达标 ✗**'}")
print(f"  负向对照误返（R6-08：不得放宽条件造命中）: {FP} 条")
# ⚠️ 只在**直接运行**时写报告。`eval_success_precision.py` 会 `from eval_gold_recall import QUERY_SPEC`
# —— 那是模块级导入，会把本文件顶层全部执行一遍，从而**连带覆写这个工作区文件**
# （2026-09-13 对抗审计实测：一次「只读审计」也会改动 data/gold_recall_eval.json，虽内容相同）。
if __name__ == "__main__":
    io.open(CLEAN / "data" / "gold_recall_eval.json", "w", encoding="utf-8").write(
        json.dumps({"per_query": rows_out, "summary": {"relevant": den, "retrieved": TP,
                                                       "false_positives": FP,
                                                       "recall": round(TP / den, 4) if den else None}},
                   ensure_ascii=False, indent=1))
    print("报告 → data/gold_recall_eval.json")
