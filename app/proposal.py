"""R7 模块级方案生成 —— **本地机械件**（需求小节 → 候选池 → 角色过滤 → 近重复聚类 → Evidence Pack）。

计划 R7-01/R7-03/R7-04/R7-05/R7-07 的本地部分。**全程不外发**：
召回走 ES BM25（不做 kNN，避免把查询/正文送 embedding 网关），
近重复判定走本地字符 shingle 相似度。

关键约束（R7-03，逐条对应）：
  - 每个小节先召回 **20–30 候选池**（`pool_size`）→ 角色过滤 → 近重复/模板聚类 → 去重后 **3~5** 条 Evidence Pack
    （**5=硬上限，3=软下限**）；同项目默认 **≤2** 条（diversity 软策略）。
  - **近重复去除后才计数**：不同项目复制自同一模板**不得**视为独立证据。
  - 去重后 <3 条 → `sparse`；0 条 → `insufficient`。**不得用低质/重复材料补足**（宁可报缺口）。
  - 角色：只允许 `our_response` / `final_signed`（R5-01/D2A：竞品、招标、未知一律不进方案证据）。

**生成步骤（把证据包交给模型）不在本模块默认路径内**：见 `generate_proposal`，
默认关闭、未授权即抛错（同 `app/ocr.py` 的 `_guard()` 模式）。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import NamedTuple
from dataclasses import dataclass, field
from pathlib import Path

import app.config as config
from app.search import DEFAULT_ROOTS

# —— 方案模块词表（计划 §2 需求二；2026-09-14 起为 8 个，"只保留方案类"）——
# 关键词表**放在 `app/module_keywords.json`，不再硬编码**：业务新增/调整方案小节
# 若每次都要改代码发版，模块化就名不副实。改词表只动配置、不动代码。
#
# 关键词刻意写具体、互不重叠：`section_type` **在 ES 里全是 `unclassified`**
# （R5-02 的 LLM 章节分类从未运行，实测 classification_source='none' 的 358/358），
# 所以小节映射只能靠**标题/正文词面**，不能依赖索引里的分类字段。
#
# ⚠️ **改词表的后果是指标级的**：加词或改词会直接移动覆盖率。改完必须重跑
# `tmp/check_coverage.py` 复测，并如实记录结果 —— 不要为凑覆盖率加词。
#
# 2026-09-14：用户裁定需求二**只保留"方案"类模块**，故摘掉「对项目的理解与需求分析」
# （项目理解/需求分析不是方案）。摘后复测 **8/8 = 100%**（各模块标题命中 5、召回池 25）。
MODULE_KEYWORDS_FILE = Path(__file__).resolve().parent / "module_keywords.json"


def _load_module_keywords() -> dict[str, tuple[str, ...]]:
    """从配置读方案模块的词表。**顺序即展示顺序**，必须保留（判定与比对依赖它）。"""
    import json

    if not MODULE_KEYWORDS_FILE.exists():
        raise RuntimeError(f"模块词表配置缺失：{MODULE_KEYWORDS_FILE}")
    raw = json.loads(MODULE_KEYWORDS_FILE.read_text(encoding="utf-8"))
    return {name: tuple(words) for name, words in raw.items()}


MODULE_KEYWORDS: dict[str, tuple[str, ...]] = _load_module_keywords()

VALID_ROLES = ("our_response", "final_signed")
# 评分要求行不得进入正式证据（判据自 R5 阶段沿用；现就地定义在本文件，不再有旁支模块）
_SCORE_KW = ("得2分", "得1分", "评分项目", "评分标准", "评审标准", "打分", "得分标准")

# 近重复阈值：同一模板跨项目复制的章节，4-gram Jaccard 通常 ≥0.8；不同主题 <0.4
DUP_THRESHOLD = 0.75
_SHINGLE_K = 4
# 召回优先级（计划 §3.4）：原生文字 → 混合 → 扫描 OCR。仅用于同簇内择优与同分排序。
_FORMAT_RANK = {"native_text": 0, "mixed": 1, "scanned_ocr": 2}


class ProposalGenNotAuthorized(RuntimeError):
    """未开启方案生成（外发）时的显式拒绝。"""


@dataclass
class EvidencePack:
    """一个小节的证据包。`status`：ok | sparse | insufficient。"""
    module: str
    status: str
    # `display`：**上屏用的小节名**（2026-09-18）。用户点名了一个系统不认识的小标题时
    # （如「服务周期」），`module` 记它**归属的模块**（决定召回与角色口径），
    # `display` 记用户**自己的措辞**（决定输出标题与页面标签）。默认与 module 相同。
    display: str = ""
    # `terms`：归因/判定用的词元（小节名 + 所属模块关键词），空则按 module 推导。
    terms: tuple[str, ...] = ()
    evidence: list[dict] = field(default_factory=list)   # 去重后的证据（≤ max_packs）
    pool_size: int = 0            # 召回池大小（角色过滤前）
    role_rejected: int = 0        # 因角色不符（竞品/招标/未知）被拒的条数
    deduped: int = 0              # 近重复聚类后（= len(evidence)）
    heading_hits: int = 0         # 其中**标题命中**条数（决定 ok / sparse）
    dropped_duplicates: int = 0   # 因近重复/同项目超额被丢弃的条数
    notes: list[str] = field(default_factory=list)


# ============================================================================
# R7-01 需求小节提取
# ============================================================================

def extract_required_sections(text: str) -> list[str]:
    """从用户问题/招标文本提取必须包含的方案小节。**匹配不到就返回空，不猜**。

    返回顺序固定为 `MODULE_KEYWORDS` 的定义顺序（确定性、可比对）。
    """
    t = (text or "").strip()
    if not t:
        return []
    return [mod for mod, kws in MODULE_KEYWORDS.items()
            if mod in t or any(k in t for k in kws)]


# 用户**点名要求**的小节（「必须包含X」「要有X」）—— 用于检查有没有被静默丢掉。
_REQ_CLAUSE = re.compile(r"(?:必须|需要|要有|要|请|务必|一定)\s*(?:包含|包括|含|有|写|提供)\s*"
                         r"([^，,。；;" + "\n" + r"]{2,20})")


def unrecognized_requirements(text: str, recognized: list[str]) -> list[str]:
    """用户**点名要求**、但没被识别成方案小节的内容 —— 如实报出来，**不静默丢**。

    ⚠️ 由来（2026-09-14 B2B 评审漏报、属项目红线）：用户输入
    `售后服务方案，必须包含质控要求` → `extract_required_sections` 只认出「售后方案」
    （因为 `MODULE_KEYWORDS` 里质量控制方案的关键词是「质量控制/质量保证…」，「**质控**要求」不含任何一个），
    而「质控要求」就这样**被静默丢掉**，接口 **200**、`coverage.requested=1` ——
    用户以为两个要求都提了，实际只有一个进了流程。

    与 `_UNSUPPORTED_CONDITIONS`（检索侧「拒绝优于静默」）同一原则：
    **认不出来要说出来**，由用户决定换措辞还是接受忽略。
    """
    out: list[str] = []
    for m in _REQ_CLAUSE.finditer(text or ""):
        frag = m.group(1).strip()
        if len(frag) < 2:
            continue
        # 该片段自己能被认出（或其子串命中任一模块关键词）→ 不算丢
        if extract_required_sections(frag):
            continue
        if any(frag in mod or mod in frag for mod in recognized):
            continue
        if frag not in out:
            out.append(frag)
    return out


# ============================================================================
# 本地近重复判定（不外发）
# ============================================================================

def _norm(text: str) -> str:
    """归一：去空白与常见排版符，仅保留中日韩/字母/数字（跨项目模板复制后只剩这些稳定）。"""
    return re.sub(r"[^0-9A-Za-z一-鿿]+", "", (text or "").lower())


def shingles(text: str, k: int = _SHINGLE_K) -> frozenset[str]:
    s = _norm(text)
    if len(s) < k:
        return frozenset([s]) if s else frozenset()
    return frozenset(s[i:i + k] for i in range(len(s) - k + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _cluster(cands: list[dict], threshold: float = DUP_THRESHOLD) -> list[list[dict]]:
    """按字符 shingle Jaccard 贪心聚类（**确定性**：按输入顺序，先到者为簇心）。

    同一模板跨项目复制的章节会落进同一簇 —— 它们**不是独立证据**（R7-03）。
    """
    sigs = [(c, shingles(c.get("text") or "")) for c in cands]
    clusters: list[list[dict]] = []
    reps: list[frozenset[str]] = []
    for cand, sig in sigs:
        for i, rep in enumerate(reps):
            if jaccard(sig, rep) >= threshold:
                clusters[i].append(cand)
                break
        else:
            clusters.append([cand])
            reps.append(sig)
    return clusters


def _is_score_line(text: str) -> bool:
    return any(k in (text or "") for k in _SCORE_KW)


def _is_score_section(heading: str, text: str) -> bool:
    """整个章节是不是**评分要求章节**（而不是正文里顺带提了一次）。

    ⚠️ 判据**不能照搬 R5 阶段的逐行判据**：那里是**逐行**用的（一行不采），这里拿到的是**整章**
    （可达 7 万字）。**在整章粒度上照搬逐行判据会把真方案整章剔除** —— 对抗性复核实测：
    「13、服务方案」6,002 字，只因正文含「对代谢物进行打分（Score）」这一处实验方法描述被整条丢弃；
    九模块合计误剔 15 条候选，其中一条 70,898 字的章节本应进入正文兜底池。
    故只看两处：标题是否就是评分项，以及正文是否**过半**是评分行。
    """
    if _is_score_line(heading or ""):
        return True
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    scored = sum(1 for ln in lines if _is_score_line(ln))
    return scored * 2 >= len(lines)


def _pick_representative(cluster: list[dict]) -> dict:
    """簇内代表：**原生文字优先**，同格式取正文最长的（信息量最大，且确定性）。

    ⚠️ `content_format` 必须参与比较（R7-04「原生文字证据优先，扫描件作为补充」）。
    只按长度排序时，同簇内**带页眉噪声的 OCR 文本更长** → OCR 版取代原生版、
    原生版反被当重复丢弃（对抗性复核实测：J=0.905 同簇，77 字原生 vs 84 字 OCR 噪声，选中 OCR）。
    """
    return max(cluster, key=lambda c: (
        -_FORMAT_RANK.get(c.get("content_format") or "", 3),
        len(c.get("text") or ""), c.get("section_id") or ""))


# ============================================================================
# 召回（ES BM25，本地）
# ============================================================================

def _is_plausible_heading(heading: str | None) -> bool:
    """该章节标题像不像**真标题**（而非切分残渣）。

    实测（2026-09-12）：索引 9,437 条里有 **541 条（5.7%）**的 `heading` 是
    电话号码（`18981846247`）、表格数值（`3.4300` / `5.74`）、引文页码范围（`283-286,`）、DOI。
    来源是 `scanned_ocr` 295 条 + `native_pdf_text` 238 条 —— **OCR 与原生 PDF 都会产出**。
    这类"章节"是切分器把正文当成边界切出来的，**不是方案小节**。

    **只在召回层拒绝，不删索引**：那些 section 的正文可能是真内容（只是标题被切错），
    删了会连带丢内容；拒绝进证据则完全可回退。
    """
    h = (heading or "").strip()
    if len(h) < 2:
        return False
    if re.fullmatch(r"[\d.,%()（）\-–—～~\s]+", h):        # 纯数字/百分比/标点（含电话号）
        return False
    if re.match(r"^(10\.\d{4,}/|doi[:：]|https?://)", h, re.I):   # DOI / 链接
        return False
    return True


# ============================================================================
# 文件身份说明（2026-09-15）：给证据附带「项目是干什么的 + 文件是干什么的」，
# 让 LLM 召回后能复核证据的类型/作用。**纯确定性、零外发** —— 只从项目目录名与
# 文件角色/文件名主干规则推导，不概括正文（那是第二步，需外发授权）。
# ============================================================================

# document_role → 中文短标签（供 LLM 复核「这份文件是什么」）
_ROLE_LABELS = {
    "our_response": "我方响应",
    "final_signed": "我方最终版",
    "contract_evidence": "合同证据",
    "competitor_response": "竞品响应",
    "tender_requirement": "招标要求",
    "qualification_evidence": "资质证明",
    "process_material": "过程材料",
    "system_or_temp": "系统/临时",
    "unknown": "待定",
    "ambiguous": "未判定",
}
# 文件名**末尾**常见厂商署名段（剥除后露出业务主干）；抬头（如「上海欧易生物…投标文件」）不剥
_FN_COMPANY_SUFFIXES = (
    "上海欧易生物医学科技有限公司", "欧易生物医学科技有限公司", "欧易生物科技有限公司",
    "上海欧易生物", "欧易生物", "鹿明生物", "上海鹿明", "欧易", "鹿明",
    "百趣生物", "诺禾致源", "诺禾", "美吉生物", "美吉", "华大基因", "华大",
    "联川生物", "联川", "拜谱生物", "拜谱", "吉凯基因", "吉凯",
)


_FN_SEPARATORS = "+·-｜_（(/ "  # str（rstrip/lstrip 只收 str，不收 tuple）；半角 +，勿打全角 ＋


_FN_SEPARATORS = "+·-｜_（(/ "  # str 单字符集（勿打全角 ＋）

def _strip_trailing_separators(text: str) -> str:
    """剥掉末尾任意个分隔符（endswith(整串) 不表示「末尾任一分隔符」，须逐字判断）。"""
    while text and text[-1] in _FN_SEPARATORS:
        text = text[:-1]
    return text


def _strip_company_suffix(filename: str) -> str:
    """剥文件名自带的厂商署名段（末尾 + 中间**独立段**），恢复业务主干。

    示例：`报价单+上海欧易生物医学科技有限公司.pdf` → `报价单`
          `欧易响应文件-上海欧易生物.pdf` → `欧易响应文件`
          `…-欧易生物-响应文件-20250103.docx` → `…-响应文件-20250103`（中段独立段剥除）

    只剥「独立段」：厂商名**前后都是分隔符**（或到串首/串尾）才剥 ——
    嵌在中文词里（`欧易生物资源中心`/`…欧易生物科技公司`）不碰。
    """
    name = re.sub(r"\.[^.]+$", "", filename)   # 去扩展名，便于按段判定
    # 1) 末尾独立段：`endswith(suffix)`，循环剥并被其前分隔符
    changed = True
    while changed:
        changed = False
        for suffix in _FN_COMPANY_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                name = _strip_trailing_separators(name)
                changed = True
                break
    # 2) 中段独立段：厂商名前后都有分隔符（或前为串首）→ 剥除该段及一侧分隔符。
    #    规则：只剥「后随分隔符 &&（前随分隔符 || 前为串首）」的实例 —— 登记前缀或
    #    项目名内分隔的厂商，不是业务主干；抬头独立段（`欧易生物报名文件`）**不剥**（后随字）。
    for suffix in _FN_COMPANY_SUFFIXES:
        while True:
            idx = name.find(suffix)
            if idx < 0:
                break
            before, after = name[:idx], name[idx + len(suffix):]
            front_ok = idx == 0 or before[-1] in _FN_SEPARATORS
            back_ok = bool(after) and after[0] in _FN_SEPARATORS
            if front_ok and back_ok:
                # 剥掉厂商段，保留一侧分隔符（`A-欧易生物-B` → `A-B`）。
                # 前侧：厂商在串首时（idx==0）无前分隔，只需清 after 的**前导**分隔符；
                #       厂商在中段时剥 before 尾分隔（`A-` → `A`）。
                if idx == 0:
                    after2 = after[1:] if after[:1] in _FN_SEPARATORS else after
                    name = after2.lstrip(_FN_SEPARATORS)
                else:
                    name = _strip_trailing_separators(before) + after
                continue
            break  # 该 suffix 在此无独立段出现，换下一个
    return _strip_trailing_separators(name)


def project_summary_of(project_folder: str) -> str:
    """项目简介 = 一级目录名去掉日期前缀。

    现库目录名形态 `20250103-客户-联系人-项目名`（实测 20 条），日期段是纯登记前缀、
    不含业务信息，剥掉即是「这个项目干什么」的最小确定性描述。容忍无日期（原样保留）。
    """
    pf = (project_folder or "").strip()
    if not pf:
        return ""
    return re.sub(r"^\d{8}-?", "", pf)


def _filename_stem(relative_path: str) -> str:
    """文件名主干：剥厂商尾缀 + 去扩展名 + 收紧空白与首尾符号。不判角色（角色由 document_role 给出）。"""
    fn = (relative_path or "").rsplit("/", 1)[-1]
    base = _strip_company_suffix(fn)
    base = re.sub(r"\.[^.]+$", "", base)
    return base.strip().strip("-_（）()[]【】 ")


def doc_purpose_of(relative_path: str, document_role: str) -> str:
    """文件用途一句话 = 角色中文标签（document_role）+ 文件名主干。

    例：our_response + `…/欧易响应文件_20260912151213.pdf` → `我方响应（欧易响应文件_20260912151213）`
        其中 `_20260912151213` 保留（版本戳，剥掉会丢区分度）。
    """
    stem = _filename_stem(relative_path)
    if not stem:
        role = _ROLE_LABELS.get(document_role or "", None)
        return role or document_role or "待定"
    role = _ROLE_LABELS.get(document_role or "", document_role or "待定")
    return f"{role}（{stem}）"


def _recall(terms: tuple[str, ...], pool_size: int) -> list[dict]:
    """**按小节**召回候选池（R7-03：「每个小节先召回 20–30 候选池」）。

    必须逐小节召回，不能所有小节共用一个池 —— 实测共用池时高分章节全是「长而泛」的
    正文（如「6 样品流转」），各小节拿到的证据高度重合，等于没有分小节。

    `terms` 由调用方给：模块名 + 该模块关键词，或（用户自造小标题时）小节名 + 所属模块关键词。
    """
    from elasticsearch import Elasticsearch

    es = Elasticsearch(config.ES_URL, request_timeout=30)
    should: list[dict] = []
    for t in terms:
        should.append({"match": {"heading": {"query": t, "boost": 6}}})
        should.append({"match": {"text": {"query": t}}})
    body = {
        "size": max(1, min(pool_size, 60)),
        "_source": ["section_id", "document_id", "project_key", "heading", "text",
                    "section_type", "structural_role", "content_format", "boundary_source"],
        "query": {"bool": {
            "should": should,
            # 只取正文型章节（结构项如目录/封面/页眉不进方案证据）
            "filter": [{"term": {"structural_role": "content_section"}}],
            "minimum_should_match": 1,
        }},
    }
    return [dict(h["_source"], score=round(h["_score"], 4))
            for h in es.search(index=config.ES_INDEX_SCHEME, **body)["hits"]["hits"]]


def _match_basis(terms: tuple[str, ...], heading: str, text: str) -> str | None:
    """该章节属本小节的依据：`heading`（标题命中，高精度）> `text`（正文命中，噪声大）。

    **标题优先是刻意的**：正文里顺带提到某个词的章节（如长正文里出现一次「培训」）
    不是该小节的方案 —— 只按正文子串匹配会让每个小节都收到同一批泛化章节。

    `terms` 由调用方给（不是模块名）：2026-09-18 起支持**用户自造的小标题**
    （如「服务周期」不在模块词表里），此时 terms = 该小节名 + 其所属模块的关键词。
    """
    h = heading or ""
    if any(t in h for t in terms):
        return "heading"
    if any(t in (text or "") for t in terms):
        return "text"
    return None


# ============================================================================
# R7-03 证据包组装
# ============================================================================

def build_evidence_packs(con, modules: list[str], *, pool_size: int = 25,
                         max_packs: int = 5, min_packs: int = 3,
                         max_per_project: int = 2,
                         roots: dict[str, Path] | None = None,
                         section_map: dict[str, tuple[str, tuple[str, ...]]] | None = None,
                         ) -> list[EvidencePack]:
    """按小节组装 Evidence Pack。**只读**：ES 召回 + SQLite 回查角色/真实路径。

    `con` 必须是调用方传入的连接（理由同 `search_scheme_sections`：
    自己开库会连到 `bid_ai_clean.db` 正式库 → 回查全落空、静默返回 0 条）。
    """
    roots = roots or DEFAULT_ROOTS
    docs: dict[str, dict | None] = {}

    def _doc(document_id: str) -> dict | None:
        if document_id not in docs:
            row = con.execute(
                "SELECT relative_path, source_root_id, project_folder, document_role, parse_status "
                "FROM documents WHERE document_id=?", (document_id,)).fetchone()
            docs[document_id] = dict(row) if row is not None else None
        return docs[document_id]

    out: list[EvidencePack] = []
    for mod in modules:
        # `section_map`：**用户自造的小标题** → (归属模块, 判定词元)。
        # 未在表里的按模块名走原路径（不改变既有行为）。
        # ⚠️ 归属模块**必须是 MODULE_KEYWORDS 里的**（调用方保证）——
        # 否则 `MODULE_KEYWORDS[mod]` 会 KeyError，且角色/门槛口径无从谈起。
        # ⚠️ **不能用 `dict.get(mod, 默认值)`** —— 默认值是**立即求值**的，
        # 而用户自造小节名（`服务周期`）不在 `MODULE_KEYWORDS` 里 → `MODULE_KEYWORDS[mod]`
        # 会直接 KeyError（实测踩到）。必须先判断再取值。
        hit = (section_map or {}).get(mod)
        if hit:
            owner, terms = hit
        else:
            owner, terms = mod, (mod,) + MODULE_KEYWORDS[mod]
        pack = EvidencePack(module=owner, display=mod, terms=tuple(terms),
                            status="insufficient")
        cands = _recall(pack.terms, pool_size)
        pack.pool_size = len(cands)
        head: list[dict] = []
        text_only: list[dict] = []
        for c in cands:
            d = _doc(c["document_id"])
            if d is None:
                continue          # 索引里的章节在当前库找不到文档 → 不返回（可追溯性优先）
            # 角色过滤（R5-01/D2A）：只有我方响应/最终版可作方案证据
            if d["document_role"] not in VALID_ROLES:
                pack.role_rejected += 1
                continue
            if not (c.get("text") or "").strip():
                continue
            # 标题不像标题的（电话号/表格数值/DOI，实测占索引 5.7%）不进证据
            if not _is_plausible_heading(c.get("heading")):
                pack.role_rejected += 1
                continue
            if _is_score_section(c.get("heading"), c.get("text")):
                continue          # 评分要求章节不得进入正式证据（整章粒度，见 _is_score_section）
            basis = _match_basis(pack.terms, c.get("heading"), c.get("text"))
            if basis is None:
                continue
            item = dict(c, match_basis=basis, file_name=d["relative_path"].rsplit("/", 1)[-1],
                        project_folder=d["project_folder"], document_role=d["document_role"],
                        source_path=str(roots.get(d["source_root_id"], Path("")).joinpath(
                            *d["relative_path"].split("/"))))
            # 文件身份说明（2026-09-15）：让 LLM 复核「这份文件属于什么项目、是干什么的」。
            # 纯规则生成、零外发；`_doc()` 已 SELECT 到 project_folder / relative_path / document_role，
            # 故不改 SQL、不改测试 fixture。source_path **不进提示词**（红线，见 build_gen_prompt）。
            item["project_summary"] = project_summary_of(d["project_folder"])
            item["doc_purpose"] = doc_purpose_of(d["relative_path"], d["document_role"])
            (head if basis == "heading" else text_only).append(item)

        if not head and not text_only:
            pack.notes.append("该小节无任何命中（招标文件可能未单独设节，或历史响应文件尚未入库）")
            out.append(pack)
            continue

        chosen, dropped = _select(head, text_only, max_packs, min_packs, max_per_project)
        pack.evidence = chosen
        pack.deduped = len(chosen)
        pack.heading_hits = sum(1 for e in chosen if e.get("match_basis") == "heading")
        pack.dropped_duplicates = dropped
        # `ok` 要求**至少 min_packs 条标题命中** —— 标题命中才是真正对题的方案小节。
        # 全靠正文兜底凑够条数的包**不叫 ok**：那正是 R7-03/D12 禁止的「用低质材料补足」。
        if pack.heading_hits >= min_packs:
            pack.status = "ok"
        else:
            pack.status = "sparse"
            pack.notes.append(
                f"去重后 {len(chosen)} 条，其中**标题命中仅 {pack.heading_hits} 条**（软下限 {min_packs}）；"
                f"另丢弃近重复/同项目超额 {dropped} 条 —— 按 R7-03/D12 报 sparse，不用重复或低质材料补足")
        if not head:
            pack.notes.append("**本小节无任何标题命中**，下列证据全部来自正文匹配，精度低，需人工确认")
        elif text_only and pack.heading_hits < len(chosen):
            pack.notes.append("含正文兜底项（凑到软下限即止，不再向硬上限填充）")
        out.append(pack)
    return out


def _select(head: list[dict], text_only: list[dict], max_packs: int, min_packs: int,
            max_per_project: int) -> tuple[list[dict], int]:
    """标题命中优先（上限 `max_packs`）；不足 `min_packs` 时才用正文命中**补到软下限即止**。

    返回 (选中, 丢弃数)。正文兜底**不向硬上限填充** —— 5 是给真正对题的小节留的空间。
    """

    def _take(pool: list[dict], chosen: list[dict], seen_sig: list, seen_proj: dict,
              limit: int) -> int:
        dropped = 0
        clusters = _cluster(pool)
        reps = sorted((_pick_representative(cl) for cl in clusters),
                      key=lambda r: (_FORMAT_RANK.get(r.get("content_format") or "", 3),
                                     -len(r.get("text") or ""), r.get("section_id") or ""))
        dropped += len(pool) - len(clusters)      # 近重复（同模板跨项目复制）
        for r in reps:
            if len(chosen) >= limit:
                break
            # 跨来源去重：正文兜底项不得与已选中项近重复
            sig = shingles(r.get("text") or "")
            if any(jaccard(sig, s) >= DUP_THRESHOLD for s in seen_sig):
                dropped += 1
                continue
            pk = r.get("project_key") or r.get("project_folder") or "?"
            if seen_proj.get(pk, 0) >= max_per_project:
                dropped += 1
                continue
            seen_proj[pk] = seen_proj.get(pk, 0) + 1
            seen_sig.append(sig)
            chosen.append(r)
        return dropped

    chosen: list[dict] = []
    seen_sig: list = []
    seen_proj: dict = {}
    dropped = _take(head, chosen, seen_sig, seen_proj, max_packs)
    if len(chosen) < min_packs and text_only:
        # 正文兜底只补到**软下限**，且**不得超过硬上限**：`max_packs < min_packs` 时（调用方
        # 明确要更少，如 `?limit=1`）硬上限优先 —— 否则会把调用方的上限冲掉
        # （对抗性复核实测：`?modules=售后方案&limit=1` 返回 3 条，违反 R7-03「5=硬上限」的同一条原则）。
        dropped += _take(text_only, chosen, seen_sig, seen_proj, min(min_packs, max_packs))
    return chosen, dropped


def packs_to_payload(packs: list[EvidencePack], *, excerpt: int = 1200,
                     budget_chars: int | None = None,
                     min_excerpt: int = 300) -> dict:
    """证据包 → 可下发结构（截断正文，附来源与引用编号）。

    引用编号 `E1..En` **由系统生成**、按 (模块, 顺序) 稳定编号；模型不得自造。

    **提示词预算**（`budget_chars`）：方案模块全开时，5 条 ×1200 字 ×8 节约 4.8 万字，
    真跑时可能直接超模型上下文而失败。超过预算时**按比例收缩每条摘录**（下限 `min_excerpt`），
    并在 `truncation` 里**如实记录收缩情况** —— 生成结果必须让人知道"这不是全文"。

    `budget_chars` 在**调用时**从配置解析，**不做默认参数值** —— 默认参数在 import 时就被求值冻结，
    运行时改 `config.PROPOSAL_GEN_MAX_PROMPT_CHARS` 不会生效（实测踩到，测试因此一直不触发收缩）。
    """
    if budget_chars is None:
        budget_chars = config.PROPOSAL_GEN_MAX_PROMPT_CHARS
    raw_total = sum(len(e.get("text") or "") for p in packs for e in p.evidence)
    per_item = excerpt
    if raw_total > budget_chars:
        n_items = max(1, sum(len(p.evidence) for p in packs))
        per_item = max(min_excerpt, budget_chars // n_items)

    out, n = [], 0
    for p in packs:
        evs = []
        for e in p.evidence:
            n += 1
            text = e.get("text") or ""
            evs.append({
                "ref": f"E{n}",
                "section_id": e.get("section_id"),
                "document_id": e.get("document_id"),
                "project_folder": e.get("project_folder"),
                "file_name": e.get("file_name"),
                "source_path": e.get("source_path"),
                "heading": e.get("heading"),
                "content_format": e.get("content_format"),
                "document_role": e.get("document_role"),
                "match_basis": e.get("match_basis"),
                "project_summary": e.get("project_summary"),
                "doc_purpose": e.get("doc_purpose"),
                "text": text[:per_item],
                "text_full_len": len(text),
                "score": e.get("score"),
            })
        out.append({"module": p.module, "display": p.display or p.module,
                    "status": p.status, "evidence": evs,
                    "pool_size": p.pool_size, "role_rejected": p.role_rejected,
                    "deduped": p.deduped, "heading_hits": p.heading_hits,
                    "dropped_duplicates": p.dropped_duplicates, "notes": p.notes})

    truncation = None
    if per_item < excerpt:
        after = sum(len(e["text"]) for m in out for e in m["evidence"])
        truncation = {
            "budget_chars": budget_chars, "excerpt_per_item": per_item,
            "original_chars": raw_total, "applied_chars": after,
            "note": (f"证据正文按提示词预算收缩到每条 {per_item} 字"
                     f"（原 {excerpt} 字，合计 {raw_total}→{after} 字）——"
                     f"模型看到的是**摘录**，不是全文；引用仍指向完整源文件。"),
        }
    return {"modules": out,
            "truncation": truncation,
            "coverage": {"requested": len(packs),
                         "ok": sum(1 for p in packs if p.status == "ok"),
                         "sparse": sum(1 for p in packs if p.status == "sparse"),
                         "insufficient": sum(1 for p in packs if p.status == "insufficient")}}


# ============================================================================
# R7-05/R7-06/R7-07 生成（**外发，按授权记录启用**）
# ============================================================================

# 生成提示词。规则逐条对应计划要求，不是泛泛的"请写一份方案"：
#   R7-05 只用命中的原文+来源+用户约束；R7-06 输出正文+引用+冲突/缺口警告；
#   R7-07 关键数字必须来自证据；D12 证据不足不得补造；数字冲突只告警不择一。
GEN_SYSTEM = """你是投标方案撰写助手。你**只能**使用下面给出的【历史证据】组织内容，不得引入外部知识。

硬性规则：
1. 每个关键事实、承诺时限、数字后面必须紧跟引用编号，形如 [E3]；**没有引用支撑的承诺和数字不要写**。
2. 只能引用【历史证据】里实际出现的编号。**严禁编造编号**，也不要把编号写成 [E99] 这种不存在的。
3. 同一事项在不同证据里数值不一致时，**必须在正文中写明存在冲突并逐一列出各来源**，不得自行选定一个数字。
4. 证据状态为 sparse 的小节，如材料不足请直接写「历史材料不足，以下仅为可查到的片段」，**不要用常识补写**。
5. 证据状态为 insufficient 的小节，直接写「历史材料未覆盖本小节」。
6. **不要**写评分标准、招标文件要求、竞品做法的内容。
7. 输出 Markdown；每个小节用二级标题，标题必须包含小节名。
   ⚠️ **若提示里给了【必须逐字遵守的标题结构】，则以它为准**：大标题用一级标题 `#`、
   小标题用二级标题 `##`，**逐字照抄、顺序一致、不得增删改名、不得再加别的同级标题**。
   未给该结构时，按上面这句的默认写法（每个模块小节一个二级标题）。
   两种情况下**标题只是骨架**，标题下的内容仍必须遵守规则 1–6、8、9。
8. 每条证据行给出的【项目简介】/【文件用途】只用于**判断该证据是否适用于本方案**；
   **不得**把这两项的内容当作可引用的原文或承诺。
9. 若提示里指定了【本次方案的产品线】，而证据中**并列了多个产品线**的条目
   （典型：同时列出「RNA 项目 / DNA 项目 / 单细胞项目」的异常处理），
   **只保留与该产品线相关的条目**，其余产品线的条目**不得写入**；
   若该小节确实与产品线无关（如通用管理条款），照常写。
10. **引用编号放在句末，不要放在句首**。写成「数据验收合格后，提供不少于 2 年的售后服务 [E3]。」，
   **不要**写成「根据【E3】，提供不少于 2 年的售后服务。」—— 逐条以「根据 Ex」开头会让整篇
   读起来像检索日志，而不是方案。同一条编号在同一句里**只出现一次**（不要 `[E3] [E3]`）。
   编号一律用**半角方括号** `[E3]`（不要用全角的【E3】，虽然系统也认，但半角才是规范写法）。
11. **不要输出与本次结构无关的内容**：标题结构给了几个小节就写几个，
   不要另加「总结」「说明」「参考文献」之类的段落；不要在正文里解释你做了什么取舍
   （如「因不属于本次产品线，不予写入」）—— 取舍直接体现为**不写那条**即可。"""


class ProposalGenError(RuntimeError):
    """生成调用失败。"""


def _guard() -> None:
    if not config.PROPOSAL_GEN_ENABLED:
        raise ProposalGenNotAuthorized(
            "方案生成未授权/未开启（config.PROPOSAL_GEN_ENABLED=false）。"
            "该步骤会把我方响应正文外发；启用前须有书面授权记录"
            "（见 docs/authorizations/llm-generation-authorization.md）。"
            "本地机械件（召回/聚类/证据包）无需此开关。")


def _gen_client():
    from openai import OpenAI
    base = config.LLM_BASE_URL
    key = config.LLM_API_KEY
    model = config.LLM_MODEL
    if not (base and key and model):
        raise ProposalGenError("缺少 LLM 网关配置（LLM_BASE_URL / LLM_API_KEY / LLM_MODEL）")
    return OpenAI(base_url=base, api_key=key,
                  timeout=config.PROPOSAL_GEN_TIMEOUT, max_retries=2), model


def plan_sections(query: str, modules: list[str] | None = None,
                  max_sections: int = 6) -> dict:
    """把用户那句话拆成「**输出结构**」—— 大标题 + 小标题清单。

    需求方 2026-09-18 原话：「检索条件是：售后服务方案，必须包含服务周期和应急预案
    要做意图识别，这时候我需要的就是 大标题：售后服务方案 小标题（1）服务周期（2）应急预案
    **不需要输出多余的内容** 只需要把这两个小标题相关的内容放进去即可」。

    返回 `{"title": str, "sections": [{"name": str, "module": str, "terms": tuple}], ...}`
    （**空 dict** = 什么都没拆出来，调用方据此**不施加约束**，不猜）。

    **三条规则**（都来自实测，不是设计偏好）：
      1. **大标题 = 原句里第一个模块名，按用户措辞的长形**（用户裁定「原话优先」）——
         `售后服务方案` 里含模块名 `售后方案`，就取**用户那个长形**做标题，
         而不是替他把「服务」两个字抹掉。取不到模块名 → **不设标题**（宁可不要，不要猜）。
      2. **小标题 = 「必须包含X」子句切开后的每一项**（`_split_req_clause`），加上
         **原句里出现但不在该子句里的模块名**（如「…必须包含质控要求，保密方案」里
         句外的 `保密方案` —— 那是用户也要的小节，实测原先靠 `extract_required_sections` 拾到）。
      3. **每一项必须能挂到一个模块上**：
         · 它本身是模块名 / 命中某个模块的关键词 → `module` = 那个模块（**且名字不重复收录**，
           因为模块名本身已经是大标题或其它小节）；
         · 否则看它**含不含**某个模块名或关键词（`服务周期` 含「服务」→「售后方案」）→ 归到该模块；
         · 都挂不上 → **不单立小节**（宁缺毋滥），由调用方如实报「该项未被纳入」。
       唯一例外：**整个小标题清单为空**时，允许把挂不上的项保留为「用户自造小节」，
       因为那时用户没给系统任何能用的结构，丢掉等于什么都没做。

    输出小节名**保留用户措辞**（需求方要逐字），归属模块由 `module` 单独承载 ——
    这样「服务周期」能在**售后方案的证据池**里召回（而不是去查一个叫「服务周期」的模块，
    那个模块不存在）。
    """
    text = (query or "").strip()
    if not text:
        return {}
    mods = [m for m in (modules or list(MODULE_KEYWORDS)) if m in MODULE_KEYWORDS]

    def owner_of(name: str) -> str:
        """该项目挂到哪个模块：先精确（等于模块名/命中关键词），再包含（含模块名或关键词）。"""
        for mod in mods:
            if name == mod or name in MODULE_KEYWORDS[mod]:
                return mod
        for mod in mods:
            if mod in name or any(k in name for k in MODULE_KEYWORDS[mod]):
                return mod
        return ""

    # 大标题：原句里**最早出现**的那个模块，取**用户措辞的长形**（`售后服务方案` > `售后方案`）。
    # ⚠️ 两个坑（实测踩到，都已修）：
    #   ① **模块名不一定原样出现**：`售后服务方案` 里并没有子串 `售后方案`（它是「售后+服务+方案」），
    #      所以必须**按模块关键词**去定位（`售后服务` 是「售后方案」的关键词）——
    #      否则 `售后服务方案…` 这句压根找不到大标题。
    #   ② **不能按 `mods` 顺序取第一个命中**：`培训方案，必须包含讲师安排，保密方案` 里
    #      `保密方案` 在词表里更靠前，会把句尾的它当成大标题，而用户显然说的是句首的「培训方案」。
    #      必须按**在句子里的位置**取最早的那个。
    title, title_mod, best_pos = "", "", None
    for mod in mods:
        for term in (mod,) + tuple(MODULE_KEYWORDS[mod]):
            i = text.find(term)
            if i < 0:
                continue
            if best_pos is None or i < best_pos:
                # 往右吃掉紧邻的中文/字母（`售后服务` → `售后服务方案`；到标点/空白即停）
                j = i + len(term)
                while j < len(text) and (text[j].isalnum() or text[j] in "（）()·")                         and text[j] not in _TITLE_STOP:
                    j += 1
                title, title_mod, best_pos = text[i:j].strip(), mod, i
            break                       # 同一模块只看它最早命中的那个词

    sections: list[dict] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        name = (name or "").strip(" 　:：,，、。；;")
        if len(name) < 2 or name in seen:
            return
        mod = owner_of(name)
        inferred = False
        if not mod:
            # ⚠️ **挂不上模块 ≠ 丢掉**：需求方 2026-09-18 的原话就是「小标题（1）服务周期」——
            # 那是**用户自己写的小标题**，不是系统编的。丢掉等于没满足需求（实测：
            # 「服务周期」在模块词表里一个字都不命中，早先版本因此把它整条丢掉）。
            # 归属给**大标题那个模块**（句首语义最强），并**如实标记 `module_inferred`**，
            # 让上屏与提示词都能说明「这个归属是系统推的，不是用户指定的」。
            if not title_mod:
                return                  # 连大标题都没有 → 真没有依据，不猜（宁缺毋滥）
            mod, inferred = title_mod, True
        if name == title:
            return                      # 与大标题同字 → 不重复（它就是大标题本身）
        seen.add(name)
        # ⚠️ 判定词元 = 用户措辞 + **归属模块的关键词** —— 后者是必要的：
        # 光用「服务周期」在历史章节里几乎召不到（ES 标题短语实测仅 1 条），
        # 而它本来就在售后方案这个模块里（服务承诺/响应时间/质保…都是该模块的关键词）。
        terms = (name,) + tuple(k for k in MODULE_KEYWORDS[mod] if k not in name)
        sec = {"name": name, "module": mod, "terms": terms}
        if inferred:
            sec["module_inferred"] = True    # 归属是系统推的 → 上屏要说明
        sections.append(sec)

    seps = _split_req_clause(text)      # 「必须包含」清单里的项（`add` 需要它来区分同名情形）

    for frag in seps:
        add(frag)
    # 句外的模块名（如「…，保密方案」）—— 去掉大标题已占的那个
    for mod in mods:
        if mod in text and mod != title_mod:
            add(mod)

    sections = sections[:max(0, max_sections)]
    if not title and not sections:
        return {}
    return {"title": title, "title_module": title_mod, "sections": sections,
            "dropped": _dropped_requirements(text, sections, title)}


# 大标题右扩的停止符：并列词与标点都算边界（`售后服务方案和应急预案` 里的大标题到「和」为止）
_TITLE_STOP = frozenset("和与及、，。；;：:和 ")

# 「必须包含X和Y」—— `_REQ_CLAUSE` 抓的是**整段**（`服务周期和应急预案`），必须再切。
# ⚠️ 切分符要**含「和/与/及」**：需求方那句就是「服务周期和应急预案」（实测 `_REQ_CLAUSE`
# 原样吐出这一整串，不切的话「服务周期」会被连带跳过一次都不产出 —— 见 `_dropped_requirements`）。
_CLAUSE_SPLIT = re.compile(r"[、,，;；/]|和|与|及")


def _split_req_clause(text: str) -> list[str]:
    """把「必须包含…」子句切成**逐个小标题**（保序、去重、剥要求动词）。"""
    out: list[str] = []
    for m in _REQ_CLAUSE.finditer(text or ""):
        for part in _CLAUSE_SPLIT.split(m.group(1)):
            s = _REQ_VERB.sub("", (part or "").strip()).strip(" 　:：,，、。；;")
            if len(s) >= 2 and s not in out:
                out.append(s)
    return out


def _dropped_requirements(text: str, sections: list[dict], title: str) -> list[str]:
    """**用户点名、但没被纳入结构**的小标题 —— 如实报出，**不静默丢**（项目红线）。

    ⚠️ 这是原 `unrecognized_requirements` 的**句子粒度修法**：原实现拿
    `_REQ_CLAUSE` 抓到的**整片段**（`服务周期和应急预案`）去问
    `extract_required_sections`，只要片段里**任一**模块命中，整片段就被判为「已认出」→
    **片段里的「服务周期」被连带跳过，一条告警都没有**（2026-09-18 实测：
    「售后服务方案，必须包含服务周期和应急预案」的告警是**空**）。
    现在按**切分后的小标题**逐个核对，粒度对了才不会连带。
    """
    got = {s["name"] for s in sections} | {title}
    out: list[str] = []
    for name in _split_req_clause(text):
        if name in got or any(name in g or g in name for g in got if g):
            continue
        if name not in out:
            out.append(name)
    return out


def parse_outline(raw) -> dict:
    """把用户给的 outline（dict 或 Markdown 文本）规整成 `{"title": str, "sections": [...]}`。

    **两种形态都收**（前端给文本框，同事可能直接粘一份 Markdown）：
      · dict：`{"title": "售后解决方案", "sections": ["售后服务团队", ...]}`
      · 文本：`# 售后解决方案` + `## 售后服务团队` …（`#` 数量不敏感，**层级由出现顺序定**：
        第一个标题是大标题，其余是小标题；没有 `#` 时**首行**当大标题）
    规整不出任何标题 → 返回空 dict（调用方据此**不施加约束**，不猜）。
    """
    if isinstance(raw, dict):
        title = str(raw.get("title") or "").strip()
        secs = [str(s).strip() for s in (raw.get("sections") or []) if str(s).strip()]
        return {"title": title, "sections": secs} if (title or secs) else {}
    text = str(raw or "").strip()
    if not text:
        return {}
    heads = [ln.lstrip("#").strip() for ln in text.splitlines() if ln.strip().startswith("#")]
    if heads:
        return {"title": heads[0], "sections": heads[1:]}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return {"title": lines[0], "sections": lines[1:]} if lines else {}


def format_outline_block(outline: dict) -> str:
    """outline → 提示词里的**硬性结构块**（生成时逐字遵守）。"""
    title = (outline.get("title") or "").strip()
    secs = [s for s in (outline.get("sections") or []) if s]
    lines = ["【必须逐字遵守的标题结构】（本次方案的输出骨架，**不得增删改名、不得调整顺序**）"]
    if title:
        lines.append(f"大标题（一级标题 `#`）：{title}")
    if secs:
        lines.append("小标题（二级标题 `##`），按此顺序各一节：")
        lines += [f"  {i}. {s}" for i, s in enumerate(secs, 1)]
    lines.append("每个小标题下的**内容**仍必须遵守下面的引用与证据规则（标题是骨架，不是内容）。")
    return "\n".join(lines) + "\n"


def build_gen_prompt(payload: dict, constraints: str = "", kb: list[dict] | None = None,
                     product: str = "", outline: dict | None = None) -> str:
    """把**模块化经验** + 证据包 + 用户约束(+产品线) 拼成提示词。

    两段分工明确（R7-05 的范围未变：仍然只放命中原文、来源、约束）：
      - `kb`（可选）：**归纳自历史文件**的结构与口径（常用小节、承诺时限）。
        它告诉模型「我们通常怎么写」；**它本身不是原文，不得被引用**。
      - 证据包：**这一次可引用的原文**，带 `[E1]` 编号。
    两者混在一起会让模型把归纳句当原文引用，故显式分段并在提示里写明禁令。

    `product`（2026-09-15 加，见 PLAN-20260915 §8-3）：**方案按产品线裁剪**。
    用户裁定"全部模块都按产品特异处理、调用时交给 LLM 区分" —— 故这里只**如实告知产品线**
    并给一条裁剪规则（`GEN_SYSTEM` 规则 9），**不做人工的通用/特异清单**。

    `outline`（2026-09-17 加，需求方第二次对接的需求2）：用户**指定的标题结构**
    `{"title": "售后解决方案", "sections": ["售后服务团队", ...]}` ——
    模型必须**逐字**按它输出（大标题一级、小标题二级、顺序一致、不得增删改名）。
    只约束**输出结构**，不改变证据召回（召回仍按 `MODULE_KEYWORDS` 的模块名走）。
    `None`/空 = 不约束，行为与加此参数前**完全一致**。
    """
    lines: list[str] = []
    if outline and (outline.get("title") or outline.get("sections")):
        lines.append(format_outline_block(outline))
    if product:
        lines.append(f"【本次方案的产品线】{product}"
                     f"（证据里若并列了其它产品线的条目，只保留与本产品线相关的）\n")
    if constraints.strip():
        lines.append(f"【用户要求必须包含的内容】\n{constraints.strip()}\n")
    if kb:
        lines.append(kb_to_prompt_block(kb) + "\n")
    lines.append("【历史证据】（只能引用下列编号）")
    for m in payload.get("modules", []):
        lines.append(f"\n### 小节：{m.get('display') or m['module']}（证据状态：{m['status']}）")
        if not m.get("evidence"):
            lines.append("（无证据）")
        for e in m["evidence"]:
            lines.append(f"[{e['ref']}] 来源：{e.get('file_name')}"
                         f"（项目：{e.get('project_folder')}"
                         f"｜项目简介：{e.get('project_summary') or '—'}"
                         f"｜文件用途：{e.get('doc_purpose') or '—'}"
                         f"｜章节：{e.get('heading') or '无标题'}）")
            lines.append(e.get("text") or "")
    lines.append("\n请按上述规则输出 Markdown 方案。")
    return "\n".join(lines)


_NUM = re.compile(r"\d+(?:\.\d+)?")
# 要求动词前缀：「必须包含服务周期」→「服务周期」（真机实测模型会用这句话当小节标题）
_REQ_VERB = re.compile(r"^(?:必须|需要|要有|要|请|务必|一定)?(?:包含|包括|含|有|写|提供)?")
# 2026-09-14 补 `日`/`周`：招标文件写「★|交货时间|自合同签订之日起**60日内**交付」，
# 只认「天」会把这条**★硬性条款**整条漏掉（实测一份 46k 字的招标文件因此少抽 1 条）。
_NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(分钟|个小时|小时|个工作日|工作日|天|日|周|[hH](?![A-Za-z]))")
# `年` 单列：**必须限位数**。`2024年`是日期不是时长，而 `不少于2年` 才是承诺期。
# （2026-09-14 实测：`售后服务期不少于2年` vs `不少于5年` 是真冲突，但四位数年份会灌进大量噪声。）
# `年` 是否作为时限提取：**暂不**（见上面 `_TIME_SLOTS` 的说明 —— 归不到槽位只会产生噪声）。
# 保留正则与换算，改主意时一行即可启用。
_YEAR_UNIT = re.compile(r"(?<!\d)(\d{1,3})\s*年")
# 中文数字的时限（`一周内解决`、`三个月`、`半个月`）—— 阿拉伯数字正则抓不到。
# 实测招标文件里「其他无法迅速解决的问题在**一周内**解决」就是这种写法，
# 漏掉会让「逐条列出招标要求」不完整。
_CN_DIGIT = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}
_CN_TIME = re.compile(r"([一两二三四五六七八九十半])\s*个?\s*(小时|工作日|天|日|周|个月|月|年)")
_CN_MINUTES = {"小时": 60, "工作日": 480, "天": 1440, "日": 1440,
               "周": 10080, "个月": 43200, "月": 43200, "年": 525600}
_YEAR_UNIT_ENABLED = False
_UNIT_MINUTES_MORE = {"h": 60, "H": 60, "年": 525600, "日": 1440, "周": 10080,
                       "个月": 43200, "月": 43200}
# `7×24小时` / `7*24小时` 里的 `24小时` 意思是「**全天候**」，不是「24 小时内」的时限承诺。
# 它会被 `_NUM_UNIT` 直接抓成时限（实测：九模块 6 条告警里第 3 条就是它造的假冲突）。
_NON_DEADLINE_BEFORE = "×*xX✕╳"


class _Mention(NamedTuple):
    """一条时限提及。**用统一结构而不是正则 Match** —— `_NUM_UNIT` 有 2 个捕获组、
    `_YEAR_UNIT` 只有 1 个，调用方直接 `group(2)` 会对后者抛 IndexError（实测踩到）。"""
    value: str
    unit: str
    raw: str
    start: int
    end: int


def _time_mentions(text: str):
    """正文里的**时限**提及：`_NUM_UNIT`（含 `24h` 这类缩写）+ `_YEAR_UNIT`（承诺年限）。

    剔除两类非时限用法：
      · `7×24小时` 的 `24小时` —— 那是「**全天候**」，不是「24 小时内」的承诺；
      · `2024年` —— 四位数是**日期**，不是时长（`_YEAR_UNIT` 用 `\d{1,3}` 挡掉）。
    """
    for m in _NUM_UNIT.finditer(text):
        if m.start() and text[m.start() - 1] in _NON_DEADLINE_BEFORE:
            continue
        # `日` 作为单位时必须防**日期**：`2020年1月1日` 里的 `1日` 不是时限。
        # 判据：数字前面紧挨着 `N月`（可带空格）。
        if m.group(2) == "日" and re.search(r"\d{1,2}\s*月\s*$", text[max(0, m.start() - 6):m.start()]):
            continue
        yield _Mention(m.group(1), m.group(2), m.group(0), m.start(), m.end())
    # 中文数字的时限（`一周内`、`三个月`）—— 走同一条 _Mention 结构，单位已归一到分钟数
    for m in _CN_TIME.finditer(text):
        if m.start() and text[m.start() - 1] in _NON_DEADLINE_BEFORE:
            continue
        if m.group(2) in ("日", "月") and re.search(r"\d{1,2}\s*月\s*$",
                                                    text[max(0, m.start() - 6):m.start()]):
            continue
        yield _Mention(str(_CN_DIGIT[m.group(1)]), m.group(2), m.group(0), m.start(), m.end())
    if not _YEAR_UNIT_ENABLED:
        return
    for m in _YEAR_UNIT.finditer(text):
        if m.start() and text[m.start() - 1] in _NON_DEADLINE_BEFORE:
            continue
        yield _Mention(m.group(1), "年", m.group(0), m.start(), m.end())
# 时限语义槽位：**同单位不等于同一件事** —— 「30 分钟响应」与「24 小时到场」都含时间数字，
# 却不冲突。必须先把数字归到同一个语义槽位再比，否则会把无关数字报成冲突（误报比漏报更糟）。
_TIME_SLOTS = ("响应", "回复", "反馈", "到场", "上门", "交付", "完成", "解决",
               "服务周期", "质保", "保修", "供货周期")
# 同一概念的**不同说法**归一：招标文件写「一周内**解决**」、响应文件写「**完成**处置」，
# 不归一的话两边槽位对不上，`compare_with_history` 会把它误判成「历史未覆盖」。
_SLOT_ALIAS = {"解决": "完成"}
# ⚠️ 槽位词的**假朋友**：招标文件里「响应」绝大多数是「**响应文件**」「响应截止时间」
# 「响应供应商」—— 那是**投标文件本身**，不是服务响应。不排除就会把资格要求当成服务时限：
# 实测「首次**响应文件**递交截止时间前**六个月**内任一月份的税收凭据」被抽成「响应 六个月」、
# 「参加采购活动前**三年**内被列入失信名单」被抽成「响应 三年」——两条都是资格要求，与响应时限无关。
_SLOT_BLOCK_AFTER = {
    "响应": ("文件", "截止", "供应", "人", "函", "表", "无效", "性", "偏离"),
    "回复": ("函",),
    "交付": ("物",),
}
# ⚠️ 2026-09-14 试过给「承诺年限」加 `售后`/`服务期` 两个槽位词，**实测帮倒忙、已撤回**：
# 它们太宽，把「售后服务响应 2 小时」与「售后…20 个工作日」都归到同一个「售后」槽，
# 九模块告警从 4 条涨到 7 条且新的全是假阳性。**年限类的冲突暂不自动报**
# —— 宁可不报，也不要制造假阳性让用户以为是真冲突。
# 时限**紧跟其后**的动作动词里，这些**不是**槽位词 —— 出现即说明该时限管的是它，
# 此时**不再回看前文**（否则会把 14 字外的「响应机制」误当归属，见 `_slot_of` 的说明）。
_NON_SLOT_ACTION = ("提交", "出具", "上报", "备案", "启动", "发出", "安排", "报送", "汇总")
_AFTER_LOOKAHEAD = 8
_UNIT_MINUTES = {"分钟": 1, "个小时": 60, "小时": 60,
                 "个工作日": 480, "工作日": 480, "天": 1440}
_SLOT_WINDOW = 14
# 引用编号的**所有常见括号形态**都要认（2026-09-18 需求方实测）：
# 模型会用全角【E3】而不是半角 [E3]，而原先只认半角 → 后果**成串**：
#   ① 校验器认不出引用 → 报「正文没有任何引用编号」；
#   ② 编号里的数字被当正文数字 → 报「无法回溯的数字：['3']」；
#   ③ 前端 `renderMarkdown` 也只认半角 → 【E3】**原样留在正文里**，不成上标、不成可点引用。
# 指令里已写明「形如 [E3]」，但**输出的括号形态不该由提示词一处兜底**——工具链三处必须都认。
_CITE = re.compile(r"[(（\[【［「](E\d+)[)）\]】］」]")


def _slot_of(text: str, start: int, end: int) -> str | None:
    """数字归属的**语义槽位**。

    **取最近的**（不是元组里第一个命中的）：`…3 小时响应，24 小时到场处理。` 里 24 的邻域同时含
    「响应」和「到场」，取第一个会把「到场」误判成「响应」（实测踩到），进而报出假冲突。

    **后向优先**（2026-09-14 加）：`1小时内响应，4小时内到场` 里，4 小时与前一个「响应」的
    距离和与后一个「到场」的距离**相等（都是 1）**，而原实现用「严格小于」保留先找到的
    （`_TIME_SLOTS` 里「响应」在前）→ 归成「响应」，与紧跟其后的「到场」打架（实测九模块
    6 条告警里的第 5 条）。**时限管的是它后面那件事**，故等距时后向胜。

    **后接非槽位动词时抑制前向匹配**（2026-09-14 加）：`响应机制：技术总监牵头处置，4小时内
    提交应急报告` —— 该时限管的是「**提交**报告」，不是 14 字外那个「响应机制」的「响应」。
    原实现会归成「响应」，与「10分钟内响应」的销售响应凑成假冲突（实测第 2 条）。
    故若时限**紧跟其后**是一个明确动作动词（且不是槽位词），则**不再回看前文**、返回 None
    （宁可漏报，也不制造假冲突 —— 假阳性会让人以为每个数字都要人工核）。
    """
    lo, hi = max(0, start - _SLOT_WINDOW), min(len(text), end + _SLOT_WINDOW)
    window = text[lo:hi]
    after = text[end:end + _AFTER_LOOKAHEAD]

    # ① 紧跟其后的动作动词 —— 最能说明这个时限管什么
    for _pos, s in sorted(((after.find(s), s) for s in _TIME_SLOTS if s in after),
                          key=lambda x: x[0]):
        tail = after[_pos + len(s): _pos + len(s) + 2]
        if any(tail.startswith(b) for b in _SLOT_BLOCK_AFTER.get(s, ())):
            continue          # 「响应文件」「响应截止」→ 不是服务响应，跳过这个候选
        return _SLOT_ALIAS.get(s, s)
    if any(v in after for v in _NON_SLOT_ACTION):
        return None          # 管的是「提交/出具/上报…」，不是前文那个槽位词

    # ② 回看前文：用**真实距离**排序，后向只给 0.5 的等距偏好
    #    ⚠️ 首版把方向编码成「后向取负」，结果**任意靠后的槽位都赢过最近的前向槽位** ——
    #    `服务响应时间为 2 小时，重大问题 24 小时到场处理` 里，「2 小时」被 11 字外的「到场」
    #    抢走（它只该赢等距的场合）。必须用距离本身比较。
    best: str | None = None
    best_d: float | None = None
    for s in _TIME_SLOTS:
        pos = window.find(s)
        while pos >= 0:
            a, b = lo + pos, lo + pos + len(s)
            if b <= start:
                d = float(start - b)          # 在前：正距离
            elif a >= end:
                d = float(a - end) - 0.5      # 在后：距离减 0.5 → **等距时**后向胜，更远仍输
            else:
                d = -0.5                      # 与时限重叠（如「响应时间」紧贴数值）：最强
            # 前向窗口同样要排除「响应文件」这类假朋友
            tail = text[b: b + 2]
            if any(tail.startswith(x) for x in _SLOT_BLOCK_AFTER.get(s, ())):
                pos = window.find(s, pos + 1)
                continue
            if best_d is None or d < best_d:
                best, best_d = s, d
            pos = window.find(s, pos + 1)
    return _SLOT_ALIAS.get(best, best) if best else best


def _context_of(text: str, start: int, end: int, width: int = 22) -> str:
    """时限在原句里的**语境片段**（前后各取一段，压成一行）。

    加它的理由（2026-09-14 实测）：告警只列「7个工作日、30个工作日」时，读的人必须**回原文**
    才知道一个是「重检交付结果」、一个是「项目总工期」——**不是同一件事**。
    把语境一并给出，一眼可判，不必翻文件。
    """
    seg = text[max(0, start - width): min(len(text), end + width)]
    return re.sub(r"\s+", " ", seg).strip()


def detect_conflicts(payload: dict) -> list[dict]:
    """同一小节、**同一语义槽位**内的时限数值冲突 → 警告（**不自动择一**，R7-06/项目红线）。

    单位归一后再比（30 分钟 vs 2 小时 → 同一件事的两种写法）。槽位不同不算冲突。

    **三条收紧（2026-09-14，实测九模块 6 条告警全是假阳性）**：
      1. 槽位判定的后向优先与「后接非槽位动词则不回看」（见 `_slot_of`）；
      2. 剔除非时限用法（`7×24小时` 是「全天候」，见 `_time_mentions`）；
      3. **同一份证据内部的多个值不算冲突** —— 那多半是文档自己的**分级/分场景设计**
         （实测：「Ⅰ级…24小时处置」与「Ⅲ级…」、「样本不合格 3 日」与「文库失败 5 日」），
         而本告警的措辞是「**在不同历史文件里**不一致」，同文件的多值放进来说不通。
         跨证据的仍照报（如 E1「30 分钟响应」vs E2「2 小时响应」——同一承诺的两种写法）。
    """
    out: list[dict] = []
    for m in payload.get("modules", []):
        # slot -> {归一分钟后数值: {"raw": (值, 单位), "refs": [...], "ctx": [...]}}
        slots: dict[str, dict[float, dict]] = {}
        for e in m.get("evidence", []):
            text = e.get("text") or ""
            for mm in _time_mentions(text):
                slot = _slot_of(text, mm.start, mm.end)
                if not slot:
                    continue
                val, unit = mm.value, mm.unit
                try:
                    minutes = float(val) * _UNIT_MINUTES.get(unit, _UNIT_MINUTES_MORE.get(unit))
                except (ValueError, KeyError):
                    continue
                rec = slots.setdefault(slot, {}).setdefault(
                    minutes, {"raw": mm.raw.replace(" ", ""), "refs": [], "ctx": []})
                # 同一份证据里同一时限可能被提到多次（如"10 分钟响应…10 分钟内到场"），
                # 逐次 append 会产出 ['E7','E7'] 这种重复，用户会怀疑数字算错了。
                # 按出现顺序去重 —— 告警是给人看的，重复引用只会损伤可信度。
                if e["ref"] not in rec["refs"]:
                    rec["refs"].append(e["ref"])
                ctx = _context_of(text, mm.start, mm.end)
                if ctx and ctx not in rec["ctx"]:
                    rec["ctx"].append(ctx)
        for slot, vals in slots.items():
            if len(vals) <= 1:
                continue
            # 收紧 ③：全部值都来自**同一份证据** → 是该文档自己的分档，不是"不同文件不一致"
            if len({r for v in vals.values() for r in v["refs"]}) < 2:
                continue
            ordered = sorted(vals.items())
            out.append({
                "module": m["module"], "slot": slot,
                "values": [v["raw"] for _, v in ordered],
                "sources": {v["raw"]: v["refs"] for _, v in ordered},
                "contexts": {v["raw"]: v["ctx"][0] if v["ctx"] else "" for _, v in ordered},
                "message": f"「{m['module']}」的「{slot}」在不同历史文件里不一致："
                           + "；".join(f"{v['raw']}（…{v['ctx'][0]}…）" if v["ctx"] else v["raw"]
                                       for _, v in ordered)
                           + " —— 请人工确认，系统不自动择一",
            })
    return out


def validate_generation(markdown: str, payload: dict, constraints: str = "",
                        outline: dict | None = None) -> list[str]:
    """R7-07 生成后校验。返回问题列表（空=通过）。**只报问题，不改写正文**。

    **小节覆盖判据**（实测修正）：模型会按用户要求改小节标题 —— 用户说「必须包含服务周期」，
    模型就把标题写成「服务周期」而不是模块名「售后方案」。所以覆盖判定接受三者**任一**：
    模块名 / 模块关键词 / 用户声明的必含内容。否则会在真实输出上**常态误报**，
    而一个总在误报的校验器会被用户直接忽略，等于没有。
    """
    problems: list[str] = []
    avail = {e["ref"] for m in payload.get("modules", []) for e in m.get("evidence", [])}
    used = set(_CITE.findall(markdown or ""))
    fake = sorted(used - avail)
    if fake:
        problems.append(f"引用了不存在的编号（编造）：{fake}")
    # 「正文没有引用」只在**存在可用引用**时才成立：全部小节 insufficient 时，
    # 按 GEN_SYSTEM 第 5 条本来就该写「历史材料未覆盖」且不带引用，固定报错属假报
    # （对抗性复核实测）。
    if not used and avail:
        problems.append("正文没有任何引用编号")

    # 必要小节覆盖：模块名 / 模块关键词 / 用户约束里**不是别的模块名**的 token。
    # ⚠️ 约束 token 必须排除模块名：否则约束切片产出的**另一个模块名**会满足所有模块的检查，
    # 真正缺的小节不报（对抗性复核实测：constraints 含「应急预案」时，只写应急预案的正文
    # 被判定「售后方案」也在）。
    other_mods = set(MODULE_KEYWORDS)
    c_tokens = set()
    for t in re.split(r"[、，,和与及\s]+", constraints or ""):
        # 同时收原 token 与**剥掉要求动词后的残句**：真机实测模型会把小节标题写成用户措辞
        # （约束「必须包含服务周期」→ 标题「## 服务周期」），只收原串会再次误报「缺少小节」。
        for cand in (t, _REQ_VERB.sub("", t)):
            if len(cand) >= 2 and cand not in other_mods:
                c_tokens.add(cand)
    for m in payload.get("modules", []):
        # ⚠️ **给了 outline 时跳过模块名覆盖检查** —— outline 是**用户钦定的骨架**：
        # 逐字标题由 `_validate_outline` 负责校验；模块名与用户标题**本就不同名**
        # （「售后解决方案」vs 模块名「售后方案」），继续按模块名判必然误报（实测踩到）。
        # 且用户未列进结构的小节，其证据不被引用是**合规**的，不该报「缺少小节」。
        if outline and (outline.get("title") or outline.get("sections")):
            continue
        labels = {m["module"], m.get("display") or m["module"],
                  *MODULE_KEYWORDS.get(m["module"], ())} | c_tokens
        if not any(lbl in (markdown or "") for lbl in labels):
            problems.append(f"缺少必要小节：{m.get('display') or m['module']}")

    # 关键数字溯源：正文里的数字必须能在证据原文里找到。
    # ⚠️ **必须先剥掉引用标记再抽数字**：`[E23]` 里的 `23` 会被 `_NUM` 当成正文数字抠出来，
    # 而 `used` 里存的是带前缀的 `"E23"`（不是 `"23"`）—— 直接比对会让**任何 [E10]..[En] 引用
    # 都被伪报成「无法回溯的数字」**。真实九模块证据包有 29 条引用，等于真实生成必踩。
    # （对抗性复核实测复现：`[E23]` → 伪报 `['23']`；换成 `[E6]` 则无此问题。）
    prose = _CITE.sub(" ", markdown or "")
    # 引用清单里编号写成 `` `E13` ``（反引号，便于阅读）—— `_CITE` 只剥括号形态，
    # 不剥它会把编号里的数字（`13`）误报成「无法回溯」。实测抽取式装配上残留 2 个。
    prose = re.sub(r"[`']?E\d+[`']?", " ", prose)
    # 「可回溯」的对照集合必须**含证据自身的文件名与标题** —— 它们会被合法地写进输出的出处行
    # （如 `出处：…响应文件-20250521….docx`、`（8.11 质量控制…）`）。
    # 不纳入会把出处行里的数字误报成「无法回溯」——实测抽取式装配上一报就是 7 个。
    evidence_blob = "\n".join(
        f"{e.get('text') or ''}\n{e.get('file_name') or ''}\n{e.get('heading') or ''}"
        for m in payload.get("modules", []) for e in m.get("evidence", []))
    evidence_nums = set(_NUM.findall(evidence_blob))
    # 用户约束里的数字也算「有出处」：R7-05 把约束随证据一起下发，模型照抄用户给的数字
    # （如「服务周期不少于24个月」）是**合规**行为，判成「无法回溯」是假报（对抗性复核实测）。
    evidence_nums |= set(_NUM.findall(constraints or ""))
    untraceable = sorted({n for n in _NUM.findall(prose)
                          if n not in evidence_nums and len(n) >= 2})
    if untraceable:
        problems.append(f"无法回溯到证据的数字：{untraceable}")
    problems.extend(_validate_outline(markdown or "", outline))
    problems.extend(_style_problems(markdown or ""))
    return problems


_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$", re.M)


def _validate_outline(markdown: str, outline: dict | None) -> list[str]:
    """校验输出标题结构是否**逐字**符合用户指定的 outline（只报问题，不改写）。

    需求方 2026-09-17 明确要求「严格按那个标题来生成」—— 提示词已下硬约束，
    但**模型漂移必须被报出来**（一个不报的校验器等于没有校验）。

    报三类问题：缺小标题 / 大标题不符或缺失 / 出现 outline 之外的二级标题。
    标题比对**容忍编号前缀与空白**（`1. 售后服务团队` / `## 1、售后服务团队` 都算命中）——
    实测模型会自己加序号，那是合规的排版，不是漂移。
    """
    if not outline or not (outline.get("title") or outline.get("sections")):
        return []
    heads = _HEADING_RE.findall(markdown or "")
    lvl1 = [h[1] for h in heads if len(h[0]) == 1]
    lvl2 = [h[1] for h in heads if len(h[0]) == 2]

    def norm(s: str) -> str:
        return re.sub(r"^[（(]?[\d一二三四五六七八九十]+[）)、.．\s]*", "", (s or "").strip())

    problems: list[str] = []
    title = (outline.get("title") or "").strip()
    if title:
        if not lvl1:
            problems.append(f"标题结构不符：缺少大标题（一级标题）「{title}」")
        elif not any(norm(h) == norm(title) or norm(title) in h for h in lvl1):
            problems.append(f"标题结构不符：大标题应逐字为「{title}」，实际为 {lvl1}")
    want = [s for s in (outline.get("sections") or []) if s]
    missing = [s for s in want
               if not any(norm(h) == norm(s) or norm(s) in h for h in lvl2)]
    if missing:
        problems.append(f"标题结构不符：缺少小标题 {missing}（要求逐字使用，不得改名）")
    extra = [h for h in lvl2
             if not any(norm(h) == norm(s) or norm(s) in h for s in want)]
    if want and extra:
        problems.append(f"标题结构不符：出现了结构外的二级标题 {extra}（不得自行增补小节）")
    return problems


# 句首「根据 Ex，」的写法（需求方 2026-09-18 实测反馈：「现在的输出会显示根据【E3】 这个需要优化」）。
# ⚠️ **只报不拦**：那是**行文风格**问题，不是事实错误 —— 拦下来会让用户拿不到草稿。
# 由提示词（`GEN_SYSTEM` 规则 10）负责改写法，这里只如实提醒。
_CITE_HEAD = re.compile(r"(?:^|[。；;！？\n])\s*(?:根据|依据|按照|据)\s*"
                        r"[(（\[【［「]E\d+[)）\]】］」](?:和|与|、|及)?\s*[(（\[【［「]?E?\d*[)）\]】］」]?"
                        r"[，,、]?")


def _style_problems(markdown: str) -> list[str]:
    """行文风格类提醒（**不改写正文**，只提示）。

    `根据【E3】，…` 逐条开头会让整篇读起来像检索日志而不是方案；同句重复引用
    （`[E3] [E3]`）也损伤可读性。两者都是模型输出里实测出现的。
    """
    md = markdown or ""
    out: list[str] = []
    hits = len(_CITE_HEAD.findall(md))
    if hits >= 3:
        out.append(f"行文风格：有 {hits} 处以「根据 Ex，」开头 —— 读起来像检索日志而非方案。"
                   f"应把引用编号移到**句末**（如「…提供不少于 2 年的售后服务 [E3]。」）。")
    dup = re.findall(r"([(（\[【［「]E\d+[)）\]】］」])\s*\1", md)
    if dup:
        out.append(f"行文风格：同一编号在同句里重复出现 {len(dup)} 处（如 {dup[0]*2}）—— "
                   f"保留一个即可。")
    return out


def assemble_proposal(payload: dict, constraints: str = "") -> dict:
    """**本地抽取式装配**：把 Evidence Pack 里的原文按小节组织成方案草稿。**零外发。**

    为什么要有它（2026-09-12）：模型生成那一步必须外发正文，而本会话的权限层明确拒绝且
    声明「对话中的授权清不掉它」。但需求二的交付物是**一份可用的方案草稿**，这件事不一定非要模型做 ——
    计划对生成结果的五条要求，**抽取式装配天然全部满足，且比模型更严格**：

    | 计划要求 | 抽取式装配 |
    |---|---|
    | 覆盖用户指定的必要条件 | 每个点名小节都有小节块（不足时如实标注） |
    | **只使用我方响应/最终版** | **由构造保证**（证据包已按 `VALID_ROLES` 过滤） |
    | **每个关键事实/时限/数字可回溯源文件** | **由构造保证** —— 每句都是原文，逐字可核对 |
    | 多份材料冲突时明确提示、不自动择一 | `detect_conflicts()` |
    | 证据不足时明确说明、不补造 | `sparse`/`insufficient` 如实标注，**不生成任何非证据文字** |

    **核心性质：本函数不产生一个字的非证据内容**（小节标题、程序性说明除外）。
    模型路径（`generate_proposal`）仍在，默认关闭、需授权；两条路并存，`mode` 字段区分，
    产物**永不混淆**。
    """
    lines: list[str] = ["# 方案草稿（本地抽取式装配）", ""]
    if constraints.strip():
        lines += [f"> 用户要求必须包含：{constraints.strip()}", ""]
    warnings = detect_conflicts(payload)
    gaps = [m["module"] for m in payload.get("modules", []) if m["status"] != "ok"]
    if warnings:
        lines += ["## ⚠️ 数值冲突（系统不自动择一，请人工确认）", ""]
        # 引用一律写成 `[En]` **方括号形式**：与全文一致，且校验器只认这种形式——
        # 写成 `'En'`（f-string 加引号）会被 `_NUM` 当成正文数字、误报「无法回溯」（实测 5 例）。
        lines += [f"- {w['message']}（来源："
                  + "、".join(f"{v}=" + "".join(f"[{r}]" for r in rs)
                              for v, rs in w["sources"].items()) + "）"
                  for w in warnings]
        lines += [""]
    if gaps:
        lines += ["## ⚠️ 证据不足的小节", ""]
        lines += [f"- {g}" for g in gaps]
        lines += [""]

    citations: list[dict] = []
    n = 0
    for m in payload.get("modules", []):
        lines.append(f"## {m['module']}")
        lines.append("")
        ev = m.get("evidence") or []
        if m["status"] == "insufficient" or not ev:
            lines += ["> **历史材料未覆盖本小节。** 系统不补造内容，请人工撰写或补充历史材料。", ""]
            continue
        if m["status"] == "sparse":
            lines += [f"> **历史材料不足**（去重后仅 {m['deduped']} 条，标题命中 {m['heading_hits']} 条）。"
                      "以下为可查到的片段，**不构成完整小节**。", ""]
        for e in ev:
            n += 1
            ref = e["ref"]
            citations.append({"ref": ref, "file_name": e.get("file_name"),
                              "source_path": e.get("source_path"),
                              "heading": e.get("heading"),
                              "amount_source": e.get("amount_source")})
            text = (e.get("text") or "").strip()
            lines.append(f"- {text} [{ref}]")
            lines.append(f"  - 出处：{e.get('file_name')}"
                         f"（项目：{e.get('project_folder')}"
                         f"｜项目简介：{e.get('project_summary') or '—'}"
                         f"｜文件用途：{e.get('doc_purpose') or '—'}"
                         f"｜{e.get('heading') or '无标题'}）")
        lines.append("")
    lines += ["---", "", "## 引用来源（逐条可打开核对）", ""]
    lines += [f"- `{c['ref']}` {c.get('heading') or '（无标题）'} — {c.get('file_name')}"
              for c in citations]
    markdown = "\n".join(lines)
    return {
        "markdown": markdown,
        "mode": "local_extractive",          # 与模型产物**永不混淆**
        "citations": citations,
        "warnings": warnings,
        "gaps": gaps,
        # 复用同一套校验：抽取式装配应当**由构造通过**（若不过，说明实现有 bug）
        "validation": validate_generation(markdown, payload, constraints),
        "usage": {"passages": n, "model": None},
    }


def generate_proposal(payload: dict, constraints: str = "", kb: list[dict] | None = None,
                      product: str = "", outline: dict | None = None) -> dict:
    """R7-05/06/07：证据包 → 方案正文 + 引用清单 + 冲突/缺口警告 + 校验结果。

    `kb` 为「模块化经验」（`build_module_kb` 产出），由调用方从库里整理后传入 ——
    本函数不持有数据库连接，且整理是**零外发**的本地步骤，与生成分开。

    ⚠️ 这一步会把我方响应文件正文送往外发网关。授权记录见
    `docs/authorizations/llm-generation-authorization.md`；未开启 `config.PROPOSAL_GEN_ENABLED` 时直接抛错。
    """
    _guard()
    client, model = _gen_client()
    prompt = build_gen_prompt(payload, constraints, kb, product=product, outline=outline)
    try:
        resp = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system", "content": GEN_SYSTEM},
                      {"role": "user", "content": prompt}],
            extra_body={"enable_thinking": False},
        )
        markdown = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenError(f"生成调用失败：{str(exc)[:200]}") from exc
    if not markdown:
        raise ProposalGenError("模型未返回内容")

    avail = {e["ref"]: e for m in payload.get("modules", []) for e in m.get("evidence", [])}
    used = sorted(set(_CITE.findall(markdown)),
                  key=lambda r: int(r[1:]))
    warnings = detect_conflicts(payload)
    gaps = [{"module": m["module"], "status": m["status"], "notes": m.get("notes", [])}
            for m in payload.get("modules", []) if m["status"] != "ok"]
    return {
        # ⚠️ `mode` **必须**回传，与 `assemble_proposal` 的 `local_extractive` 配对。
        # 产品红线原文：「`mode` **必须展示给用户**——本地拼装与模型起草的产物可信度不同」。
        # 原先本函数**没有**这个字段（返回 `mode=undefined`）→ 前端 `MODE_LABEL[undefined]`
        # 取不到标签、页面显示不出"这是模型起草的"。该缺陷一直存在但**从未暴露**，
        # 因为 LLM 路径此前从未被启用过（2026-09-13 首次真实生成时发现）。
        "mode": "llm",
        "markdown": markdown,
        "citations": [{"ref": r, "file_name": avail[r].get("file_name"),
                       "source_path": avail[r].get("source_path"),
                       "heading": avail[r].get("heading")} for r in used if r in avail],
        "warnings": warnings,
        "gaps": gaps,
        "validation": validate_generation(markdown, payload, constraints, outline),
        "usage": {"prompt_chars": len(prompt), "model": model},
    }


# ============================================================================
# 模块化整理（需求二：把历史经验按模块归纳成可复用的「模块化内容」）
# ============================================================================

def build_module_kb(con, modules: list[str], *, pool_size: int = 60,
                    max_sections: int = 30, max_per_project: int = 3) -> list[dict]:
    """把历史响应文件里的章节按模块整理成**可复用的模块化经验**（**确定性、零外发**）。

    与 `build_evidence_packs` 的分工：
      - 证据包 = 「**这一次**生成要引用的 3~5 段原文」（面向引用与追溯）；
      - 本函数 = 「这个模块历史上**我们写过什么**」的**跨项目归纳**（面向复用与提示词）。
    用户 2026-09-13 的目标就是后者：「在以往的投标文档中整理出模块化的内容，
    生成方案时把这些模块化的内容当作提示词发给 LLM，让它根据这些模块化的经验去生成方案」。

    **为什么这一步不需要 LLM**：归纳所需的材料（近重复聚类、时限数值、小节标题）都是
    确定性的，现成能力已够（`_cluster` / `_slot_of` / `_UNIT_MINUTES`）。
    故本函数**零外发**——外发只发生在最后的「生成」一步，且那份 prompt 里的原文范围不变。

    每个模块产出：
      - `coverage`  覆盖多少份文件 / 多少个项目（**覆盖窄时必须让用户看见**）
      - `outline`   历史用过的小节标题（去重，按出现次数）
      - `commitments` 承诺时限按**语义槽位**归纳；同槽位多值 = 冲突，**如实并列不择一**
      - `key_points` 去重后的要点（每项目限额，避免一个项目刷屏）
      - `sources`   来源文件与项目（可回溯）
    """
    out: list[dict] = []
    for module in modules:
        if module not in MODULE_KEYWORDS:
            continue
        packs = build_evidence_packs(con, [module], pool_size=pool_size,
                                     max_packs=max_sections, min_packs=0,
                                     max_per_project=max_per_project)
        p = packs[0] if packs else None
        evs = p.evidence if p else []
        docs = {e.get("document_id") for e in evs if e.get("document_id")}
        projs = {(e.get("project_folder") or e.get("project_key") or "") for e in evs}

        outline: Counter = Counter()
        for e in evs:
            h = (e.get("heading") or "").strip()
            if h:
                outline[h] += 1

        # 承诺时限：与 `detect_conflicts` 同一套槽位与单位归一，**保证两处口径一致**
        # （汇总页说「响应 30 分钟」，生成告警说「响应有 5 个值」，不能各说各话）。
        slots: dict[str, dict[float, dict]] = {}
        for e in evs:
            text = e.get("text") or ""
            for mm in _time_mentions(text):
                slot = _slot_of(text, mm.start, mm.end)
                if not slot:
                    continue
                try:
                    minutes = float(mm.value) * _UNIT_MINUTES.get(
                        mm.unit, _UNIT_MINUTES_MORE.get(mm.unit))
                except (ValueError, KeyError):
                    continue
                # ⚠️ 证据项**没有 `ref`** —— E1..En 是 `packs_to_payload` 才生成的编号。
                # 这里用 `section_id` 作身份（本函数在 payload 之前跑）。
                rec = slots.setdefault(slot, {}).setdefault(
                    minutes, {"raw": mm.raw.replace(" ", ""), "section_ids": []})
                if e["section_id"] not in rec["section_ids"]:
                    rec["section_ids"].append(e["section_id"])
        commitments = []
        for slot, vals in sorted(slots.items()):
            ordered = sorted(vals.items())
            commitments.append({
                "slot": slot,
                "values": [v["raw"] for _, v in ordered],
                "section_ids": {v["raw"]: v["section_ids"] for _, v in ordered},
                "conflict": len(vals) > 1,
            })

        # 要点：每份证据取首段，**按项目限额**（同一项目复制同一模板会刷屏）
        per_proj: Counter = Counter()
        key_points = []
        for e in evs:
            proj = e.get("project_folder") or e.get("project_key") or ""
            if per_proj[proj] >= max_per_project:
                continue
            text = (e.get("text") or "").strip()
            if not text:
                continue
            per_proj[proj] += 1
            key_points.append({"section_id": e["section_id"],
                               "heading": e.get("heading") or "",
                               "excerpt": text[:220], "file_name": e.get("file_name") or "",
                               "project_folder": proj})

        out.append({
            "module": module,
            "status": p.status if p else "insufficient",
            "coverage": {"files": len(docs), "projects": len(projs),
                         "sections": len(evs), "deduped": p.deduped if p else 0,
                         "heading_hits": p.heading_hits if p else 0},
            "outline": [{"heading": h, "count": n} for h, n in outline.most_common(20)],
            "commitments": commitments,
            "key_points": key_points,
            "notes": p.notes if p else [],
        })
    return out


def kb_to_prompt_block(kb: list[dict]) -> str:
    """把模块化经验渲染成**提示词里的一段**（生成时用）。

    ⚠️ 只放**归纳结果**（承诺槽位、小节结构），不放正文 —— 正文由后面的证据包给，
    两者分工明确：这段告诉模型「我们通常承诺什么、通常怎么分小节」，
    证据包告诉模型「这一次可以引用哪些原文」。混在一起会让模型把归纳句当原文引用。
    """
    lines: list[str] = ["【我司历史模块化经验】（**归纳自历史投标文件，用于把握结构与口径；"
                        "引用时必须用下面证据包里的编号，不得引用本段）】"]
    for m in kb:
        cov = m.get("coverage") or {}
        lines.append(f"\n### {m['module']}（历史覆盖：{cov.get('files', 0)} 份文件 / "
                     f"{cov.get('projects', 0)} 个项目）")
        if m.get("outline"):
            lines.append("常用小节：" + "、".join(o["heading"] for o in m["outline"][:8]))
        if m.get("commitments"):
            for c in m["commitments"]:
                tail = "（历史不一致，需人工确认，**不得自行择一**）" if c["conflict"] else ""
                lines.append(f"  · {c['slot']}：{' / '.join(c['values'])}{tail}")
        else:
            lines.append("  · （历史材料里未提取到明确的时限承诺）")
    return "\n".join(lines)
