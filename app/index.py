"""bid-ai-clean 唯一 ES 方案索引映射（R1 只建空映射，不写入）。

只保存方案章节（contract_evidence 不进方案索引；与 D2A/D11 对齐）。
检索命中后按 document_id 回查 SQLite 获取文件信息；ES 不存源路径/合同字段/全量元数据。

结构边界(structural_role)与业务语义(section_type)正交双层（D11）。
embedding dims 由 embedding_spec 探测后显式写入（D13）；dims 未知时不建 embedding 字段。
"""
from __future__ import annotations

from elasticsearch import Elasticsearch

import app.config as config

BASE_PROPERTIES = {
    "section_id": {"type": "keyword"},
    "document_id": {"type": "keyword"},
    "project_key": {"type": "keyword"},
    "section_type": {"type": "keyword"},
    "structural_role": {"type": "keyword"},
    "boundary_source": {"type": "keyword"},
    "classification_source": {"type": "keyword"},
    "classification_confidence": {"type": "float"},
    "heading": {"type": "text", "analyzer": "cjk_analyzer"},
    "text": {"type": "text", "analyzer": "cjk_analyzer"},
    "content_format": {"type": "keyword"},
}

EMBEDDING_PROP = {"type": "dense_vector", "similarity": "cosine"}

SETTINGS = {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "analysis": {
        "analyzer": {"cjk_analyzer": {"type": "cjk"}},
    },
}


def properties_for(dims: int | None = None) -> dict:
    props = dict(BASE_PROPERTIES)
    if dims:
        props["embedding"] = {**EMBEDDING_PROP, "dims": dims}
    return props


def ensure_index(es: Elasticsearch | None = None, dims: int | None = None) -> str:
    """创建（若不存在）方案索引；dims 未知则暂不建 embedding 字段（D13）。

    R1 只建空映射；R5 使用前按 embedding_spec 探测 dims 后 put_mapping 补上。
    """
    es = es or Elasticsearch(config.ES_URL, request_timeout=30)
    index = config.ES_INDEX_SCHEME
    if not es.indices.exists(index=index):
        es.indices.create(index=index, body={
            "settings": SETTINGS,
            "mappings": {"properties": properties_for(dims)},
        })
    elif dims:
        # 已存在: 补 embedding 字段（幂等, 不重建）；失败不允许静默忽略（P1）
        es.indices.put_mapping(index=index, body={
            "properties": {"embedding": {**EMBEDDING_PROP, "dims": dims}}})
    return index