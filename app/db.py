"""bid-ai-clean 最小数据模型：四张 SQLite 空表（与决策 D3A/D5A/D9/D11 对齐）。

- documents    : 文件台账（唯一约束 source_root_id + relative_path；sha256 内容去重）
- contracts    : 合同记录（乙方归属，contract_vendor/vendor_scope/vendor_conflict）
- contract_items: 产品服务明细行（row_type / product_amount_source）
- material_facts: 财务社保/仪器事实（fact_type 三列稀疏表）

开发阶段不建设迁移框架；表结构变化时删测试库重建。R1 只建空表，不迁移旧业务数据。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import app.config as config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    source_root_id TEXT NOT NULL,
    project_folder TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    file_ext TEXT, file_size INTEGER, file_mtime REAL, sha256 TEXT,
    project_type TEXT,               -- 审计字段：标书/比选/非标书/调研/…（范围真值仍按 R2 范围规则）
    document_role TEXT,
    vendor_name TEXT,
    path_vendor TEXT,                -- D5A：路径厂商（仅审计）
    role_source TEXT, role_confidence REAL, manual_override INTEGER DEFAULT 0,
    content_format TEXT,
    parse_status TEXT, pipeline_version TEXT, error_message TEXT,
    canonical_document_id TEXT,      -- D3A：内容去重，同 sha256 只解析一次
    UNIQUE (source_root_id, relative_path)
);
CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);

CREATE TABLE IF NOT EXISTS contracts (
    contract_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    ordinal INTEGER,
    contract_number TEXT,
    party_a TEXT, party_b TEXT,
    contract_vendor TEXT, vendor_scope TEXT, vendor_evidence TEXT,
    vendor_conflict INTEGER DEFAULT 0,                              -- D5A
    contract_date TEXT,
    total_amount REAL,
    evidence_text TEXT
);

CREATE TABLE IF NOT EXISTS contract_items (
    item_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES contracts(contract_id),
    product_raw TEXT, product_canonical TEXT,
    quantity REAL, unit_price REAL, line_amount REAL,
    row_type TEXT,                  -- D9: detail|product_subtotal|contract_total|header|note
    product_amount_source TEXT,     -- D9: declared|explicit_product_subtotal
    evidence_text TEXT
);

CREATE TABLE IF NOT EXISTS material_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    fact_type TEXT,   -- finance_period|social_security_month|instrument|purchase_contract|invoice|instrument_photo
    fact_value TEXT,
    evidence_text TEXT
);
"""


def connect() -> sqlite3.Connection:
    db_path = Path(config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(force: bool = False) -> None:
    db_path = Path(config.DB_PATH)
    if force and db_path.exists():
        db_path.unlink()
    con = connect()
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


@contextmanager
def session():
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def health() -> dict:
    """健康自检：可连接 + 四表存在。"""
    con = connect()
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required_tables = {"documents", "contracts", "contract_items", "material_facts"}
        return {"ok": required_tables <= tables, "tables": sorted(tables)}
    finally:
        con.close()