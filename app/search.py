"""R4 只读材料定位入口（最小试点：产品 + 金额条件，复用金额状态门槛）。

- 只查询**已核准文件**（白名单 build/_approved_document_ids）对应的有效历史合同。
- 资格门槛（每条记录都校验）：
  - document_role ∈ {our_response, final_signed, contract_evidence}（有效角色）
  - parse_status 非 holding_review/paused/blocked/skipped（非待复核/暂停）
  - 内容版本：documents.canonical_document_id 与 parse_artifacts 存在且配对完整
    （老/变 stale 时不判命中）；明细与 canonical 一起由 sync 同步 —— 若内容已更新而明细未更新
    （自动实现保证不可能，但作为可核验项校验 canonical==file-sha 一致时才作命中依据）。
- 金额状态由 `product_amount_status`（解析 records）判定：unknown/conflict → 不正式命中。
  只有 status='ok' 且 明细求和≥阈值 才 hit=True。
- 金额取自 contract_items detail 求和（同合同内），绝不用 contracts.total_amount，
  也不另写绕过 status 的 SUM。
- 返回 项目、文件名、完整源路径、命中产品、金额、证据状态。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.extract import (parse_contract_service_table, product_amount_status,
                         product_from_filename, product_key_of)

DEFAULT_ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}

# 已核准文件白名单；查询只在这些文件范围内。
# 可审计清单优先：`data/approved_documents.json`（由 docs/ocr-authorization.md 授权范围内的
# 人工核准件 + OCR 批量件生成）。文件缺失时退回内置的两份人工核准件，**不会静默放宽范围**。
_FALLBACK_APPROVED = {
    "b2bbb5507121",  # 单细胞-欧易19.98万
    "d2d84da5c2b0",  # 多组学-鹿明9.76万
}
_APPROVED_FILE = Path(__file__).resolve().parents[1] / "data" / "approved_documents.json"


def _load_approved() -> set[str]:
    """从可审计清单读取已核准文档前缀；读不到则退回内置集合。"""
    try:
        items = json.loads(_APPROVED_FILE.read_text(encoding="utf-8"))
        prefixes = {str(x["document_id"])[:12] for x in items if x.get("document_id")}
        return prefixes or set(_FALLBACK_APPROVED)
    except Exception:  # noqa: BLE001 —— 清单缺失/损坏时退回内置，不放宽
        return set(_FALLBACK_APPROVED)


APPROVED_DOCUMENT_IDS = _load_approved()

VALID_ROLES = ("our_response", "final_signed", "contract_evidence")
BLOCKED_STATUSES = ("holding_review", "paused", "blocked", "skipped")
BLOCKED_SUBTYPES = ("framework_sample",)  # 框架协议/供应商库样稿：资格层拒绝（不正式命中）


@dataclass(frozen=True)
class LocateResult:
    contract_id: str
    document_id: str
    project_folder: str
    file_name: str
    source_path: str
    product: str
    amount: float | None
    amount_status: str       # ok | unknown | conflict
    detail_evidence: str
    hit: bool
    skipped_reason: str = ""   # 资格被拒原因（非空=未参与命中）
    # 金额来源：`declared`=文档明写；`derived_qty_x_price`=**由数量×单价推算**
    # （仅当 `CONTRACT_DERIVE_AMOUNT=true` 时可能出现）。
    # **必须一路带到接口层** —— 否则推算值会与文档明写的值长得一模一样，
    # 用户无从分辨「这数字是合同上写的」还是「我们算出来的」。
    amount_source: str | None = None
    # —— 计划 §2 要求合同场景返回的合同级字段（2026-09-11 补）——
    contract_number: str | None = None
    party_a: str | None = None
    party_b: str | None = None
    contract_date: str | None = None
    total_amount: float | None = None
    # —— R6-05 排序层需要（2026-09-13 补）——
    # 计划 R6-05 原文：「结果按**我方响应/最终版、原生文字、混合、扫描 OCR、相关性**排序」。
    # 原实现**没有排序层** —— 结果按 `contract_id` 字典序返回，`_FORMAT_RANK` 只用于方案证据的
    # 同簇择优，**未作用于定位结果集**。缺它 `Success@5/Precision@10` 无定义（见
    # `docs/success5-precision10-clarification.md`）。
    content_format: str | None = None
    document_role: str | None = None


# —— R6-05 排序权重 ——
# ① 角色：需求一只要我方响应材料，`our_response` 与 `final_signed` 同级（都是我方），其余排后。
_ROLE_RANK = {"our_response": 0, "final_signed": 0}
# ② 格式：§3.4「召回优先级固定为 我方响应/最终版 → native_text → mixed → scanned_ocr」。
#    `mixed` 全库目前为 0 条，但**权重位必须留着** —— 一旦数据层产生 mixed，排序不能把它当未知。
_FORMAT_RANK = {"native_text": 0, "native_pdf_text": 0, "native_xlsx_text": 0,
                "mixed": 1, "scanned_ocr": 2}


def locate_sort_key(r: "LocateResult"):
    """R6-05 排序键（确定性，可解释）：角色 → 格式 → 命中 → 金额 → 签订日 → 合同号。

    相关性取「金额大 → 签订日新」：需求一的金额类问法是「X 万以上」，
    金额越大越贴近意图；同金额时新合同更有参考价值。最后用 `contract_id` 兜底，
    保证**同一查询两次运行结果完全一致**（评测可复现的前提）。
    """
    return (
        _ROLE_RANK.get(r.document_role or "", 9),
        _FORMAT_RANK.get(r.content_format or "", 3),
        0 if r.hit else 1,
        -(r.amount if r.amount is not None else -1.0),
        -_date_ord(r.contract_date),
        r.contract_id,
    )


def _date_ord(d: str | None) -> int:
    """日期 → 可比较整数（`YYYYMMDD`），用于「越新越靠前」。空值 → -1（取负后排最后）。

    ⚠️ **不能**直接对日期字符串按位取补来降序：`YYYY-MM`(7 字符) 与 `YYYY-MM-DD`(10 字符)
    长度不同，字符串比较会得到错误顺序。换算成整数就没有这个问题。
    """
    if not d:
        return -1
    digits = "".join(c for c in d if c.isdigit())
    if len(digits) < 6:
        return -1
    return int(digits[:8].ljust(8, "0"))


def _cat_of(product_raw: str | None) -> str:
    p = (product_raw or "").strip()
    return p.split("/", 1)[0] if "/" in p else p


def _row_product_raw(record: dict) -> str:
    """由一条**解析记录**重建它写入 `contract_items.product_raw` 时的文本（`类别/服务名`）。

    必须与 `sync_contract_service_items` 的写入口径一致：
    `((category + "/") if category else "") + service_name`。
    产品匹配要在**这条重建文本**上做子串判断，才能同时覆盖类别与服务名两段。
    """
    cat = record.get("category")
    svc = record.get("service_name") or ""
    return f"{cat}/{svc}" if cat else svc


def _qualification_gate(doc: dict, art_row: dict | None,
                        ctl_row: dict | None) -> tuple[bool, str]:
    """资格门槛：有效角色 + 非待核/暂停 + 明细来源版本与当前有效产物一致。

    doc         documents 行（须含 document_role/parse_status/canonical_document_id）
    art_row     parse_artifacts 行（canonical 产物；含 sha256/parser_version）
    ctl_row     contracts 行（含 source_sha256/parser_version —— 明细提取来源指纹）
    返回 (通过?, 理由)。
    """
    if doc.get("document_role") not in VALID_ROLES:
        return False, f"角色无效:{doc.get('document_role')}"
    if doc.get("parse_status") in BLOCKED_STATUSES:
        return False, f"待复核/暂停:{doc.get('parse_status')}"
    if (doc.get("doc_subtype") or "") in BLOCKED_SUBTYPES:
        return False, f"框架协议/样稿子类:{doc.get('doc_subtype')}，不得作为有效历史合同命中"
    canonical = doc.get("canonical_document_id")
    if not canonical or art_row is None:
        return False, "缺少canonical/产物，明细边界不可用"
    # 内容版本：documents.canonical 与 artifact.sha256 必须一致（否则产物失配）
    if art_row["sha256"] != canonical:
        return False, "canonical 与 artifact.sha256 不一致（内容版本失配）"
    # 明细来源指纹：合同/明细提取时使用的来源 SHA 与 解析版本
    src_sha = ctl_row.get("source_sha256") if ctl_row else None
    src_pv = ctl_row.get("parser_version") if ctl_row else None
    if not src_sha or not src_pv:
        return False, "明细来源指纹缺失（未成功提取同步）；旧记录不得命中"
    if src_sha != canonical or src_pv != art_row["parser_version"]:
        return False, "明细来源SHA/解析版本≠当前产物（明细可能过时）"
    return True, ""


def search_scheme_sections(con, query: str, top_k: int = 10,
                           document_ids: set[str] | None = None) -> list[dict]:
    """方案章节检索（需求一 场景4：以前哪个响应文件写过某方案）。

    往 ES `bid_scheme_sections_v1` 查（R5 建索引，358 条 / 13 份文档）。

    **只用 BM25，不做 kNN** —— 两个理由：
      1. kNN 要把查询文本送 embedding 网关（**外发**）；BM25 全程在本地 ES，不外发。
      2. 实测方案主题词（"售后团队""培训方案""应急预案"）词面明确，cjk 分析器下 BM25 命中稳定。
         语义近义查询（如"交付节奏"→"进度计划"）BM25 会弱，需要时再开 kNN——
         届时须先确认 embedding 外发授权。

    `document_ids`：只在这些文档范围内检索（默认不限，即全部已索引章节）。
    **必须传入 `con`**：ES 与 SQLite 是两份数据，回查真实路径要靠 `con`。
    **不要在这里自己开库**——实测踩过：`config.DB_PATH`（默认 `bid_ai_clean.db` 正式库）
    与 API 用的 `BID_AI_DEMO_DB`（默认测试库）**是两个环境变量**，自己开库会连到空库，
    回查全部落空、静默返回 0 条。由调用方传连接可彻底避免。

    返回 [{section_id, document_id, project_key, heading, text, section_type,
           structural_role, boundary_source, content_format, score}]，
    另附 `file_name`/`source_path`（回查 SQLite 补真实路径，计划 §2 要求返回真实源文件路径）。
    """
    from elasticsearch import Elasticsearch
    import app.config as config

    q = (query or "").strip()
    if not q:
        return []
    es = Elasticsearch(config.ES_URL, request_timeout=30)
    body: dict = {
        "size": max(1, min(top_k, 50)),
        "_source": ["section_id", "document_id", "project_key", "heading", "text",
                    "section_type", "structural_role", "boundary_source", "content_format"],
        "query": {
            "bool": {
                "must": [{"multi_match": {"query": q, "fields": ["heading^3", "text"]}}],
                # 只有 content_section 进方案检索（用户裁定：结构角色排除项不进）
                "filter": [{"term": {"structural_role": "content_section"}}],
            }
        },
    }
    if document_ids:
        body["query"]["bool"]["filter"].append({"terms": {"document_id": sorted(document_ids)}})
    hits = es.search(index=config.ES_INDEX_SCHEME, **body)["hits"]["hits"]

    roots = DEFAULT_ROOTS
    out = []
    for h in hits:
        s = dict(h["_source"])
        s["score"] = round(h["_score"], 4)
        d = con.execute(
            "SELECT relative_path, source_root_id, project_folder, document_role "
            "FROM documents WHERE document_id=?", (s["document_id"],)).fetchone()
        if d is None:
            # 索引里的章节在当前库中找不到对应文档 → 不返回（可追溯性优先）
            continue
        s["file_name"] = d["relative_path"].rsplit("/", 1)[-1]
        s["project_folder"] = d["project_folder"]
        s["document_role"] = d["document_role"]
        s["source_path"] = str(roots.get(d["source_root_id"], Path("")).joinpath(
            *d["relative_path"].split("/")))
        out.append(s)
    return out


def locate_by_product_amount(con, product_keywords: tuple[str, ...], min_amount: float,
                             roots: dict[str, Path] | None = None,
                             approved_document_ids: set[str] | None = None,
                             date_from: str | None = None,
                             date_to: str | None = None,
                             party_include: tuple[str, ...] = (),
                             party_exclude: tuple[str, ...] = (),
                             product_exclude: tuple[str, ...] = (),
                             match_exclude: tuple[str, ...] = ()) -> list[LocateResult]:
    """只读定位：产品词（命中类别）+ 最小金额 [+ 日期区间] → 各已核准有效合同中的结果。

    - 仅遍历 approved_document_ids（默认 APPROVED_DOCUMENT_IDS）对应文档的 CTL 合同。
    - 资格门槛见 _qualification_gate；未通过 → LocateResult(skipped_reason=…) 且 hit=False。
    - hit=True 仅当 amount_status=='ok' 且 amount≥min_amount。

    **日期条件**（date_from / date_to，形如 `YYYY-MM` 或 `YYYY-MM-DD`）：
    与 `contracts.contract_date` 做**字符串比较**（ISO 格式下字典序即时间序）。
    ⚠️ 已知局限：编号年月只有月精度（`YYYY-MM`），当边界精确到**日**时，
    该月内的合同会被**保守排除**（宁可漏、不误纳）——因为无法判定它是否在边界日之后。

    **机构条件**（`party_include` / `party_exclude`，子串匹配）：
    对 `contracts.party_a`（甲方）与 `party_b`（乙方）**同时**判断，命中任一即算。
    - `party_include`：三个字段都不含任一关键词 → 整份合同跳过（不返回，也不记为 excluded）。
    - `party_exclude`：任一字段含关键词 → 整份合同跳过。
    ⚠️ **语义说明**：本接口只检索白名单内的**我方**响应与合同，**没有竞品响应文件可排除**。
    所以「排除华大」实际做的是「排除甲乙方含华大的合同」，**不等于**「排除了竞品的响应材料」
    ——调用方必须把这个区别告知用户（见 api 层的 `filter_note`）。

    **产品排除**（`product_exclude`，子串匹配）：命中的产品类别**不进结果**（用于「不要蛋白组」）。
    """
    roots = roots or DEFAULT_ROOTS
    approved = approved_document_ids if approved_document_ids is not None else APPROVED_DOCUMENT_IDS
    out: list[LocateResult] = []
    for ctl_row in con.execute(
            "SELECT contract_id, document_id, source_sha256, parser_version, contract_date, "
            "       contract_number, party_a, party_b, total_amount FROM contracts "
            "WHERE contract_id LIKE 'CTL-%' ORDER BY contract_id"):
        ctl = dict(ctl_row)
        cdate = (ctl.get("contract_date") or "").strip()
        # 机构过滤：对甲乙方**同时**判断（命中任一即算）。
        # 排除用 continue（整份合同不进结果，也不记为 excluded —— 不是"金额不够"）。
        if party_include or party_exclude:
            parties = (ctl.get("party_a") or "") + "|" + (ctl.get("party_b") or "")
            if party_include and not any(k in parties for k in party_include):
                continue
            if party_exclude and any(k in parties for k in party_exclude):
                continue
        # 合同级字段：每个命中都带上（计划 §2 要求返回编号/甲乙方/日期/总额）
        hdr = {"contract_number": ctl.get("contract_number"), "party_a": ctl.get("party_a"),
               "party_b": ctl.get("party_b"), "contract_date": ctl.get("contract_date") or None,
               "total_amount": ctl.get("total_amount")}
        # 日期过滤：无日期的合同在带日期条件时**不参与**（不猜、不放宽）
        if date_from or date_to:
            if not cdate:
                continue
            if date_from and cdate < date_from:
                continue
            if date_to and cdate > date_to:
                continue
        doc = dict(con.execute("SELECT * FROM documents WHERE document_id=?",
                               (ctl["document_id"],)).fetchone())
        if not doc or not doc["canonical_document_id"]:
            continue
        if not any(doc["document_id"].startswith(pid) for pid in approved):
            continue
        art = con.execute("SELECT text, sha256, parser_version FROM parse_artifacts WHERE canonical_document_id=?",
                          (doc["canonical_document_id"],)).fetchone()
        if not art:
            continue
        ok, reason = _qualification_gate(doc, art, ctl)
        if not ok:
            out.append(LocateResult(
                contract_id=ctl["contract_id"], document_id=doc["document_id"],
                project_folder=doc["project_folder"],
                file_name=doc["relative_path"].rsplit("/", 1)[-1],
                source_path=str(Path(roots.get(doc["source_root_id"], Path(""))).joinpath(*doc["relative_path"].split("/"))),
                product="", amount=None, amount_status="skipped",
                detail_evidence="", hit=False, skipped_reason=reason,
                content_format=doc.get("content_format"),
                document_role=doc.get("document_role"), **hdr))
            continue
        records = parse_contract_service_table(art["text"] or "")
        # 与 sync 侧保持同一口径：无「服务类别」列时用**文件名里的产品**当产品键。
        # 不这样做，这里解析出的 records 会以服务名为键，与 contract_items.product_raw
        # （写入时已注入文件名产品）对不上，明细被全过滤、命中恒为空。
        fp = product_from_filename(doc["relative_path"].rsplit("/", 1)[-1])
        if fp:
            for r in records:
                if not r.get("category"):
                    r["category"] = fp
        # 若该文档 canonical 正文未解析出任何服务明细，则显示为 "dataless" 跳过（不参与命中）
        if not records:
            out.append(LocateResult(
                contract_id=ctl["contract_id"], document_id=doc["document_id"],
                project_folder=doc["project_folder"],
                file_name=doc["relative_path"].rsplit("/", 1)[-1],
                source_path=str(Path(roots.get(doc["source_root_id"], Path(""))).joinpath(*doc["relative_path"].split("/"))),
                product="", amount=None, amount_status="dataless",
                detail_evidence="", hit=False,
                skipped_reason="canonical 正文无服务明细（可能表格格式或表头缺失）", **hdr))
            continue
        # 产品匹配：`product_raw` = 「类别/服务名」，**两段都参与匹配**。
        # 只匹配类别会让「类别通用」的合同（如 `多组学检测（非范本合同）`，实测 98 份里有 30 份）
        # 任何产品查询都查不到 —— 尽管其服务名里有 `LC-MS/MS 精准靶向代谢`。
        # 这是金标准实测漏检的主要来源（见 docs/agent-handoff.md §7.1）。
        #
        # **两条路径分开走，保证零回归**：
        #   按**类别**命中 → 沿用 `product_amount_status`（保留其 conflict 检测，行为与改前完全一致）
        #   按**服务名**命中 → 只对新出现的这批合同生效，金额取**命中明细行的子集和**
        # ⚠️ 服务名路径**绝不能**退化成「按类别把所有明细加总」—— 那等于合同总额、且混着非该产品的服务，
        #    属 D9 明令禁止的「用合同总额替代产品金额」。
        keys: list[tuple[str, str, bool]] = []      # (展示键, 匹配用字符串, 是否按类别命中)
        seen_keys: set[str] = set()
        # 产品排除先做**合同级**判定：只要该合同的任一明细行命中被排除的产品，**整份合同不进结果**。
        # ⚠️ 原实现是**行级**（只跳过含排除词的明细行，合同仍返回）—— 那与金标准语义不符：
        # 冻结查询 G05「代谢合同，**不要蛋白组和宏基因组**」在金标准里把
        # `YOE2024114080`（多组学，同时含代谢/蛋白/宏基因组明细）标为 **irrelevant**，
        # 即要求**整份合同**排除，而非只排除其蛋白明细行。实测行级口径下该合同仍被返回（误返）。
        all_rows = [it["product_raw"] or "" for it in con.execute(
            "SELECT product_raw FROM contract_items WHERE contract_id=? AND row_type='detail'",
            (ctl["contract_id"],))]
        if product_exclude and any(x in raw for raw in all_rows for x in product_exclude):
            continue
        for raw in all_rows:
            cat, _, svc = raw.partition("/")
            # ⚠️ `match_exclude`：正向关键词是**纯子串**匹配，宽词会把整条别的产品线一起吞掉。
            # 实测（2026-09-13 对抗审计）：只写「转录组」会命中「10x Genomics 单细胞转录组测序」
            # 与「空间转录组测序」——查转录组返回 38 份合同里只有 7 份真是转录组（过度率 81.6%）；
            # 只写「靶向」会命中**反义词**「中药非靶向代谢组检测」。
            # 故按**明细行**再挡一道负向词（行级，不是合同级：多组学打包合同里可能有真有假）。
            if match_exclude and any(x in raw for x in match_exclude):
                continue
            if any(k in cat for k in product_keywords):
                key, needle, by_cat = cat, cat, True
            elif svc and any(k in svc for k in product_keywords):
                key, needle, by_cat = svc, svc, False
            else:
                continue
            if key not in seen_keys:
                seen_keys.add(key)
                keys.append((key, needle, by_cat))
        for key, needle, by_cat in keys:
            if by_cat:
                st = product_amount_status(records, product_key=key)
                label = ("unknown" if st["has_unknown"]
                         else "conflict" if st["conflict"]
                         else "ok")
                amount = st["detail_sum"]
            else:
                rows = [r for r in records if r["row_type"] == "detail"
                        and needle in _row_product_raw(r)]
                if not rows:
                    continue
                if any(r.get("line_amount") is None for r in rows):
                    label, amount = "unknown", None
                else:
                    label = "ok"
                    amount = round(sum(r["line_amount"] for r in rows), 2)
            ev = "；".join(
                (r.get("row_text") or "")[:80]
                for r in records
                if r["row_type"] == "detail" and needle in _row_product_raw(r)
            )[:240]
            # 金额来源：该产品是否含**推算**出来的明细行（开关开启时才可能）
            src_kind = ("derived_qty_x_price"
                        if any(r["row_type"] == "detail" and needle in _row_product_raw(r)
                               and r.get("amount_source") == "derived_qty_x_price"
                               for r in records)
                        else "declared")
            hit = (label == "ok" and amount is not None and amount >= min_amount)
            src = str(roots.get(doc["source_root_id"], Path("")).joinpath(*doc["relative_path"].split("/")))
            out.append(LocateResult(
                contract_id=ctl["contract_id"],
                document_id=doc["document_id"],
                project_folder=doc["project_folder"],
                file_name=doc["relative_path"].rsplit("/", 1)[-1],
                source_path=src,
                product=key,
                amount=round(amount, 2) if amount is not None else None,
                amount_status=label,
                detail_evidence=ev,
                hit=hit,
                amount_source=src_kind,
                content_format=doc.get("content_format"),
                document_role=doc.get("document_role"),
                **hdr,
            ))
    # —— R6-05：排序层（2026-09-13 补）——
    # 计划 R6-05 要求「结果按我方响应/最终版、原生文字、混合、扫描 OCR、相关性排序」，
    # 原实现没有这一层（按 `contract_id` 字典序返回）。缺它 Success@5/Precision@10 无定义。
    out.sort(key=locate_sort_key)
    return out