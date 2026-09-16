"""白名单索引：只把**本次新 OCR 的 my 方响应扫描件**的章节写进方案索引（BM25）。

为什么单独一个脚本：`scripts/r5_index_bm25_only.py` 是「补到 N 份」的分批模式 ——
传 TARGET_DOCS 会把**库里所有**未索引的 our_response/final_signed 一起带走（存量数百份），
远超本次范围。本脚本**只索引 `parse_status IS NULL 且 content_format='scanned_ocr'`**
的 17 份（2026-09-15 白名单 OCR 产出），不碰其它。

安全：只读库；只写本机 ES；**不调用任何网关**（BM25-only，无 embedding → 零外发）。
新写入章节带 `embedding_state='absent_bm25_only'`（诚实标注：日后开 kNN 须补算向量）。

用法：
  python scripts/refresh_index_whitelist.py --dry-run    # 只报将写入多少章节
  python scripts/refresh_index_whitelist.py              # 真实写入 ES（零外发）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))

import app.config as config  # noqa: E402
from app.extract import extract_scheme_sections  # noqa: E402
from elasticsearch import Elasticsearch  # noqa: E402

KEEP_ROLES = ("content_section", "appendix")
ROLE_SET = ("our_response", "final_signed")
DB_PATH = CLEAN / "bid_ai_clean_reg.db"

SELECT_SQL = """
SELECT d.document_id, d.project_folder, d.document_role, d.relative_path,
       d.content_format, a.text, a.page_metadata
FROM documents d JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id
WHERE d.document_role IN ('our_response','final_signed')
  AND d.parse_status IS NULL
  AND a.content_format = 'scanned_ocr'
  AND length(a.text) > 0
ORDER BY length(a.text) DESC
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="白名单索引：本次新 OCR 的响应件 → 方案章节索引（BM25，零外发）")
    ap.add_argument("--dry-run", action="store_true", help="只报将写入多少章节，不写 ES")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(SELECT_SQL)]
    con.close()
    print(f"白名单候选（our_response/final_signed · OCR 正文 · 未索引过）: {len(rows)} 份")

    es = Elasticsearch(config.ES_URL, request_timeout=60)
    idx = config.ES_INDEX_SCHEME
    indexed = {b["key"] for b in es.search(
        index=idx, size=0, aggs={"d": {"terms": {"field": "document_id", "size": 2000}}}
    )["aggregations"]["d"]["buckets"]}
    todo = [r for r in rows if r["document_id"] not in indexed]
    print(f"索引现有 {len(indexed)} 份文档 / {es.count(index=idx)['count']} 条章节；本次待写入 {len(todo)} 份")

    sections: list[dict] = []
    per_doc: list[tuple[str, int, int]] = []
    skipped: list[tuple[str, int]] = []
    for r in todo:
        meta = json.loads(r["page_metadata"] or "{}")
        outline = (meta.get("structure") or {}).get("outline") or []
        got = [s for s in extract_scheme_sections(r["text"], r["document_id"], r["project_folder"],
                                                  r["content_format"] or "", outline)
               if s["structural_role"] in KEEP_ROLES]
        name = r["relative_path"].rsplit("/", 1)[-1]
        if not got:
            skipped.append((name, len(r["text"])))
            continue
        per_doc.append((name, len(r["text"]), len(got)))
        sections.extend(got)

    roles = {r["document_role"] for r in todo}
    assert roles <= set(ROLE_SET), f"混入非我方文档: {roles - set(ROLE_SET)}"

    print("=== 本次索引的文档 ===")
    for name, ln, n in per_doc:
        print(f"  正文 {ln:>9,} 字 → 章节 {n:>4} 条   {name[:56]}")
    if skipped:
        print(f"（跳过 {len(skipped)} 份：无正文型章节）")
        for n, ln in skipped:
            print(f"    · 正文 {ln:>9,} 字   {n[:56]}")
    print(f"合计新增章节 {len(sections)} 条")

    if args.dry_run:
        print("[dry-run] 未写 ES。")
        return 0
    if not sections:
        print("没有可写入的章节，退出。")
        return 0

    t0 = time.time()
    ops = []
    for s in sections:
        doc = {k: s[k] for k in ("section_id", "document_id", "project_key", "section_type",
                                 "structural_role", "boundary_source", "classification_source",
                                 "classification_confidence", "heading", "text", "content_format")}
        doc["embedding_state"] = "absent_bm25_only"
        ops.append({"index": {"_index": idx, "_id": s["section_id"]}})
        ops.append(doc)
    es.bulk(operations=ops, refresh=True)
    after_docs = es.search(index=idx, size=0, aggs={"d": {"cardinality": {"field": "document_id"}}}
                           )["aggregations"]["d"]["value"]
    print(f"\n写入 {len(sections)} 条，用时 {time.time()-t0:.1f}s")
    print(f"索引现在：{es.count(index=idx)['count']} 条章节 / {after_docs} 份文档")
    (CLEAN / "data" / "d1_bm25_only_docs.json").write_text(
        json.dumps({"added": [{"file": n, "chars": l, "sections": c} for n, l, c in per_doc],
                    "note": "2026-09-15 白名单（本次新 OCR 响应件）章节，BM25-only，无 embedding"},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())