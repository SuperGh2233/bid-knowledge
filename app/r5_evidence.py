"""R5 证据提取可复用模块（代码自动提取；Agent 复核结论另行标注）。

核心语义（用户拍板修正）：
- merge_passages：只合并同一章节内命中的连续/相邻行；**text 必须严格等于该精确范围的完整原文**
  （text == "\n".join(lines[line_start-1 : line_end])，验证来自真实回读，不固定写 true）。
  命中位置与上下文范围分开保存：line_start/line_end=命中范围；context=扩展上下文（另存）。
- classify_purpose：培训正文（含 客户/用户/线上/现场/集中培训）优先归 培训正文；
  "分析/解析" 等词不得把培训正文误判为产品说明；产品说明单列。
- is_score_line：识别评分要求（得X分/评分标准/评审标准/打分/得分标准），评分行一律不进入正式证据。
- source_path_of / validate_report：来源指纹一致 + 范围对应 + 评分排除 + 用途/条件完整性校验，
  依据真实 source_root_id 构造路径。
"""
from __future__ import annotations

import re
from pathlib import Path

CHAPTER_TITLE = re.compile(r"^\d+(\.\d+)*\s+\S")
NOTE_LIKE = re.compile(r"^(注：|说明：|备注：|投标人可按)")

SOURCE_ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}

_SCORE_KW = ("得2分", "得1分", "评分项目", "评分标准", "评审标准", "打分", "得分标准")
_TRAIN_KW = ("培训", "对用户", "对客户", "线上培训", "现场培训", "集中培训")
_PRODUCT_KW = ("数据库", "测序平台", "表达矩阵", "OTU", "质谱", "标准品库",
               "定量检测", "靶向代谢", "[分析内容]", "[技术应用]", "[技术优势]",
               "试剂", "流程", "技术指标", "原理", "平台")
_ORG_KW = ("项目经理", "项目负责人", "项目小组", "附表", "人员情况", "团队成员")
_TENDER_KW = ("招标文件要求", "采购文件要求", "必须满足", "须提供", "无偏离")
_INTERNAL_KW = ("员工培训", "内部培训", "技术团队培训", "实验人员培训", "新员工")


def is_score_line(text: str) -> bool:
    return any(k in text for k in _SCORE_KW)


def is_section_boundary(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if NOTE_LIKE.match(s):
        return True
    return bool(CHAPTER_TITLE.match(s))


def classify_purpose(text: str, topic: str = "") -> str:
    """用途粗分。培训上下文（含客户/线上/现场/集中培训）优先，不受产品词影响。"""
    if topic == "培训安排" and "培训" in text and any(k in text for k in _TRAIN_KW[1:]):
        return "培训正文"
    if any(k in text for k in _SCORE_KW):
        return "评分要求"
    if any(k in text for k in _PRODUCT_KW):
        return "产品技术说明"
    if "目录" in text[:6] or text.count(".") > 8:
        return "目录"
    if any(k in text for k in _TENDER_KW):
        return "招标引用"
    if any(k in text for k in _ORG_KW):
        return "项目组织介绍"
    if any(k in text for k in _INTERNAL_KW):
        return "内部培训"
    return "方案正文"


def merge_passages(lines: list[str], kws: tuple[str, ...], gap: int = 4):
    """按行归并同一章节内命中的连续/相邻行（不跨章节、不拼接不连续带）。

    返回 [{line_start, line_end, text, context}]：
      - line_start/line_end = 命中范围（1-based）
      - text = 该精确范围完整原文（lines[line_start-1 : line_end] 按行 join）
      - context = 扩展上下文（命中范围 ±2 行），仅作参考
    """
    spans = []
    cur = None
    for i, ln in enumerate(lines):
        hit = bool(ln) and any(k in ln for k in kws)
        is_b = is_section_boundary(ln)
        if hit:
            if cur is None:
                cur = [i, i]
            else:
                if (not is_b) and i - cur[1] <= gap:
                    cur[1] = i
                else:
                    spans.append(tuple(cur))
                    cur = [i, i]
        elif cur is not None and (i - cur[1] > gap or is_b):
            spans.append(tuple(cur))
            cur = None
    if cur:
        spans.append(tuple(cur))

    n = len(lines)
    out = []
    for lo, hi in spans:
        exact = lines[lo:hi + 1]
        ctx = lines[max(0, lo - 2): min(n, hi + 3)]
        out.append({
            "line_start": lo + 1,
            "line_end": hi + 1,
            "line_count": hi - lo + 1,
            "text": "\n".join(exact),
            "context": "\n".join(ctx),
        })
    return out


def extract_evidence(lines: list[str], kws: tuple[str, ...], exclude: tuple[str, ...] = ()):
    """逐行收集关键词命中证据（跳过 exclude 与评分行），标记 代码提取。"""
    out = []
    for i, ln in enumerate(lines):
        if not any(k in ln for k in kws):
            continue
        if any(k in ln for k in exclude):
            continue
        if is_score_line(ln):
            continue
        out.append({"line": i + 1, "text": ln.strip(), "provenance": "code_extract"})
    return out


def source_path_of(doc_row: dict, roots: dict[str, Path] | None = None) -> str | None:
    """依据文档真实 source_root_id 构造完整源路径（不硬编码年份）。"""
    roots = roots or SOURCE_ROOTS
    root = roots.get(doc_row.get("source_root_id"))
    if root is None:
        return None
    return str(root.joinpath(*doc_row["relative_path"].split("/")))


def validate_report(report: dict, con, roots: dict[str, Path] | None = None) -> list[str]:
    """对落盘报告做真实校验，返回错误列表（空=通过）。

    检查：
      来源一致  - document_id 已登记；canonical_sha==documents.canonical_document_id；
                 full_source_path==source_path_of(doc)
      范围对应  - 每条 merged_passes.text == 该 ParseArtifact 指定范围完整原文
      评分排除  - 所有正式证据（矩阵）不含 is_score_line
      用途正确  - 培训正文含 培训+客户/用户/线上/现场/集中 → purpose=培训正文
      条件完整  - 交付物清单（L56/L61）不算交付时限；交付时限只有周期行（L69-70）
    """
    roots = roots or SOURCE_ROOTS
    errors: list[str] = []
    src = report.get("source") or {}
    doc = con.execute("SELECT * FROM documents WHERE document_id=?", (src.get("document_id"),)).fetchone()
    if doc is None:
        errors.append("source.document_id 未登记于 documents")
        return errors
    doc = dict(doc)
    if src.get("canonical_sha") != doc["canonical_document_id"]:
        errors.append(f"canonical_sha 不一致: {src.get('canonical_sha')} vs {doc['canonical_document_id']}")
    if src.get("relative_path") != doc["relative_path"]:
        errors.append("relative_path 与 documents 不一致")
    exp_path = source_path_of(doc, roots)
    if src.get("full_source_path") != exp_path:
        errors.append(f"full_source_path 不符（应为真实 source_root）: {src.get('full_source_path')} vs {exp_path}")
    art = con.execute("SELECT text FROM parse_artifacts WHERE canonical_document_id=?",
                      (doc["canonical_document_id"],)).fetchone()
    if art is None:
        errors.append("ParseArtifact 缺失，无法回读范围")
        return errors
    lines = (art["text"] or "").splitlines()
    for p in report.get("merged_passes", []):
        lo, hi = p.get("line_start") - 1, p.get("line_end") - 1
        if lo < 0 or hi >= len(lines) or lo > hi:
            errors.append(f"merged 范围越界 L{p.get('line_start')}-{p.get('line_end')}")
            continue
        expected = "\n".join(lines[lo:hi + 1])
        if p.get("text") != expected:
            errors.append(f"merged 文本与范围不符 L{p.get('line_start')}-{p.get('line_end')}")
    mat = report.get("matrix") or {}
    for area in ("售后团队", "服务周期", "培训安排"):
        for sub in mat.get(area, {}):
            for e in (mat[area][sub].get("evidence") or []):
                if is_score_line(e.get("text", "")):
                    errors.append(f"评分混入正式证据 {area}·{sub} L{e.get('line')}")
                if not e.get("line"):
                    errors.append(f"{area}·{sub} 证据缺少行号")
    # 用途：培训正文不被产品词误分类
    for p in report.get("merged_passes", []):
        if p.get("topic") == "培训安排" and "培训" in p.get("text", "") and \
                any(k in p["text"] for k in _TRAIN_KW[1:]):
            if p.get("purpose") not in ("培训正文", "方案正文"):
                errors.append(f"培训正文被误分类: L{p.get('line_start')} purpose={p.get('purpose')}")
    return errors