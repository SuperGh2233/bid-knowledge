"""D1 补强：把剩余我方响应文件加进方案章节索引 —— **只用 BM25，不生成 embedding**。

为什么可以不带向量：`search_scheme_sections` 与 `proposal._recall` **全程只用 BM25**
（这是刻意的——kNN 要把章节正文送 embedding 网关，属外发）。所以补齐**纵向闭环**
（D1：解析 → 章节 → 定位/证据）**不需要任何外发**。

为什么单独一个脚本而不是改 `r5_embed_sections.py`：那个脚本会**清空重写**索引，
而现有 358 条是带 embedding 的产物 —— 重写会把它降级。本脚本**只追加**。

诚实标注：新写入的章节带 `embedding_state='absent_bm25_only'`。
日后若要开 kNN，**必须先按它筛出这些文档补算向量**，否则语义检索会静默缺一段。

安全：只读库；只写本机 ES；不调用任何网关。
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))

import app.config as config  # noqa: E402
from app.extract import extract_scheme_sections  # noqa: E402
from elasticsearch import Elasticsearch  # noqa: E402

TARGET_DOCS = int(sys.argv[1]) if len(sys.argv) > 1 else 20   # D1 门槛
KEEP_ROLES = ("content_section", "appendix")
ROLE_SET = ("our_response", "final_signed")   # R5-01/D2A：竞品/招标/未知不得进方案索引

es = Elasticsearch(config.ES_URL, request_timeout=60)
idx = config.ES_INDEX_SCHEME

con = sqlite3.connect((CLEAN / "bid_ai_clean_reg.db").resolve().as_uri() + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute(
    "SELECT d.document_id, d.project_folder, d.document_role, d.relative_path, "
    "       a.text, a.content_format, a.page_metadata "
    "FROM documents d JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id "
    "WHERE d.document_role IN ('our_response','final_signed') "
    # ⚠️ 不能只写 `= 'native_text'`：**原生 PDF 存的是 `native_pdf_text`**
    # （实测 93 份我方响应 PDF 因此被整体跳过，其中有 36 份能抽出 60–260 条章节）。
    # `scanned_ocr` 也纳入（2026-09-11）：R7-04「原生文字优先，扫描件作**补充**」——
    # 排序时 `_FORMAT_RANK` 已把 scanned_ocr 排在最后，故纳入不会挤掉原生证据；
    # 但**不纳入就等于本地 OCR 白跑**（抽出的正文永远进不了方案索引）。
    "  AND a.content_format IN ('native_text','native_pdf_text','mixed','scanned_ocr')"
    "  AND length(a.text) > 0 "
    "ORDER BY length(a.text) DESC")]
con.close()

indexed = {b["key"] for b in es.search(
    index=idx, size=0, aggs={"d": {"terms": {"field": "document_id", "size": 500}}}
)["aggregations"]["d"]["buckets"]}
todo = [r for r in rows if r["document_id"] not in indexed]
need = max(0, TARGET_DOCS - len(indexed))
print(f"索引现有 {len(indexed)} 份文档 / {es.count(index=idx)['count']} 条章节")
print(f"D1 目标 {TARGET_DOCS} 份 → 还需 {need} 份；候选（我方响应·原生）共 {len(todo)} 份\n")

if need == 0:
    print("已达到目标份数，无需追加。")
    raise SystemExit(0)

picked: list[dict] = []
sections: list[dict] = []
per_doc: list[tuple[str, int, int]] = []
skipped: list[tuple[str, int]] = []
# 逐个候选试：产出 0 章节的文档**跳过并继续取下一个**，直到「有章节的文档」达到 need 份。
# （实测 `海思云创欧易生物合同.docx` 产出 0 章节；若不跳过会卡住、永远差 1 份。）
for r in todo:
    if len(picked) >= need:
        break
    meta = json.loads(r["page_metadata"] or "{}")
    outline = (meta.get("structure") or {}).get("outline") or []
    got = [s for s in extract_scheme_sections(r["text"], r["document_id"], r["project_folder"],
                                              r["content_format"] or "", outline)
           if s["structural_role"] in KEEP_ROLES]
    name = r["relative_path"].rsplit("/", 1)[-1]
    if not got:
        skipped.append((name, len(r["text"])))
        continue
    picked.append(r)
    per_doc.append((name, len(r["text"]), len(got)))
    sections.extend(got)

# 退出条件自检：角色硬校验（与 r5_embed_sections.py 同一道门）
roles = {r["document_role"] for r in picked}
assert roles <= set(ROLE_SET), f"混入非我方文档: {roles - set(ROLE_SET)}"

print("=== 本次追加的文档 ===")
for name, ln, n in per_doc:
    print(f"  正文 {ln:>6} 字 → 章节 {n:>3} 条   {name[:62]}")
if skipped:
    print(f"（跳过 {len(skipped)} 份：无正文型章节）")
    for n, ln in skipped:
        print(f"    · 正文 {ln:>6} 字   {n[:62]}")
print(f"\n合计新增章节 {len(sections)} 条")

if not sections:
    print("没有可写入的章节，退出。")
    raise SystemExit(0)

t0 = time.time()
ops = []
for s in sections:
    doc = {k: s[k] for k in ("section_id", "document_id", "project_key", "section_type",
                             "structural_role", "boundary_source", "classification_source",
                             "classification_confidence", "heading", "text", "content_format")}
    # **不写 embedding** —— 本批是 BM25-only。显式标注，避免日后开 kNN 时静默缺一段。
    doc["embedding_state"] = "absent_bm25_only"
    ops.append({"index": {"_index": idx, "_id": s["section_id"]}})
    ops.append(doc)

es.bulk(operations=ops, refresh=True)
after_docs = es.search(index=idx, size=0, aggs={"d": {"cardinality": {"field": "document_id"}}}
                       )["aggregations"]["d"]["value"]
print(f"\n写入 {len(sections)} 条，用时 {time.time()-t0:.1f}s")
print(f"索引现在：{es.count(index=idx)['count']} 条章节 / {after_docs} 份文档"
      f"（D1 目标 {TARGET_DOCS} ：{'达标 ✓' if after_docs >= TARGET_DOCS else '未达标'}）")

io.open(CLEAN / "data" / "d1_bm25_only_docs.json", "w", encoding="utf-8").write(
    json.dumps({"target": TARGET_DOCS, "added": [{"file": n, "chars": l, "sections": c}
                                                 for n, l, c in per_doc],
                "note": "这些文档的章节只进 BM25，无 embedding（embedding_state=absent_bm25_only）"},
               ensure_ascii=False, indent=1))
print("报告 → data/d1_bm25_only_docs.json")
