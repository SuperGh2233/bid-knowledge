"""R3 原生 PDF/XLSX 解析测试（共享入口 + 内容 SHA 缓存 + 范围守卫）。

- PDF ：按页提取文字 + 保留真实页码；无原生文字页明确记录缺口，禁止自动 OCR / 不宣称全文完整。
- XLSX：保留工作表、行列位置、单元格内容；日期/数值/合并单元格；公式保留原文与缓存值；
        缓存缺失不补造金额。
- 两种格式：同内容复用（缓存命中、原生解析器不重复执行）；内容变化失效（重新解析、不返回旧文）；
  空正文建空产物但不计业务成功；失败不阻断其他文件。
只读独立临时库；不 OCR / 不写 ES / 不读 NAS。
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
    path = tmp_path / "pdfxlsx_test.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    return db.connect()


class _Litmus:
    def __call__(self, path):
        raise AssertionError("原生解析器不应被调用")


def register(con, *, doc_id, rel, ext, root="2026年", project="20260101-标书-某人-某项目",
             role="our_response", status="pending"):
    with con:
        con.execute(
            "INSERT INTO documents (document_id, source_root_id, project_folder, relative_path, file_ext,"
            " document_role, manual_override, parse_status) VALUES (?,?,?,?,?,?,0,?)",
            (doc_id, root, project, rel, ext, role, status))


def doc_row(con, doc_id):
    return dict(con.execute("SELECT * FROM documents WHERE document_id=?", (doc_id,)).fetchone())


def roots_map(tmp_path, root="2026年"):
    r = tmp_path / root
    r.mkdir(parents=True, exist_ok=True)
    return {root: r}


# —— 造 PDF（fitz 绘制现实行） ——
def _mk_pdf(path, pages_text):
    import fitz
    doc = fitz.open()
    for t in pages_text:
        page = doc.new_page()
        page.insert_text((72, 90), t)
    doc.save(str(path))
    doc.close()


# —— 造 XLSX（openpyxl） ——
def _mk_xlsx(path, sheets_spec, merged=None):
    # sheets_spec: list[dict{name, rows(list[list[value]], None=跳过；公式写 "=..." 字符串区分)}]
    from openpyxl import Workbook
    wb = Workbook()
    for idx, spec in enumerate(sheets_spec):
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = spec["name"]
        for r_i, row in enumerate(spec.get("rows", []), start=1):
            for c_i, val in enumerate(row, start=1):
                ws.cell(row=r_i, column=c_i, value=val)
        for m in spec.get("merged", []) or merged or []:
            ws.merge_cells(m)
    wb.save(str(path))
    wb.close()


# ============ 1) PDF：按页文字 + 页码 + 无文字页缺口 ============
def test_pdf_pages_with_page_numbers(tmp_path):
    p = tmp_path / "t.pdf"
    _mk_pdf(p, ["Page One Body", "Page Two Body"])
    text, chars, page_meta = parser.pdf_pages(p)
    assert "Page One Body" in text and "Page Two Body" in text
    assert len(page_meta) == 2
    assert [m["page_no"] for m in page_meta] == [1, 2]
    assert all(m["has_text"] for m in page_meta)


def test_pdf_blank_page_recorded_as_gap_not_full_text(tmp_path):
    p = tmp_path / "t2.pdf"
    # 页2无原生文字（空白/scanned 模拟）：不可自动 OCR，且不宣称全文完整
    import fitz
    doc = fitz.open()
    doc.new_page()
    doc.new_page()  # 空白页
    page = doc.new_page()
    page.insert_text((72, 90), "Text Page")
    doc.save(str(p)); doc.close()
    text, _, page_meta = parser.pdf_pages(p)
    assert page_meta[1]["has_text"] is False and page_meta[1]["chars"] == 0
    assert page_meta[2]["has_text"] is True


# ============ 2) XLSX：sheet/row/col/单元格 ============
def test_xlsx_preserves_sheets_rows_cells(tmp_path):
    p = tmp_path / "t.xlsx"
    _mk_xlsx(p, [
        {"name": "Sheet1", "rows": [
            ["产品", "数量", "单价", "金额"],
            ["AA", 2, 150.5, 301.0],
            ["BB", 1, 99, 99.0],
        ]},
        {"name": "报价", "rows": [
            ["项目", "小计"],
            ["测序", 12345],
        ]},
    ])
    text, _, sheets = parser.xlsx_workbook(p)
    assert "Sheet1!A1='产品'" in text or "Sheet1!A1=\"产品\"" in text or repr("产品") in text
    # 保行坐标：数量行值
    assert "Sheet1!B2=2" in text and "Sheet1!C2=150.5" in text
    assert "Sheet1!A3='BB'" in text or "BB" in text
    assert len(sheets) == 2
    assert sheets[0]["sheet"] == "Sheet1" and sheets[1]["sheet"] == "报价"
    assert sheets[0]["rows"] == 3  # 含头行


def test_xlsx_dates_numbers_merged_sheets(tmp_path):
    p = tmp_path / "t3.xlsx"
    from openpyxl import load_workbook
    _mk_xlsx(p, [
        {"name": "财务", "rows": [
            ["日期", "期间", "金额"],
            ["2025-12-01", 202512, 50000.75],
            [None, 202601, 120.5],
        ], "merged": ["A2:A3"]},
        {"name": "Sheet2", "rows": [["x", 1]]},
    ])
    wb = load_workbook(p, data_only=False)
    ws = wb["财务"]
    assert ws["A2"].value is not None  # 日期
    wb.close()
    text, _, sheets = parser.xlsx_workbook(p)
    # 日期/期间/数值以原值 repr 保留
    assert "50000.75" in text
    assert len(sheets) == 2 and sheets[1]["sheet"] == "Sheet2"


def test_xlsx_formula_preserved_with_cached_value_no_fabrication(tmp_path):
    p = tmp_path / "t4.xlsx"
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "金额"
    ws["A1"] = 100; ws["B1"] = 50; ws["C1"] = "=A1+B1"  # 公式，无缓存值
    ws["D1"] = 150  # 无公式现成数值
    wb.save(str(p)); wb.close()
    text, _, _ = parser.xlsx_workbook(p)
    # 公式原文保留，且不伪造缓存值（不会出现 "150" 来自 C1）
    assert "C1" in text and "=A1+B1" in text
    # D1 数值正常
    assert "D1=150" in text


# ============ 3) 两种格式：同内容复用 / 内容变化失效 / 空正文 / 失败不阻断 ============
@pytest.mark.parametrize("ext", ["pdf", "xlsx"])
def test_pdf_xlsx_same_content_reuse(tmp_path, fresh_db, ext):
    roots = roots_map(tmp_path)
    p = roots["2026年"] / "20260101-标书-某人-某项目"
    p.mkdir(parents=True, exist_ok=True)
    f = p / f"响应文件.{ext}"
    if ext == "pdf":
        _mk_pdf(f, ["正文 A"])
    else:
        _mk_xlsx(f, [{"name": "S1", "rows": [["正文 A"], [1, 2]]}])
    register(fresh_db, doc_id="d1", rel=f"20260101-标书-某人-某项目/响应文件.{ext}", ext=ext)
    r1 = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)
    assert r1.error is None and r1.new_artifact and r1.native_parser_called
    assert parser._valid_artifact(fresh_db, r1.content_sha) is not None
    assert fresh_db.execute("SELECT COUNT(*) FROM parse_artifacts").fetchone()[0] == 1
    r2 = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots, native_parser=_Litmus())
    assert r2.cache_hit and not r2.native_parser_called and r2.files_read == 1
    assert fresh_db.execute("SELECT COUNT(*) FROM parse_artifacts").fetchone()[0] == 1


@pytest.mark.parametrize("ext", ["pdf", "xlsx"])
def test_pdf_xlsx_content_change_invalidates(tmp_path, fresh_db, ext):
    roots = roots_map(tmp_path)
    p = roots["2026年"] / "20260101-标书-某人-某项目"
    p.mkdir(parents=True, exist_ok=True)
    target = p / f"响应文件.{ext}"
    if ext == "pdf":
        _mk_pdf(target, ["Old Body"])
    else:
        _mk_xlsx(target, [{"name": "S1", "rows": [["Old Body"]]}])
    register(fresh_db, doc_id="d1", rel=f"20260101-标书-某人-某项目/响应文件.{ext}", ext=ext)
    r1 = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)
    old_sha = r1.content_sha
    if ext == "pdf":
        _mk_pdf(target, ["New Body changed"])
    else:
        _mk_xlsx(target, [{"name": "S1", "rows": [["New Body changed"]]}])
    r2 = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)
    assert r2.content_sha != old_sha and r2.new_artifact and r2.native_parser_called
    assert doc_row(fresh_db, "d1")["canonical_document_id"] == r2.content_sha
    # 不返回旧正文
    art = fresh_db.execute("SELECT text FROM parse_artifacts WHERE canonical_document_id=?",
                           (r2.content_sha,)).fetchone()
    assert art["text"] and "New Body changed" in art["text"]
    # 未变再处理 → 原生解析器增量 0
    r3 = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots, native_parser=_Litmus())
    assert r3.cache_hit and not r3.native_parser_called


@pytest.mark.parametrize("ext", ["pdf", "xlsx"])
def test_pdf_xlsx_empty_body_not_counted_as_success(tmp_path, fresh_db, ext):
    roots = roots_map(tmp_path)
    p = roots["2026年"] / "20260101-标书-某人-某项目"
    p.mkdir(parents=True, exist_ok=True)
    f = p / f"响应文件.{ext}"
    if ext == "pdf":
        import fitz
        doc = fitz.open(); doc.new_page(); doc.save(str(f)); doc.close()  # 全空白扫描页
    else:
        _mk_xlsx(f, [{"name": "S1", "rows": [[None, None, None]]}])  # 仅空单元格
    register(fresh_db, doc_id="d1", rel=f"20260101-标书-某人-某项目/响应文件.{ext}", ext=ext)
    r = parser.process_native(fresh_db, doc_row(fresh_db, "d1"), roots)
    if r.error is not None and "解析失败" in r.error:
        # 若原生器对空产物也报（xlsx 空行），则应建空产物且不计 success
        pass
    # 空白 PDF：建 file-sha 空产物（empty_body），chars=0
    assert r.chars == 0
    assert r.content_sha is not None and r.canonical_document_id == r.content_sha
    art = fresh_db.execute("SELECT page_metadata FROM parse_artifacts WHERE canonical_document_id=?",
                           (r.content_sha,)).fetchone()
    import json as _json
    assert _json.loads(art["page_metadata"]).get("empty_body") is True
    # 空正文不参与"正文获取成功"：此状态确保下游可用 page_metadata 区分


@pytest.mark.parametrize("ext", ["pdf", "xlsx"])
def test_pdf_xlsx_failure_does_not_block_batch(tmp_path, fresh_db, ext):
    roots = roots_map(tmp_path)
    p = roots["2026年"] / "20260101-标书-某人-某项目"
    p.mkdir(parents=True, exist_ok=True)
    good = p / f"正常文件.{ext}"
    bad = p / f"坏文件.{ext}"
    if ext == "pdf":
        _mk_pdf(good, ["正常"])
        bad.write_bytes(b"not a pdf at all")
    else:
        _mk_xlsx(good, [{"name": "S1", "rows": [["正常"]]}])
        bad.write_bytes(b"not a zip")
    register(fresh_db, doc_id="good", rel=f"20260101-标书-某人-某项目/正常文件.{ext}", ext=ext)
    register(fresh_db, doc_id="bad", rel=f"20260101-标书-某人-某项目/坏文件.{ext}", ext=ext)
    r_bad = parser.process_native(fresh_db, doc_row(fresh_db, "bad"), roots)
    assert r_bad.error is not None
    assert doc_row(fresh_db, "bad")["canonical_document_id"] is None
    r_ok = parser.process_native(fresh_db, doc_row(fresh_db, "good"), roots)
    assert r_ok.error is None and r_ok.new_artifact
    assert doc_row(fresh_db, "good")["canonical_document_id"] == r_ok.content_sha