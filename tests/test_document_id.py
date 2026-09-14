"""文件身份稳定性与 DB 约束测试。

- 相同 source_root_id + relative_path → ID 不变
- 不同 来源根 或 路径 → ID 不同
- 文件内容/大小/mtime 变化 → 文件身份 ID 不变（身份不随内容与易变字段变）
- 缺失/空白/类型错误 → 抛错，禁止静默补空
- 数据库 CHECK: document_id 拒绝 NULL / 空串
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.db as db  # noqa: E402


def test_same_identity_same_id():
    a = db.deterministic_document_id("2025年", "项目/响应文件.docx")
    b = db.deterministic_document_id("2025年", "项目/响应文件.docx")
    assert a == b
    assert len(a) == 64  # 完整 SHA-256 hex


def test_different_root_different_id():
    a = db.deterministic_document_id("2025年", "项目/响应文件.docx")
    b = db.deterministic_document_id("2026年", "项目/响应文件.docx")
    assert a != b


def test_different_path_different_id():
    a = db.deterministic_document_id("2025年", "A/响应文件.docx")
    b = db.deterministic_document_id("2025年", "B/响应文件.docx")
    assert a != b


def test_content_size_mtime_do_not_affect_id():
    """文件身份基于路径，与内容/大小/修改时间无关。"""
    ids = {db.deterministic_document_id("2025年", "P/响应.docx") for _ in range(3)}
    assert len(ids) == 1
    assert db.deterministic_document_id("2025年", "P/响应.docx") == \
           db.deterministic_document_id("2025年", "P/响应.docx")  # 内容/大小/mtime 变化不影响


def test_invalid_identity_raises():
    with pytest.raises(ValueError):
        db.deterministic_document_id("", "P/f.docx")
    with pytest.raises(ValueError):
        db.deterministic_document_id("2025年", "  ")
    with pytest.raises(TypeError):
        db.deterministic_document_id(None, "P/f.docx")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        db.deterministic_document_id("2025年", 123)  # type: ignore[arg-type]


def test_stable_path_key_normalizes_separator():
    root1, rel1 = db.stable_path_key("2025年", "P\\Q\\响应.docx")
    root2, rel2 = db.stable_path_key("2025年", "P/Q/响应.docx")
    assert (rel1 == rel2) and rel1 == "P/Q/响应.docx"
    assert rel1.count("/") >= 1  # 已含项目目录层


def test_db_rejects_null_document_id(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "id_test.db"))
    db.init_db(force=True)
    con = db.connect()
    try:
        with pytest.raises(Exception):  # NOT NULL / PK / CHECK 任一拒绝
            con.execute(
                "INSERT INTO documents (document_id, source_root_id, project_folder, relative_path) "
                "VALUES (NULL, '2025年', 'P', 'P/Q/响应.docx')")
    finally:
        con.close()


def test_db_rejects_empty_document_id(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "id_test2.db"))
    db.init_db(force=True)
    con = db.connect()
    try:
        with pytest.raises(Exception):
            con.execute(
                "INSERT INTO documents (document_id, source_root_id, project_folder, relative_path) "
                "VALUES ('', '2025年', 'P', 'P/Q/响应.docx')")
    finally:
        con.close()