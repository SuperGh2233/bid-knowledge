"""R3 原生解析共享入口（缓存感知，D3A/D13）。

用户拍板修订（复用前必须验证源文件内容有效性）：
- 每份文件先读取源文件原始字节，`file_sha = sha256(文件字节)` 作为**内容身份**（可检测 NAS 文件更新）。
- 命中：parse_artifacts 中已有 `sha256 = file_sha` 且 `parser_version` 一致的产物 →
  继承 canonical、复用正文，**不调用原生解析器**。
- miss：才调用原生解析器 `docx_full` 提取全文（段落+表格保序）并写入新 ParseArtifact。
- `documents.canonical_document_id` 恒指向 PDF/正文对应 ParseArtifact 的内容键（= file_sha），
  文件字节变化 → file_sha 变化 → 必然重新解析，不会返回旧正文。
- 失败路径一致性：解析异常或产物建立失败时，`documents.canonical_document_id` 置 NULL、
  记录 error_message（不保留旧 canonical 指向旧正文），并在副本库明确；不删除既有 artifact
  （其他来源可能仍引用）。
- 合法空正文：文件字节非空但 `docx_full` 返回空正文 → 仍建立 file-sha 空产物（幂等、不每轮重解析），
  仅当文件字节为空或解析抛出异常才走 error 路径。
- 计数口径（独立于报告）：
  files_read         = 成功读取源文件字节计算内容 SHA 的次数（read_bytes 成功 +1；FileNotFound 前不计）；
  native_parser_calls = 原生解析器 docx_full 被调用的次数（含成功建产物与错误路径各 +1）；
  cache_hits         = 命中既有同版本产物（未产生新 artifact、未调用原生解析器）；
  new_artifacts      = 本次新写入 ParseArtifact 的数量。
- 所有写库（documents 关联 + INSERT artifact）在 `with con:` 事务内，保证原子性（无半写窗口）。
- 单文件失败不阻塞批处理。

### 范围守卫（人工覆盖≠阻断）
- 阻断条件 = 待复核 / 暂停处理 / 不允许解析状态（BLOCK_PARSE_STATUSES：holding_review、paused、blocked、skipped）
  或 角色不在 do解析白名单（PARSE_SET_ROLES）。
- `manual_override=1` **不**单独阻断：已确证的 `our_response/final_signed/contract_evidence + pending +
  manual_override=1` 可正常解析（人工角色纠正应被尊重，而非一刀切挡死）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PARSER_NAME = "py-docx-v1"
PARSER_VERSION = "0.1.0"
CONTENT_FORMAT = "native_text"

# PDF/XLSX 原生解析（复用同一共享入口；格式由 file_ext 路由）
NATIVE_DISPATCH = {
    "docx": "docx", "docm": "docx", "doc": "docx",      # 旧 .doc 也尝试（python-docx 失败即 unsupported）
    "pdf": "pdf", "xlsx": "xlsx", "xlsm": "xlsx",
}
SUPPORTED_EXTS = set(NATIVE_DISPATCH)
TEXT_FORMAT_MAP = {"docx": "native_text", "pdf": "native_pdf_text", "xlsx": "native_xlsx_text"}

PARSE_SET_ROLES = ("our_response", "final_signed", "contract_evidence",
                   # —— 2026-09-13 扩容：需求要定位的三类材料**住在这些角色里** ——
                   # 实测：完税证明/社保/财务统计表在 `qualification_evidence` 与 `tender_requirement`，
                   # 付款凭证/发票在 `process_material`，另有相当数量归到 `unknown`。
                   # 原白名单只有三类 → 这些材料**整块解析不了**，用户要的
                   # 「财务社保数据」「仪器设备清单」「收款凭证」因此全部查不到（成功 0 份）。
                   #
                   # **为什么安全**：`process_native` 全程本地（不调 OCR、不调任何网关），
                   # 扩白名单**不引入任何外发**；且它不改检索资格门槛（`search.VALID_ROLES` 仍是
                   # our_response/final_signed），故不会污染合同定位与方案证据的判定。
                   # 单纯让「已登记的文件能被读出正文」，供材料事实抽取使用。
                   "process_material", "qualification_evidence",
                   "tender_requirement", "unknown")

# 阻断解析的状态：待复核 / 暂停 / 不允许解析（人工角色纠正 override=1 不在此列）
BLOCK_PARSE_STATUSES = ("holding_review", "paused", "blocked", "skipped")

CANDIDATE_SQL = """
    SELECT document_id, source_root_id, project_folder, relative_path, document_role,
           manual_override, parse_status, canonical_document_id, sha256, file_ext
    FROM documents
    WHERE parse_status='pending'
      AND document_role IN ('our_response','final_signed','contract_evidence')
      AND manual_override=0
    ORDER BY document_id
"""


@dataclass(frozen=True)
class ParseResult:
    """一次 process_native 的完整结果（计数独立于报告，便于审计）。"""
    doc_id: str
    rel: str
    file_sha: str | None = None
    content_sha: str | None = None
    canonical_document_id: str | None = None
    cache_hit: bool = False
    new_artifact: bool = False
    native_parser_called: bool = False
    files_read: int = 0
    chars: int | None = None
    note: str = ""
    error: str | None = None


@dataclass
class ParseStats:
    native_parser_calls: int = 0
    files_read: int = 0
    cache_hits: int = 0
    new_artifacts: int = 0
    processed: int = 0
    errors: int = 0
    results: list[ParseResult] = field(default_factory=list)


# —— 原生解析器本体（miss 时才执行；测试可注入石蕊函数） ——
def docx_full(path) -> tuple[str, int]:
    """完整正文（段落+表格，保阅读顺序）→ (text, char_count)。"""
    text, chars, _ = docx_full_styled(path)
    return text, chars


_HEADING_STYLE = re.compile(r"^\s*(?:Heading|标题|heading)\s*([1-9])\s*$", re.IGNORECASE)


def _style_chain(p) -> list:
    """段落样式及其继承链（自身 → 基样式 → …），用于取大纲级别。"""
    chain, seen, style = [], set(), getattr(p, "style", None)
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        chain.append(style)
        style = getattr(style, "base_style", None)
    return chain


def _outline_level(p) -> int | None:
    """段落大纲级别（1-based）；取不到返回 None。

    OOXML 的 w:outlineLvl 取值 0–8 表示大纲级别，**9 表示"正文"（非标题）**，
    必须排除——否则每段正文都会被当成 10 级标题（实测多数文档 9 值即正文占绝大多数）。

    优先段落自身 pPr/outlineLvl，其次样式链上的 outlineLvl，
    最后回退样式名（Heading 1 / 标题 1）。
    """
    from docx.oxml.ns import qn

    def _from_element(el) -> int | None:
        lvl = el.find(qn("w:outlineLvl"))
        if lvl is None:
            return None
        v = lvl.get(qn("w:val"))
        if v is not None and v.strip().isdigit() and 0 <= int(v) <= 8:
            return int(v) + 1
        return None

    pPr = getattr(p._p, "pPr", None)
    if pPr is not None:
        found = _from_element(pPr)
        if found:
            return found
    for style in _style_chain(p):
        el = getattr(style.element, "pPr", None)
        if el is not None:
            found = _from_element(el)
            if found:
                return found
        m = _HEADING_STYLE.match(getattr(style, "name", "") or "")
        if m:
            return int(m.group(1))
    return None


def docx_full_styled(path) -> tuple[str, int, dict]:
    """完整正文 + 段落大纲结构。

    **正文必须与历史 docx_full 逐字节一致**——冻结示范稿的行号引用
    （L69–70 / L521–535 / L949–985）依赖它，改一个字符即失效。
    大纲信息只作为并行元数据返回，不进入 text。

    返回 (text, char_count, structure)；structure 形如
      {"outline": [{"line": 12, "style": "Heading 1", "level": 1}, ...]}
    line 为 text 的 1-based 行号（与 splitlines() 对齐）。
    """
    import docx as d
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = d.Document(str(path))
    out: list[str] = []
    outline: list[dict] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            out.append(text)
            level = _outline_level(p)
            if level:
                outline.append({"line": len(out), "level": level,
                                "style": (getattr(p.style, "name", "") or "")[:40]})
        elif child.tag == qn("w:tbl"):
            t = Table(child, doc)
            for row in t.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    out.append(" | ".join(cells))
    full = "\n".join(out)
    return full, len(full), ({"outline": outline} if outline else {})


# —— DOCX 断链关系修复（D17）——
# 现象：部分标书制作软件生成/合并的 DOCX，在 word/_rels/document.xml.rels 里留下
# `Target="../NULL" Type=".../image"` 这类指向不存在部件的 Internal 关系。
# python-docx 加载时会急切解析全部关系，读到 NULL 即抛
# KeyError: "There is no item named 'NULL' in the archive"，导致正文完全取不到。
# 处理：**只在本地临时副本上**摘除断链关系后解析；NAS 原件只读、绝不改写。
# 如实记账：摘除了几条关系写进 page_metadata.structure.docx_repair，不伪装成未修复。

def _rels_base_dir(rels_name: str) -> str:
    """OPC 约定：部件 X 的关系放在 dirname(X)/_rels/basename(X).rels。

    包级 `_rels/.rels` 的基准目录是 ''（相对包根）。
    """
    p = PurePosixPath(rels_name)
    if p.parent.name == "_rels":
        owner = p.parent.parent
        return "" if str(owner) == "." else str(owner)
    return ""


def _resolve_part_uri(base: str, target: str) -> str:
    """把关系 Target 解析成包内部件路径（规范化 . 与 ..）。"""
    joined = (PurePosixPath(base) / target) if base else PurePosixPath(target)
    parts: list[str] = []
    for seg in joined.parts:
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in (".", ""):
            parts.append(seg)
    return "/".join(parts)


def _iter_broken_rels(zf: zipfile.ZipFile, names: set[str]) -> dict[str, set[str]]:
    """找出指向不存在内部部件的 Internal 关系：{rels 路径: {rId, ...}}。

    - External（TargetMode=External）不校对。
    - 主文档关系（Type 以 /officeDocument 结尾）永不摘除：缺了它应当如实失败，
      不能靠修复掩盖一个真正损坏的文件。
    """
    broken: dict[str, set[str]] = {}
    for rels_name in [n for n in names if n.endswith(".rels")]:
        base = _rels_base_dir(rels_name)
        try:
            root = ET.fromstring(zf.read(rels_name))
        except ET.ParseError:
            continue
        for rel in root:
            if rel.get("TargetMode") == "External":
                continue
            if (rel.get("Type") or "").endswith("/officeDocument"):
                continue
            target = (rel.get("Target") or "").strip()
            if not target:
                continue
            if _resolve_part_uri(base, target) not in names:
                rid = rel.get("Id")
                if rid:
                    broken.setdefault(rels_name, set()).add(rid)
    return broken


def _strip_relationships(xml_bytes: bytes, ids: set[str]) -> bytes:
    """从 .rels XML 中移除指定 Id 的 Relationship 元素。"""
    root = ET.fromstring(xml_bytes)
    for rel in list(root):
        if rel.get("Id") in ids:
            root.remove(rel)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _repair_docx(src: Path, tmp_dir: Path) -> tuple[Path, int]:
    """无断链 → 返回 (原路径, 0)；有断链 → 写本地临时副本并返回 (副本, 摘除数)。"""
    with zipfile.ZipFile(src) as zin:
        names = set(zin.namelist())
        broken = _iter_broken_rels(zin, names)
        if not broken:
            return src, 0
        removed = sum(len(v) for v in broken.values())
        dst = tmp_dir / f"repaired-{hashlib.sha1(str(src).encode('utf-8')).hexdigest()[:12]}.docx"
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename in broken:
                    data = _strip_relationships(data, broken[item.filename])
                zout.writestr(item, data)
    return dst, removed


def docx_full_ex(path) -> tuple[str, int, dict]:
    """docx_full_styled + 断链修复；返回 (text, chars, structure)。

    structure 含 outline（大纲结构）与 docx_repair（修复留痕），供 page_metadata 记录。
    """
    with tempfile.TemporaryDirectory(prefix="bid-docx-repair-") as td:
        target, removed = _repair_docx(Path(path), Path(td))
        text, chars, structure = docx_full_styled(target)
    if removed:
        structure = {**structure, "docx_repair": {"removed_relationships": removed}}
    return text, chars, structure


def content_sha(text: str) -> str:
    """兼容保留：正文 UTF-8 SHA-256（历史用途；当前 ParseArtifact 内容键改用文件字节 SHA）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha_from_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# —— PDF 原生：按页提取文字，保留真实页码；无文字页记录缺口（不 OCR、不宣称全文完整） ——
def pdf_pages(path) -> tuple[str, int, list[dict]]:
    import fitz  # PyMuPDF
    with fitz.open(str(path)) as doc:
        n = len(doc)
        parts = []
        page_meta = []
        for i, pg in enumerate(doc, start=1):
            t = pg.get_text("text") or ""
            lines = [ln.rstrip() for ln in t.splitlines() if ln.strip()]
            text = "\n".join(lines)
            if text.strip():
                parts.append(text)
            page_meta.append({"page_no": i, "chars": len(text), "has_text": bool(text.strip()),
                              "text_len": len(text)})
        full = "\n".join(parts)
        return full, len(full), page_meta


# —— XLSX 原生：保留工作表、行列位置、单元格内容；公式保留原文与缓存值 ——
def xlsx_workbook(path) -> tuple[str, int, list[dict]]:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=False)  # data_only=False → 公式原文
    parts = []
    sheets = []
    for ws in wb.worksheets:
        sn = ws.title
        cells = []
        rows = list(ws.iter_rows(values_only=False))
        for r_i, r in enumerate(rows, start=1):
            line = []
            for c in r:
                if c.value is None:
                    continue
                v = c.value
                line.append(f"{sn}!{c.coordinate}={v!r}")
            if line:
                parts.append(" | ".join(line))
                cells.extend(line)
        sheets.append({"sheet": sn, "rows": len(rows), "cells": len(cells), "sample": cells[:8]})
    full = "\n".join(parts)
    return full, len(full), sheets


# 2026-09-14 清理：删掉 `_escape_text_meta` 与 `_native` 两个空壳。
# `_escape_text_meta` 是「把 sheet 样本截断再塞进 page_metadata」的截断逻辑 ——
#   live 的 `_compute_native` **直接** `return text, chars, {"sheets": sheets}`（未经截断），
#   即这段截断**从未被接线**，删掉不改变任何现有行为。
# `_native` 只有 3 行 `return text, {"structure": text_meta}`，逻辑已内联进 `_compute_native`。
# 两者全仓（含兄弟仓库 bid-ai/）grep 均只有定义行本身，无测试引用。


def parse_candidates(con, limit: int | None = None) -> list[dict]:
    """真实候选函数：与 R2 待核隔离回查共用一个 SQL。"""
    sql = CANDIDATE_SQL + (f" LIMIT {limit}" if limit is not None else "")
    return [dict(r) for r in con.execute(sql)]


def _extension_of(path, doc) -> str:
    ext = (doc.get("file_ext") or "").lstrip(".").casefold()
    if not ext:
        ext = Path(str(path)).suffix.lstrip(".").casefold()
    return ext


def _compute_native(doc, path, native_parser=None) -> tuple[str, int, dict, str]:
    """按扩展名选择原生解析器，返回 (text, chars, structure_meta, content_format)。

    无原生解析器（unsupported ext）→ raise UnsupportedNative；测试可注入 native_parser
    （约定返回 2 元组 (text, chars)，structure 为空）。
    """
    ext = _extension_of(path, doc)
    if native_parser is not None:
        out = native_parser(path)
        if isinstance(out, tuple) and len(out) == 2:
            return out[0], out[1], {}, TEXT_FORMAT_MAP.get(ext, CONTENT_FORMAT)
        return out[0], out[1], out[2], TEXT_FORMAT_MAP.get(ext, CONTENT_FORMAT)
    kind = NATIVE_DISPATCH.get(ext)
    if kind == "docx":
        text, chars, structure = docx_full_ex(path)
        return text, chars, structure, CONTENT_FORMAT
    if kind == "pdf":
        text, chars, page_meta = pdf_pages(path)
        return text, chars, {"pages": page_meta}, TEXT_FORMAT_MAP["pdf"]
    if kind == "xlsx":
        text, chars, sheets = xlsx_workbook(path)
        return text, chars, {"sheets": sheets}, TEXT_FORMAT_MAP["xlsx"]
    raise UnsupportedNative(f"暂无原生解析器: {ext or '?'}")


def _doc_path(source_root_id: str, project_folder: str, incoming_rel: str, roots: dict[str, object]) -> str:
    """相对源根完整路径。incoming_rel 已含项目层（source_root/项目/…内部路径），不重复拼 project。"""
    root = roots.get(source_root_id)
    if root is None:
        raise FileNotFoundError(f"source_root_id 未配置: {source_root_id}")
    _ = project_folder
    parts = [p for p in incoming_rel.split("/") if p]
    return str(Path(str(root)).joinpath(*parts))


def _valid_artifact(con, file_sha: str) -> dict | None:
    """同内容 SHA + 同解析器（name+version）的有效产物。"""
    row = con.execute(
        "SELECT canonical_document_id, sha256, parser_name, parser_version, content_format "
        "FROM parse_artifacts WHERE sha256=? AND parser_name=? AND parser_version=? LIMIT 1",
        (file_sha, PARSER_NAME, PARSER_VERSION)).fetchone()
    return dict(row) if row else None


def process_native(con, doc: dict, roots: dict[str, object], native_parser=None) -> ParseResult:
    """共享处理入口：先读源文件字节算 file_sha → 命中同版本产物即复用，miss 才解析。

    native_parser 可注入（测试用；命中缓存后被调用即视为失败——石蕊函数抛错）。
    """
    if native_parser is None:
        native_parser = None  # 由 _compute_native 按扩展名调度原生解析器
    doc_id = doc["document_id"]
    rel = doc["relative_path"]

    # —— 范围守卫（红线兜底）：绕过 parse_candidates 直接调用也不得处理待核/暂停/不允许解析角色 ——
    if doc.get("parse_status") in BLOCK_PARSE_STATUSES:
        return ParseResult(doc_id, rel, error=f"{doc.get('parse_status')} 待核/暂停，禁止解析")
    if doc.get("document_role") not in PARSE_SET_ROLES:
        return ParseResult(doc_id, rel, error=f"角色不在解析集: {doc.get('document_role')} 禁止解析")
    # manual_override=1 不阻断：已确证的人工角色纠正可正常解析（relevance 由调用方候选 SQL 控制）

    try:
        # —— 读源文件原始字节，内容身份 = file_sha（此读取每文件恰好一次） ——
        try:
            path = _doc_path(doc["source_root_id"], doc["project_folder"], rel, roots)
            raw = Path(path).read_bytes()
        except Exception as e:  # noqa: BLE001 —— 文件缺失等路径不丢批
            return ParseResult(doc_id, rel, error=f"读取源文件失败:{str(e)[:120]}")
        file_sha = file_sha_from_bytes(raw)
        files_read = 1

        # —— 复用：同版本产物命中 → 继承 canonical，不解析 ——
        cached = _valid_artifact(con, file_sha)
        if cached is not None:
            canonical = cached["canonical_document_id"]
            with con:
                con.execute("UPDATE documents SET canonical_document_id=?, sha256=?, content_format=?, "
                            "error_message=NULL WHERE document_id=?",
                            (canonical, file_sha, cached["content_format"], doc_id))
            return ParseResult(doc_id, rel, file_sha=file_sha, content_sha=file_sha,
                               canonical_document_id=canonical, cache_hit=True,
                               files_read=files_read, native_parser_called=False,
                               note="命中同文件字节SHA产物，复用既有正文")

        # —— miss：按扩展名调度原生解析器并写入新产物 ——
        try:
            text, chars, structure, text_fmt = _compute_native(doc, path, native_parser)
        except Exception as e:  # noqa: BLE001
            # 解析失败：失效旧 canonical（避免旧正文被当作当前内容返回），记录错误，不删除既有 artifact
            with con:
                con.execute("UPDATE documents SET canonical_document_id=NULL, error_message=? WHERE document_id=?",
                            (f"解析失败:{str(e)[:120]}", doc_id))
            return ParseResult(doc_id, rel, file_sha=file_sha,
                               files_read=files_read, native_parser_called=True,
                               error=f"解析失败:{str(e)[:120]}")
        if not text.strip():
            # 合法空正文（文件字节非空但正文无可复制文字）：仍建立 file-sha 空产物，幂等
            with con:
                con.execute("INSERT OR REPLACE INTO parse_artifacts "
                            "(canonical_document_id, sha256, text, page_metadata, content_format, parser_name, parser_version) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (file_sha, file_sha, "", json.dumps({"pages": 1, "order": "body",
                                                                  "empty_body": True, "structure": structure}) if structure else
                             json.dumps({"pages": 1, "order": "body", "empty_body": True}),
                             text_fmt, PARSER_NAME, PARSER_VERSION))
                con.execute("UPDATE documents SET canonical_document_id=?, sha256=?, content_format=?, "
                            "error_message=NULL WHERE document_id=?",
                            (file_sha, file_sha, text_fmt, doc_id))
            return ParseResult(doc_id, rel, file_sha=file_sha, content_sha=file_sha,
                               canonical_document_id=file_sha, new_artifact=True,
                               files_read=files_read, native_parser_called=True, chars=0,
                               note="合法空正文（无可复制文字），已建空产物")
        with con:
            meta = {"pages": 1, "order": "body"}
            if structure:
                meta["structure"] = structure
            con.execute("INSERT OR REPLACE INTO parse_artifacts "
                        "(canonical_document_id, sha256, text, page_metadata, content_format, parser_name, parser_version) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (file_sha, file_sha, text, json.dumps(meta, ensure_ascii=False),
                         text_fmt, PARSER_NAME, PARSER_VERSION))
            con.execute("UPDATE documents SET canonical_document_id=?, sha256=?, content_format=?, "
                        "error_message=NULL WHERE document_id=?",
                        (file_sha, file_sha, text_fmt, doc_id))
        return ParseResult(doc_id, rel, file_sha=file_sha, content_sha=file_sha,
                           canonical_document_id=file_sha, new_artifact=True,
                           files_read=files_read, native_parser_called=True, chars=chars,
                           note="新内容，已解析并写入产物")
    except Exception as e:  # noqa: BLE001 —— 兜底：单文件失败不阻塞批处理
        return ParseResult(doc_id, rel, error=f"处理失败:{str(e)[:120]}")


class UnsupportedNative(Exception):
    """暂不支持的原生格式（.doc/.xls 等）：记 unsupported，不伪装成功。"""