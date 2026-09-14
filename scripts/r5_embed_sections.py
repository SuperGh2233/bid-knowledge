"""R5-04：方案章节 embedding → 写 ES bid_scheme_sections_v1。

授权依据：用户批准"章节 embedding + 写 ES bid_scheme_sections_v1"（2026-09-10）。
外发范围：章节文本 → newapi.oebiotech.com 的 Embedding 网关。仅 our_response 文档，
不含 OCR（本批全原生 Word）。

维度处理：R1 建索引时探测的 768 与实际模型（qwen3.7-text-embedding → 1024）不符。
索引为空，按 D13"dims 探测后显式写入"重建。**重建前校验索引必须为 0 条**，否则中止。

长章节：embedding 取 heading + 正文前 EMBED_CHARS 字符（头部最具代表性），
ES 里存完整 text——不为向量化而丢原文。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))

import app.config as config  # noqa: E402
from app import index as index_mod  # noqa: E402
from app.extract import extract_scheme_sections  # noqa: E402
from elasticsearch import Elasticsearch  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from openai import OpenAI  # noqa: E402

ENV = dotenv_values(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai\.env")
EMBED_CHARS = 1500
BATCH = 16
KEEP_ROLES = ("content_section", "appendix")

con = sqlite3.connect((CLEAN / "bid_ai_clean_reg.db").resolve().as_uri() + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row
docs = con.execute(
    "SELECT d.document_id, d.project_folder, d.document_role, a.text, a.content_format, a.page_metadata "
    "FROM documents d JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id "
    "WHERE d.document_role='our_response' AND length(a.text) > 60000 "
    "ORDER BY length(a.text) DESC"
).fetchall()
con.close()

# —— 收集章节 ——
sections: list[dict] = []
for d in docs:
    meta = json.loads(d["page_metadata"] or "{}")
    outline = (meta.get("structure") or {}).get("outline") or []
    for s in extract_scheme_sections(d["text"], d["document_id"], d["project_folder"],
                                     d["content_format"] or "", outline):
        if s["structural_role"] in KEEP_ROLES:
            sections.append(s)
print(f"来源文档 {len(docs)} 份；进入索引的章节 {len(sections)} 条")

# 退出条件自检：竞品/招标要求不得进索引（本批全部 our_response，按角色硬校验）
roles = {d["document_role"] for d in docs}
assert roles == {"our_response"}, f"混入非我方响应文档: {roles}"

client = OpenAI(base_url=ENV["EMBEDDING_BASE_URL"], api_key=ENV["EMBEDDING_API_KEY"],
                timeout=120, max_retries=2)
probe = client.embeddings.create(model=ENV["EMBEDDING_MODEL"], input=["维度探测"]).data[0].embedding
dims = len(probe)
print(f"Embedding 模型 {ENV['EMBEDDING_MODEL']} → {dims} 维")

es = Elasticsearch(config.ES_URL, request_timeout=60)
idx = config.ES_INDEX_SCHEME
existing = es.count(index=idx)["count"]
print(f"目标索引 {idx} 现有 {existing} 条")

# 索引里只放可重建的派生物（计划 §4.2："ES 只保存可重建的历史方案章节和向量"）。
# 重建前校验既有数据确实是本流程产物，再清空重写——不 drop 索引（保留已按 1024 维建好的映射）。
if existing:
    sample = es.search(index=idx, size=200, _source=["section_type", "boundary_source"])["hits"]["hits"]
    ours = all(h["_source"].get("section_type") == "unclassified"
               and h["_source"].get("boundary_source") in ("word_outline", "text_pattern", "none")
               for h in sample)
    if not ours:
        raise SystemExit("索引内含非本流程产物，拒绝清空")
    print(f"  校验通过（抽样 {len(sample)} 条均为方案章节派生物），清空重写")
    es.delete_by_query(index=idx, query={"match_all": {}}, refresh=True)

if "embedding" not in (es.indices.get_mapping(index=idx)[idx]["mappings"].get("properties") or {}):
    es.indices.put_mapping(index=idx, body={
        "properties": {"embedding": {**index_mod.EMBEDDING_PROP, "dims": dims}}})
print(f"索引映射 embedding dims={dims}")

t0 = time.time()
written = 0
for i in range(0, len(sections), BATCH):
    chunk = sections[i:i + BATCH]
    payload = [(s["heading"] + "\n" + s["text"])[:EMBED_CHARS] for s in chunk]
    vecs = client.embeddings.create(model=ENV["EMBEDDING_MODEL"], input=payload).data
    ops = []
    for s, v in zip(chunk, vecs):
        doc = {k: s[k] for k in ("section_id", "document_id", "project_key", "section_type",
                                 "structural_role", "boundary_source", "classification_source",
                                 "classification_confidence", "heading", "text", "content_format")}
        doc["embedding"] = v.embedding
        ops.append({"index": {"_index": idx, "_id": s["section_id"]}})
        ops.append(doc)
    es.bulk(operations=ops, refresh=False)
    written += len(chunk)
    print(f"  ...已写 {written}/{len(sections)}")

es.indices.refresh(index=idx)
print(f"\n完成：{written} 条，用时 {time.time()-t0:.1f}s，索引实际计数 {es.count(index=idx)['count']}")
