"""需求一 · 历史材料定位的路由（拆分自 `app/api.py`，2026-09-14 APIRouter 整理）。

**monkeypatch 语义说明（为什么辅助函数留在 `app.api`）**：
`tests/` 会对 `app.api.DEMO_DB` / `app.api.APPROVED_DOCUMENT_IDS` 做 monkeypatch。
被调用的辅助函数（`readonly_db` / `_approved_contract_ids` / `live_scope` 等）仍定义在 `app.api`
模块 —— 其 `__globals__` 指向 `app.api`，故这些 patch 继续生效。本模块只是**调用**它们。

⚠️ 路由函数体与拆分前**逐字节相同**（AST 机械切割），只把装饰器 `@app.` 换成 `@router.`。
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api import (BASE_DIR, PRODUCT_ALIASES, PRODUCT_MATCH_EXCLUDE,
                     SearchRequest, _FACT_KW, _FINANCE_FACT_TYPES,
                     _INSTRUMENT_FACT_TYPES, THREE_MODULES, _annotate_fact_role,
                     _approved_contract_ids, _count_material_facts, _fold_by_contract,
                     _MONTHISH, live_scope, parse_demo_query, parse_fact_query,
                     parse_scope_conditions, period_covers, readonly_db,
                     scope_filter_note)
from app.search import DEFAULT_ROOTS as MATERIAL_ROOTS
from app.search import locate_by_product_amount, search_scheme_sections

router = APIRouter()

# `ask` 唯一的意图判断常量（跟随 ask 走；派生自 `_FACT_KW`，后者仍在 app.api 供 parse_fact_query 用）
_ASK_FACT_KW = tuple(k for kws, _t in _FACT_KW for k in kws)
_ASK_SCHEME_KW = ("方案", "预案", "措施", "保密", "培训", "质量控制", "质控",
                  "物流", "风险", "团队", "服务周期", "售后", "项目管理",
                  "对项目的理解", "需求分析", "样本接收")

@router.post("/api/material-search")
def material_search(request: SearchRequest):
    try:
        product, keywords, minimum, date_from, date_to = parse_demo_query(request.query)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with readonly_db() as con:
        scope = live_scope(con)
        party_inc, party_exc, prod_exc_keys, _ = parse_scope_conditions(request.query)
        # 产品排除**必须展开成别名元组**，与包含侧（`product_keywords` 就是别名元组）保持同一口径。
        # 否则拿规范键（如「蛋白组」）去对 `product_raw` 的类别做字面子串匹配：真实类别是
        # 「Olink 蛋白质组（S）」「Pro DIA定量蛋白质组」，都不含子串「蛋白组」（蛋白质组≠蛋白组）
        # → 排除**完全不生效**，而 filter_note 仍宣称已排除（对抗性复核实测）。
        prod_exc = tuple(dict.fromkeys(
            a for key in prod_exc_keys for a in PRODUCT_ALIASES.get(key, (key,))))
        results = locate_by_product_amount(con, keywords, minimum,
                                           date_from=date_from, date_to=date_to,
                                           party_include=party_inc, party_exclude=party_exc,
                                           product_exclude=prod_exc,
                                           match_exclude=PRODUCT_MATCH_EXCLUDE.get(product, ()))
        rows = []
        for result in results:
            item = asdict(result)
            doc = con.execute(
                "SELECT d.document_role, d.parse_status, a.content_format "
                "FROM documents d LEFT JOIN parse_artifacts a "
                "ON a.canonical_document_id=d.canonical_document_id WHERE d.document_id=?",
                (result.document_id,),
            ).fetchone()
            item.update(dict(doc) if doc else {})
            rows.append(item)
    # —— R6-06：同一文件的多条命中**折叠成一个文件结果**，内部业务记录保留在 `records` ——
    # 实测未折叠时「2万元以上的代谢组合同」返回 21 条却只有 17 份合同（同一合同出现 5 次），
    # 用户要在一堆重复卡片里找——而他要的是「哪几份文件可用」。
    hits_folded = _fold_by_contract([r for r in rows if r["hit"]])
    excluded_folded = _fold_by_contract([r for r in rows if not r["hit"]])
    return {
        "parsed": {"product": product, "minimum_amount": minimum, "operator": ">=",
                   "party_include": list(party_inc), "party_exclude": list(party_exc),
                   "product_exclude": list(prod_exc_keys),
                   # 日期条件**必须回传**：原先 parsed 里没有日期字段，前端无从回显，
                   # 用户给的时间条件在界面上完全看不见（评审 P1，踩「不静默丢条件」红线）。
                   "date_from": date_from, "date_to": date_to},
        # `hits` = **按文件折叠后**的结果；每条里的 `records` 是它命中的**全部内部业务记录**。
        "hits": hits_folded,
        "excluded": excluded_folded,
        "hit_records": len([r for r in rows if r["hit"]]),
        "excluded_records": len([r for r in rows if not r["hit"]]),
        # 说明里用**规范键**（用户说的词），不用展开后的别名串 —— 别名是匹配手段，不是用户语言
        "filter_note": scope_filter_note(party_inc, party_exc, prod_exc_keys),
        "scope_note": f"注意：本页目前覆盖 {scope['queryable_contracts']} 份已核合同。"
                      f"这里查不到 ≠ 公司没有，实际库里的量远不止这些。",
    }




@router.get("/api/ask")
def ask(q: str = ""):
    """**需求一唯一入口**：一句自然语言 → 自动判断该查哪一类，再转对应端点。

    四类问法（计划 §2 核心场景）与判据：

    | 问法 | 例 | 判据 | 转发到 |
    |---|---|---|---|
    | ① 合同 | 「2024年12月之后，代谢组服务金额40万元以上的合同」 | 无材料/方案词 | `/api/material-search` |
    | ② 财务社保 | 「哪些响应文件包含2025年的社保」 | 社保/财务/完税/纳税 | `/api/material-facts` |
    | ③ 仪器设备 | 「哪些文件列了仪器照片、采购合同、发票」 | 仪器/设备/发票/采购合同/照片 | `/api/material-facts` |
    | ④ 方案章节 | 「以前哪个文件写过售后团队、培训方案」 | 方案/预案/团队/质控/物流… | `/api/scheme-search` |

    **判据顺序是 材料 → 方案 → 合同**：「仪器采购合同」含「合同」但用户问的是
    「有没有采购合同这份材料」，应走材料；而「代谢组合同」不含材料词，落回合同。
    响应体统一带 `kind` 字段，调用方据此渲染。
    """
    text = (q or "").strip()
    if not text:
        raise HTTPException(400, "请输入查询，例如：2024年12月以后，代谢组金额2万元以上的合同")
    if any(k in text for k in _ASK_FACT_KW):
        body = material_facts(q=text, fact_type="", fact_value="", limit=500)
        return {"kind": "fact", "query": text, **body}
    if any(k in text for k in _ASK_SCHEME_KW):
        body = scheme_search(q=text, limit=8)
        return {"kind": "scheme", "query": text, **body}
    return {"kind": "contract", "query": text, **material_search(SearchRequest(query=text))}




@router.get("/api/material-facts")
def material_facts(fact_type: str = "", fact_value: str = "", q: str = "", limit: int = 50):
    """场景2/3：查询某份响应文件是否包含某类材料（财务社保月份／仪器／资质／发票…）。

    数据来自 `material_facts`（由「文字清单 + LLM 语义分类」产出，见
    docs/material-facts-feasibility.md 与 docs/llm-classification-authorization.md）。

    两种用法：
      - `?q=找含2025年12月社保的资料`  —— 自然问句，内部粗映射（**推荐**）
      - `?fact_type=social_security_month&fact_value=2025-12` —— 精确参数

    **边界说明（必须随结果返回）**：本接口回答「**有没有**」，不回答「材料内容是什么」——
    结构化信息取自响应文件正文里的文字清单，下方扫描件**未做 OCR**。
    无 `fact_value` 表示该条目本身不含期间（如证书名），**不是缺失**。
    """
    if q and not fact_type:
        fact_type, auto_value = parse_fact_query(q)
        fact_value = fact_value or auto_value
    con = readonly_db()
    # WHERE 单独拼，供「取数」与「数总数」两条查询共用（参数也共用）
    where = " WHERE 1=1"
    args: list = []
    if fact_type:
        where += " AND f.fact_type = ?"
        args.append(fact_type)
    if fact_value:
        where += " AND f.fact_value LIKE ?"
        args.append(f"%{fact_value}%")
    cap = max(1, min(limit, 500))
    _SELECT = ("SELECT f.fact_type, f.fact_value, f.evidence_text, d.project_folder, "
               "d.relative_path, d.source_root_id, d.document_role FROM material_facts f "
               "JOIN documents d ON d.document_id = f.document_id")
    _FROM = " FROM material_facts f JOIN documents d ON d.document_id = f.document_id"
    try:
        # ⚠️ 总数必须单独查：`LIMIT 50` 会把结果**静默截断**，而页面上看不出还有更多
        # （实测「社保」库内 1,182 条，默认只回 50 条且无任何提示 —— 与三模块端点同一类问题）。
        # 必须在 `finally: con.close()` **之前**算，否则用已关闭的连接会 500（那个坑踩过一次）。
        total_available = con.execute("SELECT COUNT(*)" + _FROM + where, args).fetchone()[0]
        rows = [dict(r) for r in con.execute(
            _SELECT + where + " ORDER BY d.relative_path LIMIT ?", args + [cap])]
        truncated = len(rows) < total_available
        # 「起始/区间式覆盖」单列一组：只在查询值精确到年/月时才做
        related: list[dict] = []
        if fact_value and _MONTHISH.match(fact_value.strip()):
            rq = ("SELECT f.fact_type, f.fact_value, f.evidence_text, d.project_folder, "
                  "d.relative_path, d.source_root_id, d.document_role FROM material_facts f "
                  "JOIN documents d ON d.document_id = f.document_id "
                  "WHERE f.fact_value LIKE '%~%'")
            rargs: list = []
            if fact_type:
                rq += " AND f.fact_type = ?"
                rargs.append(fact_type)
            rq += " ORDER BY d.relative_path LIMIT ?"
            rargs.append(max(1, min(limit, 500)))
            exact = {(x["relative_path"], x["evidence_text"]) for x in rows}
            related = [dict(r) for r in con.execute(rq, rargs)
                       if period_covers(fact_value, r["fact_value"])
                       and (r["relative_path"], r["evidence_text"]) not in exact]
    finally:
        con.close()
    for r in rows + related:
        r["source_path"] = str(
            MATERIAL_ROOTS.get(r.pop("source_root_id"), Path("")).joinpath(
                *r["relative_path"].split("/"))) if r.get("relative_path") else ""
        r["file_name"] = r["relative_path"].rsplit("/", 1)[-1]
        _annotate_fact_role(r)
    return {
        "count": len(rows),
        "facts": rows,
        "total_available": total_available,
        "truncated": truncated,
        "related_count": len(related),
        "related": related,
        "scope_note": "本页只回答「有没有这类材料」，材料内容取自响应文件正文里的文字清单；"
                      "下方扫描件未做 OCR。条目无期间值表示其本身不含期间，不是缺失。"
                      + (f"⚠️ 库内共 {total_available} 条，本次显示前 {len(rows)} 条（已截断）—— "
                         "**没显示出来的不代表没有**。" if truncated else "")
                      + ("`related` 里的条目只记录了**起始期间**（如「自2021年起缴纳」），"
                         "历史材料未记终期，因此**只能说明至该时点可能仍有效**，"
                         "不能断言该月一定有材料 —— 请人工核对源文件。"
                         if related else ""),
    }




@router.get("/api/three-modules")
def three_modules(module: str = "", q: str = "", limit: int = 100):
    """**三模块定位**：项目业绩 / 财务社保数据 / 仪器设备清单。

    用户 2026-09-13 明确只要这三类，括号内是必须识别的源信息：

      · 项目业绩     —— 哪个产品、合同金额、合同签订日期、**是否有收款凭证**
      · 财务社保数据 —— **包含什么月份**
      · 仪器设备清单 —— 哪些仪器、是否有采购合同、发票、仪器照片

    `?module=项目业绩` 只返回该类；不传则返回三类的**概览**（各自覆盖多少份/多少条）。
    """
    import json

    con = readonly_db()
    try:
        if not module:
            out = {}
            for name, spec in THREE_MODULES.items():
                if name == "项目业绩":
                    # 与侧栏「已完成核对、可以查询」同一口径（白名单），不是库里 CTL 合同总数
                    n = len(_approved_contract_ids(con))
                    out[name] = {**spec, "count": n, "unit": "份合同"}
                elif name == "财务社保数据":
                    out[name] = {**spec, "count": _count_material_facts(con, _FINANCE_FACT_TYPES),
                                 "unit": "条材料事实"}
                else:
                    out[name] = {**spec, "count": _count_material_facts(con, _INSTRUMENT_FACT_TYPES),
                                 "unit": "条材料事实"}
            return {"modules": out,
                    "scope_note": "这是需求一的**三类定位**。材料事实来自已解析的文件正文，"
                                  "未解析的扫描件不在内；缺项如实不返回，不猜。"
                                  "卡上的数字与点进去的条数**同源**（点进去可能因 limit 截断，"
                                  "截断会在页面上写明）。"}

        if module not in THREE_MODULES:
            raise HTTPException(400, f"未知模块：{module}；可选：{list(THREE_MODULES)}")

        if module == "项目业绩":
            # ⚠️ 白名单过滤必须发生在 LIMIT **之前**。原实现若先 `LIMIT n` 再想过滤，
            # 一旦库里 CTL 合同超过这个 n，卡上的数（白名单口径）与明细分叉 —— 同一类缺陷复发。
            # 现状 98 份合同，全取后过滤再截断，代价可忽略。
            approved_ids = _approved_contract_ids(con)
            cap = max(1, min(limit, 500))
            rows = []
            for c in con.execute(
                    """SELECT c.contract_id, c.contract_number, c.party_a, c.party_b, c.contract_date,
                              c.total_amount, c.document_id, d.relative_path, d.source_root_id,
                              d.project_folder, d.content_format
                       FROM contracts c JOIN documents d ON d.document_id=c.document_id
                       WHERE c.contract_id LIKE 'CTL-%' ORDER BY c.contract_date DESC"""):
                if c["contract_id"] not in approved_ids:
                    continue
                if len(rows) >= cap:
                    break
                rec = dict(c)
                # 产品明细（哪个产品 + 该产品金额）。列名是 `line_amount` / `product_amount_source`
                # （**不是** amount / amount_status —— 写错会 500，已踩过）；`---` 是解析噪声行，剔除。
                #
                # ⚠️ 按产品**聚合**，不是逐行返回：计划 §5.3/D9 要求「产品金额 = 该合同里同产品
                # detail 行 line_amount 之和」。原实现逐行返回且带 `LIMIT 20` 静默截断
                # （实测 YOE2026050397 库内 42 行 → API 只回 20 条，下游按返回求和会算少）。
                # 现按 product_raw 分组求和，并回传 `detail_rows` / `amount_known`，
                # 使「金额未确认」（全部行金额为空）与「金额为 0」（文档明写 0.00 的赠送行）
                # 在数据上可区分 —— 二者不可混为一谈。
                agg: dict[str, dict] = {}
                for i in con.execute(
                        "SELECT product_raw, product_canonical, line_amount, product_amount_source "
                        "FROM contract_items "
                        "WHERE contract_id=? AND row_type='detail' AND product_raw IS NOT NULL "
                        "AND product_raw != '---' AND product_raw != ''",
                        (rec["contract_id"],)):
                    key = i["product_raw"]
                    slot = agg.setdefault(key, {
                        "product": key, "canonical": i["product_canonical"],
                        "amount": None, "amount_source": i["product_amount_source"],
                        "detail_rows": 0, "amount_known": False, "zero_amount_rows": 0,
                        "derived": False})
                    slot["detail_rows"] += 1
                    if i["line_amount"] is not None:
                        slot["amount"] = (slot["amount"] or 0.0) + i["line_amount"]
                        slot["amount_known"] = True
                        if i["line_amount"] == 0:
                            slot["zero_amount_rows"] += 1
                    if i["product_amount_source"]:
                        slot["amount_source"] = i["product_amount_source"]
                        slot["derived"] = slot["derived"] or (
                            i["product_amount_source"] == "derived_qty_x_price")
                rec["products"] = sorted(
                    agg.values(), key=lambda x: (x["amount"] is None, -(x["amount"] or 0.0)))
                rec["product_count"] = len(rec["products"])
                rows.append(rec)
            # —— 产品族汇总：直接回答「哪个产品」——
            # `product_canonical` 由 `scripts/canonicalize_products.py` 归一（411 个原始串 → 58 个族）。
            # ⚠️ 归一**不合并不同产品**：`10x Genomics 单细胞转录组测序` 与 `…空间转录组测序` 是两个族，
            # 因为「10x Genomics」是**平台**不是产品 —— 曾写成整串替换，把两者并成了一个（已修）。
            fam: dict[str, dict] = {}
            for c in rows:
                for p in c["products"]:
                    k = p["canonical"] or p["product"]
                    f = fam.setdefault(k, {"canonical": k, "contracts": 0, "amount": 0.0,
                                           "amount_known": False})
                    f["contracts"] += 1
                    if p["amount_known"]:
                        f["amount"] += p["amount"] or 0.0
                        f["amount_known"] = True
            product_summary = sorted(fam.values(), key=lambda x: -x["contracts"])
            # 收款凭证：读台账（如实标注「未到款」/「未记」）
            pay = {}
            try:
                pv = json.loads((BASE_DIR / "data" / "payment_vouchers.json").read_text(encoding="utf-8"))
                for r in pv.get("records", []):
                    key = (r.get("matched_contract_number") or r["contract_number"]).upper()
                    pay[key] = {"has_payment": r["has_payment"], "pay_time": r.get("pay_time") or "",
                                "invoice_no": r.get("invoice_no") or "", "payee": r.get("payee") or ""}
            except Exception:  # noqa: BLE001 —— 台账缺失不影响合同定位
                pay = {}
            # 银行回单：读 `银行回单.zip` 的**中央目录**映射（只读文件名，未核内容）。
            # 这是一直存在但从没被读的证据源 —— 台账属**他方主体**且只覆盖 2 份，
            # 而本通道实测能对上 5 份 CTL 合同。**未 OCR，故不得当作「已到账」**。
            receipts: dict[str, list] = {}
            try:
                pr = json.loads((BASE_DIR / "data" / "payment_receipts.json").read_text(encoding="utf-8"))
                for r in pr.get("records", []):
                    hit = r.get("matched_contract_number")
                    if hit:
                        receipts.setdefault(hit.upper(), []).append(
                            {"inner_path": r["inner_path"], "size": r.get("size"),
                             "source_zip": r.get("source_zip"), "contract_number": r["contract_number"]})
            except Exception:  # noqa: BLE001 —— 回单映射缺失不影响合同定位
                receipts = {}
            for r in rows:
                key = (r["contract_number"] or "").upper()
                info = pay.get(key)
                r["receipt_files"] = receipts.get(key, [])
                r["receipt_count"] = len(r["receipt_files"])
                # ⚠️ 状态必须分清，否则会把「没找到证据」说成「没收钱」：
                #   yes           —— 台账明确记载有付款时间
                #   no            —— 台账明确写「未到款」
                #   unknown       —— 台账有该合同，但付款时间与收款人两栏都没写
                #   ledger_absent —— 台账里没有该合同号（**据台账无法判断**，不是"没收到钱"）
                #   receipt_file  —— 台账没覆盖，但**存在以该合同号命名的银行回单图片**（未核内容）
                # 台账优先；台账没覆盖时才看回单文件。
                if info is None and r["receipt_count"]:
                    r["payment_status"] = "receipt_file"
                else:
                    r["payment_status"] = "ledger_absent" if info is None else info["has_payment"]
                r["has_payment_voucher"] = (None if info is None else info["has_payment"])
                r["payment_detail"] = info or {}
                r["file_name"] = r["relative_path"].rsplit("/", 1)[-1]
                r["source_path"] = str(
                    MATERIAL_ROOTS.get(r.pop("source_root_id"), Path("")).joinpath(
                        *r["relative_path"].split("/")))
            n_receipt = sum(1 for r in rows if r["payment_status"] == "receipt_file")
            return {"module": module, "spec": THREE_MODULES[module], "count": len(rows),
                    "records": rows, "product_summary": product_summary,
                    "scope_note": f"本次只列**已完成核对、可查询**的 {len(rows)} 份合同"
                                  "（与查询路径同一批文件；库内尚未核对的合同不在内，"
                                  "数据总览里另有说明）。"
                                  "合同金额为**合同总额**；产品级金额见每条 products（可能小于总额）。"
                                  "`payment_status` **四种状态不得压成布尔**："
                                  "yes=台账记载有付款时间；no=台账明确「未到款」；"
                                  "unknown=台账有该合同但未记付款；"
                                  "**ledger_absent=该合同不在收款凭证台账里**"
                                  "（不等于没收钱，只是这批台账没覆盖它）；"
                                  "**receipt_file=存在以该合同号命名的银行回单图片**"
                                  "（在 `银行回单.zip` 内，**只读了文件名、未核内容**，"
                                  "故**不等于已到账**，需人工看图或 OCR 才能断言）。"
                                  f"本次台账覆盖 {len(pay)} 个合同号；"
                                  f"回单文件通道另覆盖 {n_receipt} 份合同（见每条 `receipt_files`）。"}

        # 财务社保 / 仪器设备：都走 material_facts。类型表**引用上面那两个常量**，
        # 与概览卡同源（分开写死过一次，结果卡上 546、点进去 722）。
        types = (_FINANCE_FACT_TYPES if module == "财务社保数据" else _INSTRUMENT_FACT_TYPES)
        rows = [dict(r) for r in con.execute(
            f"""SELECT f.fact_type, f.fact_value, f.evidence_text, d.relative_path,
                       d.document_role, d.source_root_id, d.project_folder, d.content_format,
                       d.document_id
                FROM material_facts f JOIN documents d ON d.document_id=f.document_id
                WHERE f.fact_type IN ({','.join('?' * len(types))})
                ORDER BY f.fact_type, f.fact_value LIMIT ?""",
            (*types, max(1, min(limit, 1000))))]
        # ⚠️ 分类计数**必须**回传：`ORDER BY fact_type, fact_value LIMIT ?` 会让排在后头的类别
        # 被**整类截掉**。实测 `instrument_purchase_contract` 库内 44 条，limit=400 时返回 0 条
        # —— 看起来像「这一类没有产出」，**静默截断读起来像「已覆盖全部」**，必须显式暴露。
        # 注意：必须在 `finally: con.close()` **之前**算，否则用已关闭的连接会 500（已踩过）。
        type_counts: dict[str, int] = {
            t: con.execute("SELECT COUNT(*) FROM material_facts WHERE fact_type=?",
                           (t,)).fetchone()[0] for t in types}
    finally:
        con.close()
    for r in rows:
        r["source_path"] = str(
            MATERIAL_ROOTS.get(r.pop("source_root_id"), Path("")).joinpath(
                *r["relative_path"].split("/")))
        r["file_name"] = r["relative_path"].rsplit("/", 1)[-1]
        # ⚠️ 角色分层**必须**回传。需求一的问法是「某份**响应文件**是否包含…」，
        # 而 material_facts 的来源角色混杂：实测「财务社保数据」254 条里只有 58 条来自
        # our_response/final_signed，另有 9 条来自 `tender_requirement`（**那是采购人的要求，
        # 不是我方已附材料**），其余来自资质附件 / 过程材料 / 待复核。
        # 不区分就会把「招标文件要求交社保」说成「我方响应文件里有社保」——事实性误导。
        r["role_scope"] = ("our" if r["document_role"] in ("our_response", "final_signed")
                           else "tender" if r["document_role"] == "tender_requirement"
                           else "other")
        r["role_label"] = {"our": "我方响应/最终版",
                           "tender": "招标要求（采购人要求，非我方已附）",
                           "other": "其它来源文件"}[r["role_scope"]]
    # ⚠️ type_counts 已在上面（连接关闭前）算好
    total_available = sum(type_counts.values())
    truncated = len(rows) < total_available
    our_n = sum(1 for r in rows if r["role_scope"] == "our")
    tender_n = sum(1 for r in rows if r["role_scope"] == "tender")
    return {"module": module, "spec": THREE_MODULES[module], "count": len(rows), "records": rows,
            "type_counts": type_counts, "total_available": total_available, "truncated": truncated,
            "our_count": our_n, "tender_count": tender_n, "other_count": len(rows) - our_n - tender_n,
            "scope_note": "材料事实取自**已解析文件**的正文与文件名；未解析的扫描件不在内。"
                          "缺项如实不返回，不猜。"
                          f"本页 {len(rows)} 条里，**{our_n} 条来自我方响应/最终版文件**"
                          f"（这是需求一问的「响应文件里有没有」），"
                          f"{tender_n} 条来自**招标要求**（采购人要求交的材料，"
                          "**不等于我方已附**），"
                          f"另有 {len(rows) - our_n - tender_n} 条来自资质附件/过程材料/待复核；"
                          "每条都带 `role_label` 可逐条分辨。"
                          + (f"⚠️ 本次因 limit 截断：库内共 {total_available} 条，"
                             f"各类别存量见 `type_counts` —— **未出现的类别不代表没有**。"
                             if truncated else "")}




@router.get("/api/scheme-search")
def scheme_search(q: str = "", limit: int = 8):
    """场景4：以前哪个响应文件写过某方案（售后团队／培训方案／应急预案…）。

    走 ES `bid_scheme_sections_v1`（R5 建索引）。**只用 BM25，不外发**——
    kNN 需把查询送 embedding 网关，词面明确的方案主题用 BM25 已够；
    语义近义查询（如"交付节奏"→"进度计划"）BM25 会弱，需要时再开 kNN。

    只返回 `structural_role=content_section` 的章节（结构排除项不进方案检索）。
    """
    text = (q or "").strip()
    if not text:
        raise HTTPException(400, "请输入方案主题，例如：售后团队 服务周期 培训方案")
    with readonly_db() as con:
        rows = search_scheme_sections(con, text, top_k=max(1, min(limit, 30)))
    # 正文按需要截断，避免一次返回几十万字
    for r in rows:
        r["text_excerpt"] = (r.get("text") or "")[:600]
        r.pop("text", None)
    return {
        "count": len(rows),
        "query": text,
        "sections": rows,
        "scope_note": f"当前方案索引覆盖 {len({r['document_id'] for r in rows})} 份文档的命中章节；"
                      "索引只含我方响应/最终版，已排除目录/封面/页眉等结构项。"
                      "本接口按词面检索，不做语义改写。",
    }



