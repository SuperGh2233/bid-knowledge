"""R1 骨架回归：四空表、外键、sha256 索引、ES 空映射、连通自检。不扫描/不解析/不 OCR。

每个用例用独立 tmp_path 数据库，避免 Windows 文件锁；DB 路径经 monkeypatch 注入。
ES 用例直接调用 ensure_index（不 spawn 子进程），不可用时 pytest.skip。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.config as app_config  # noqa: E402
import app.index as idx  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """每用例独立测试库（直接改 config.DB_PATH，重建含新字段结构）。"""
    path = tmp_path / "clean_test.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    return path


def test_db_creates_four_empty_tables(fresh_db):
    h = db.health()
    assert h["ok"] is True
    for table in ("documents", "contracts", "contract_items", "material_facts"):
        rows = list(db.connect().execute(f"SELECT * FROM {table} LIMIT 1"))
        assert rows == [], f"{table} 应为空"


def test_documents_unique_constraint(fresh_db):
    row = {
        "document_id": "d1", "source_root_id": "root1", "project_folder": "p1",
        "relative_path": "a/响应文件.docx",
    }
    cols = ",".join(row)
    ph = ",".join("?" * len(row))
    with db.session() as con:
        con.execute(f"INSERT INTO documents ({cols}) VALUES ({ph})", list(row.values()))
    violated = False
    with db.session() as con:
        try:
            con.execute(f"INSERT INTO documents ({cols}) VALUES ({ph})", list(row.values()))
        except Exception:
            violated = True
    assert violated, "唯一约束(source_root_id, relative_path) 应阻止重复插入"
    # role/scope 字段含 path_vendor、project_type（架构字段存在）
    cols_row = db.connect().execute("PRAGMA table_info(documents)").fetchall()
    assert {r[1] for r in cols_row} >= {"project_type", "path_vendor", "canonical_document_id"}


def test_contracts_fk_rejects_orphan(fresh_db):
    """contracts.document_id 外键生效（悬挂引用应被拒绝）。"""
    violated = False
    with db.session() as con:
        try:
            con.execute(
                "INSERT INTO contracts (contract_id, document_id) VALUES (?, ?)",
                ("c1", "no-such-doc"),
            )
        except Exception:
            violated = True
    assert violated, "合同悬挂 document 引用应被外键拒绝"


def test_contract_items_fk_and_row_type(fresh_db):
    """contract_items 外键拒绝悬挂 contract 引用；含 D9 row_type/product_amount_source 字段。"""
    con = db.connect()
    try:
        orphan_rejected = False
        with con:
            try:
                con.execute("INSERT INTO contract_items (item_id, contract_id) VALUES ('i1', 'ghost-contract')")
            except Exception:
                orphan_rejected = True
        assert orphan_rejected, "contract_items 悬挂 contract 引用应被外键拒绝"
        # 父→子正常插入
        with con:
            con.execute("INSERT INTO documents (document_id, source_root_id, project_folder, relative_path) "
                        "VALUES ('doc1','r','p','rel')")
            con.execute("INSERT INTO contracts (contract_id, document_id) VALUES ('c1','doc1')")
            con.execute("INSERT INTO contract_items (item_id, contract_id, row_type, product_amount_source) "
                        "VALUES ('i2','c1','detail','declared')")
        row = con.execute("SELECT row_type, product_amount_source FROM contract_items WHERE item_id='i2'").fetchone()
        assert (row["row_type"], row["product_amount_source"]) == ("detail", "declared")
    finally:
        con.close()


def test_material_facts_table(fresh_db):
    con = db.connect()
    try:
        # 先建父文档
        with con:
            con.execute("INSERT INTO documents (document_id, source_root_id, project_folder, relative_path) "
                        "VALUES ('d1','r','p','rel')")
            con.execute(
                "INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
                "VALUES ('d1', 'finance_period', '2025-12', '证据')")
        with con:
            row = con.execute("SELECT * FROM material_facts").fetchone()
            assert row["fact_type"] == "finance_period"
            assert row["fact_value"] == "2025-12"
    finally:
        con.close()


def test_sha256_index_and_project_type(fresh_db):
    con = db.connect()
    try:
        indexes = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='documents'")}
        assert "idx_documents_sha256" in indexes
        cols = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
        assert "project_type" in cols
    finally:
        con.close()


def test_es_mapping_generated_without_live_connection():
    """单元级：不依赖真实 ES——验证 ES 映射 JSON 可生成、dims 注入正确。"""
    props = idx.properties_for(768)
    assert {"section_type", "structural_role", "classification_source"} <= set(props)
    assert props["embedding"]["dims"] == 768
    # 无 dims 时不建 embedding（D13：R1 不探测 dims）
    assert "embedding" not in idx.properties_for(None)
    # settings 已含 cjk 分析器
    assert idx.SETTINGS["analysis"]["analyzer"]["cjk_analyzer"]["type"] == "cjk"
    # 断言 cli 健康对象可导入（不执行，避免真实 ES 依赖）
    from cli import health, main  # noqa: F401,E402
    assert callable(health) and callable(main)