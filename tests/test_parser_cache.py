"""R3 缓存复用测试（修正版：复用前验证文件字节 SHA）。

真实调用 app.parser.process_native（共享处理入口）。只读独立临时库。
- 冷缓存：新内容 → 真解析、新产物（native_parser_called=True, new_artifact=True）。
- 温缓存：同 document 再处理 → 读文件字节算 SHA（files_read=1）、命中同版本产物复用，
  native_parser_called=0；石蕊证明解析器不执行。
- 文件更新：同一路径内容修改 → document_id 不变、内容 SHA 变化、重新解析并更新关联、
  不返回旧正文（禁止预清 canonical 绕过）；内容未变再处理原生解析器增量=0。
- 跨文件相同内容 → 只解析一次、只存一份 ParseArtifact，两个来源 canonical 相同且都保留。
不 OCR / 不写 ES / 不读 NAS / 不改 reg 库。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config as app_config  # noqa: E402
import app.db as db  # noqa: E402
from app import parser  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "cache_test.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    return db.connect()


class _Litmus:
    """石蕊：任何一次调用都抛错 → 进入此分支即证明解析器被重复调用。"""
    def __call__(self, path):
        raise AssertionError("原生解析器不应被调用")


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
    ' Target="word/document.xml"/></Relationships>'
)


def mk_docx(path: Path, paras: list[str], fixed_ts=True):
    """构造确定性 DOCX：固定 ZipInfo 时间戳 → 同内容字节恒定（跨秒稳定）。"""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>' for t in paras)
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}<w:sectPr/></w:body></w:document>")
    ts = (2000, 1, 1, 0, 0, 0) if fixed_ts else None
    import zipfile as _z
    info_file = _z.ZipInfo("word/document.xml", ts)
    info_types = _z.ZipInfo("[Content_Types].xml", ts)
    info_rels = _z.ZipInfo("_rels/.rels", ts)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(info_types, CONTENT_TYPES)
        z.writestr(info_rels, RELS)
        z.writestr(info_file, xml)


def register(con, *, doc_id, rel, root="2026年", project="20260101-标书-某人-某项目",
             role="our_response", status="pending", override=0):
    with con:
        con.execute(
            "INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
            " document_role, manual_override, parse_status) VALUES (?,?,?,?,?,?,?)",
            (doc_id, root, project, rel, role, override, status))


def doc_row(con, doc_id):
    return dict(con.execute("SELECT * FROM documents WHERE document_id=?", (doc_id,)).fetchone())


def roots_map(tmp_path, root="2026年"):
    r = tmp_path / root
    r.mkdir(parents=True, exist_ok=True)
    return {root: r}


# —— 1) 冷缓存：新内容 → 真解析、新产物 ——
def test_cold_cache_parses_new_docs(tmp_path, fresh_db):
    roots = roots_map(tmp_path)
    p = roots["2026年"] / "20260101-标书-某人-某项目"
    p.mkdir(parents=True, exist_ok=True)
    mk_docx(p / "响应文件.docx", ["我方响应正文 A", "表格内容"], fixed_ts=True)
    mk_docx(p / "商务技术部分.docx", ["我方响应正文 B"], fixed_ts=True)
    register(fresh_db, doc_id="d1", rel="20260101-标书-某人-某项目/响应文件.docx")
    register(fresh_db, doc_id="d2", rel="20260101-标书-某人-某项目/商务技术部分.docx")

    r1 = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)
    r2 = parser.process_native(fresh_db, doc_row(fresh_db, "d2"), roots)

    assert r1.error is None and r1.new_artifact and r1.native_parser_called
    assert r1.chars and r1.chars > 0
    assert r2.new_artifact and r2.native_parser_called
    assert fresh_db.execute("SELECT COUNT(*) FROM parse_artifacts").fetchone()[0] == 2
    # 内容身份 = 文件字节 SHA
    raw = (roots["2026年"] / "20260101-标书-某人-某项目/响应文件.docx").read_bytes()
    assert r1.content_sha == parser.file_sha_from_bytes(raw)
    # —— 正文内容层断言（审查修复）：产物 text 必须是真实新文档正文，而非占位/旧文 ——
    art = fresh_db.execute("SELECT text FROM parse_artifacts WHERE canonical_document_id=?",
                           (r1.content_sha,)).fetchone()
    assert art is not None and "我方响应正文 A" in art["text"] and "表格内容" in art["text"]


# —— 2) 温缓存：同 document 再处理 → 复用，石蕊证明解析器不被调用 ——
def test_warm_cache_reuses_without_reparse(tmp_path, fresh_db):
    roots = roots_map(tmp_path)
    p = roots["2026年"] / "20260101-标书-某人-某项目"
    p.mkdir(parents=True, exist_ok=True)
    mk_docx(p / "响应文件.docx", ["完整响应正文"])
    register(fresh_db, doc_id="d1", rel="20260101-标书-某人-某项目/响应文件.docx")
    first = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)
    assert first.new_artifact

    second = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots, native_parser=_Litmus())
    assert second.error is None
    assert second.cache_hit and not second.native_parser_called
    assert second.files_read == 1  # 复用前仍读文件字节算 SHA（有效性检查；不读取则无法发现更新）
    assert second.canonical_document_id == first.content_sha
    assert fresh_db.execute("SELECT COUNT(*) FROM parse_artifacts").fetchone()[0] == 1


# —— 3) 文件更新：同路径内容变化 → 重新解析，不返回旧正文 ——
def test_file_update_detects_and_reparses(tmp_path, fresh_db):
    roots = roots_map(tmp_path)
    target = roots["2026年"] / "20260101-标书-某人-某项目" / "响应文件.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    mk_docx(target, ["旧正文 v1"])
    register(fresh_db, doc_id="d1", rel="20260101-标书-某人-某项目/响应文件.docx",
             project="20260101-标书-某人-某项目")

    first = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)
    assert first.new_artifact and first.native_parser_called
    old_sha = first.content_sha
    row_after = doc_row(fresh_db, "d1")
    assert row_after["canonical_document_id"] == old_sha
    old_artifact = fresh_db.execute(
        "SELECT COUNT(*) FROM parse_artifacts WHERE canonical_document_id=?", (old_sha,)).fetchone()[0]
    assert old_artifact == 1

    # —— 修改同一路径内容（不预清 canonical —— 这正是要验证的有效性检查） ——
    mk_docx(target, ["新正文 v2 已更新"])
    second = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)

    # 身份不变；内容 SHA 变化；被重新解析并更新关联；不返回旧正文
    assert second.doc_id == "d1"
    assert second.content_sha != old_sha
    assert second.new_artifact and second.native_parser_called
    assert second.canonical_document_id == second.content_sha
    row_now = doc_row(fresh_db, "d1")
    assert row_now["canonical_document_id"] == second.content_sha
    assert row_now["document_id"] == "d1"  # 文件身份未变
    # —— 正文内容层断言（审查修复）：新 canonical 产物的 text 必须是"新正文 v2"，且不等于旧产物 text ——
    new_art = fresh_db.execute("SELECT text FROM parse_artifacts WHERE canonical_document_id=?",
                               (second.content_sha,)).fetchone()
    old_art = fresh_db.execute("SELECT text FROM parse_artifacts WHERE canonical_document_id=?",
                               (old_sha,)).fetchone()
    assert new_art is not None and "新正文 v2 已更新" in new_art["text"]
    assert old_art is not None and old_art["text"] != new_art["text"]
    assert "新正文 v2 已更新" not in (old_art["text"] or "")

    # —— 内容未变再次处理 → 原生解析器增量 0 ——
    third = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots, native_parser=_Litmus())
    assert third.cache_hit and not third.native_parser_called and third.files_read == 1
    assert third.canonical_document_id == second.content_sha
    assert fresh_db.execute("SELECT COUNT(*) FROM parse_artifacts").fetchone()[0] == 2  # 旧+新各1，未再重复


# —— 4) 跨文件相同内容 → 只解析一次，只存一份，双来源保留 ——
def test_same_content_shared_across_different_identities(tmp_path, fresh_db):
    roots = roots_map(tmp_path)
    a_dir = roots["2026年"] / "项目A"
    b_dir = roots["2026年"] / "项目B"
    a_dir.mkdir(parents=True, exist_ok=True)
    b_dir.mkdir(parents=True, exist_ok=True)
    paras = ["完全相同的内容 ABC", "第二段"]
    # 固定 ZipInfo 时间戳 → 两份文件字节恒定一致（跨秒也稳定），并把前提显式断言固定（审查修复）
    mk_docx(a_dir / "响应文件.docx", paras, fixed_ts=True)
    mk_docx(b_dir / "投标文件.docx", paras, fixed_ts=True)
    assert parser.file_sha_from_bytes((a_dir / "响应文件.docx").read_bytes()) == \
           parser.file_sha_from_bytes((b_dir / "投标文件.docx").read_bytes()), \
        "前置前提：两份文件字节必须一致"
    register(fresh_db, doc_id="A", rel="项目A/响应文件.docx", project="项目A")
    register(fresh_db, doc_id="B", rel="项目B/投标文件.docx", project="项目B")

    rA = parser.process_native(fresh_db, doc_row(fresh_db, "A"), roots)
    rB = parser.process_native(fresh_db, doc_row(fresh_db, "B"), roots)
    # 正文内容层校验（审查修复）：产物 text 必须含真实文档正文，且双来源都指向同一产物
    art = fresh_db.execute("SELECT text FROM parse_artifacts WHERE canonical_document_id=?",
                           (rA.content_sha,)).fetchone()
    assert art is not None and "完全相同的内容 ABC" in art["text"]

    assert rA.error is None and rA.new_artifact and rA.native_parser_called
    assert rB.error is None
    assert rB.cache_hit and not rB.native_parser_called and rB.files_read == 1
    assert rA.content_sha == rB.content_sha
    assert rA.canonical_document_id == rB.canonical_document_id
    assert fresh_db.execute("SELECT COUNT(*) FROM parse_artifacts").fetchone()[0] == 1
    ids = {r["document_id"] for r in fresh_db.execute("SELECT document_id FROM documents")}
    assert ids == {"A", "B"}
    rows = {r["document_id"]: r["canonical_document_id"] for r in fresh_db.execute(
        "SELECT document_id, canonical_document_id FROM documents")}
    assert rows["A"] == rows["B"] == rA.content_sha


# —— 5) 单文件失败不阻塞批处理（缺文件/坏文件）——红线回归 ——
def test_single_file_failure_does_not_block_batch(tmp_path, fresh_db):
    roots = roots_map(tmp_path)
    p = roots["2026年"] / "20260101-标书-某人-某项目"
    p.mkdir(parents=True, exist_ok=True)
    mk_docx(p / "响应文件.docx", ["正常文档"], fixed_ts=True)
    # 坏文件：非 zip 后缀伪装 .docx
    (p / "坏文件.docx").write_bytes(b"this is not a docx zip")
    register(fresh_db, doc_id="d-ok", rel="20260101-标书-某人-某项目/响应文件.docx",
             project="20260101-标书-某人-某项目")
    register(fresh_db, doc_id="d-bad", rel="20260101-标书-某人-某项目/坏文件.docx",
             project="20260101-标书-某人-某项目")
    # 缺文件：登记了但磁盘上没有
    register(fresh_db, doc_id="d-missing", rel="20260101-标书-某人-某项目/不存在.docx",
             project="20260101-标书-某人-某项目")

    r_bad = parser.process_native(fresh_db, doc_row(fresh_db, "d-bad"), roots)
    r_miss = parser.process_native(fresh_db, doc_row(fresh_db, "d-missing"), roots)
    assert r_bad.error is not None, "坏文件应返回 error 而不抛出"
    assert r_miss.error is not None and "读取源文件失败" in r_miss.error, "缺文件应返回 error 而不抛出"
    # 解析失败 → 旧 canonical 失效（不返回旧正文）
    assert doc_row(fresh_db, "d-bad")["canonical_document_id"] is None
    # 批未被阻塞：后续正常文档仍成功
    r_ok = parser.process_native(fresh_db, doc_row(fresh_db, "d-ok"), roots)
    assert r_ok.error is None and r_ok.new_artifact and r_ok.chars > 0

    # 同一坏文件再次处理（内容未变）→ 原生解析器仍被调用一次（错误路径每次如实执行），
    # 但 counts 一致性：read_files=1、error 非空、仍未产生 canonical
    r_bad2 = parser.process_native(fresh_db, doc_row(fresh_db, "d-bad"), roots)
    assert r_bad2.error is not None and doc_row(fresh_db, "d-bad")["canonical_document_id"] is None
    # 正常文档内容未变再处理 → 原生解析器增量 0
    r_ok2 = parser.process_native(fresh_db, doc_row(fresh_db, "d-ok"), roots, native_parser=_Litmus())
    assert r_ok2.cache_hit and not r_ok2.native_parser_called


# —— 6) 文件更新后解析失败 → canonical 失效，不返回旧正文（审查修复 high） ——
def test_update_then_parse_failure_invalidates_canonical(tmp_path, fresh_db):
    roots = roots_map(tmp_path)
    target = roots["2026年"] / "20260101-标书-某人-某项目" / "响应文件.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    mk_docx(target, ["旧正文 v1"], fixed_ts=True)
    register(fresh_db, doc_id="d1", rel="20260101-标书-某人-某项目/响应文件.docx",
             project="20260101-标书-某人-某项目")
    first = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)
    assert first.new_artifact
    old_sha = first.content_sha

    # 内容变化 + 解析失败（注入抛错）→ canonical 必须失效，绝不能继续指向旧正文
    mk_docx(target, ["新正文 v2 但解析会失败"], fixed_ts=True)
    def _boom(path):
        raise RuntimeError("模拟解析崩溃")
    r = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots, native_parser=_boom)
    assert r.error is not None
    assert doc_row(fresh_db, "d1")["canonical_document_id"] is None, \
        "解析失败后旧 canonical 必须失效（不返回旧正文）"
    assert doc_row(fresh_db, "d1")["error_message"] is not None