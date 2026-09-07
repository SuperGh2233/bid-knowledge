"""bid-ai-clean 配置：密钥只从环境变量读取，不落库。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 数据/存储
DB_PATH = os.environ.get("BID_AI_CLEAN_DB", str(BASE_DIR / "bid_ai_clean.db"))
# SOURCE_ROOTS 为空串/全空时过滤，避免空配置被当成当前目录（P0 防护）
SOURCE_ROOTS = tuple(
    r for r in (p.strip() for p in os.environ.get("SOURCE_ROOTS", "").split(os.pathsep))
    if r
)
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
ES_INDEX_SCHEME = os.environ.get("ES_INDEX_SCHEME", "bid_scheme_sections_v1")
ES_INDEX_CONFIG = os.environ.get("ES_INDEX_CONFIG", "bid_config_index_v1")  # 预留：embedding dims 探测

# LLM / OCR（外发需授权；OCR 本地优先）
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", LLM_BASE_URL)
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", LLM_API_KEY)
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "")

# 扫描/解析安全
OCR_ENABLED = os.environ.get("OCR_ENABLED", "false").lower() == "true"  # 默认不 OCR
PARSE_NATIVE_FIRST = os.environ.get("PARSE_NATIVE_FIRST", "true").lower() == "true"
DERIVED_PAGE_IMAGE_SKIP = os.environ.get("DERIVED_PAGE_IMAGE_SKIP", "true").lower() == "true"


def required(name: str, value: str | None) -> str:
    if not value:
        raise RuntimeError(f"环境变量缺失：{name}")
    return value