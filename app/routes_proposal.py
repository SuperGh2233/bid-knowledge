"""需求二 · 模块级方案生成的路由（拆分自 `app/api.py`，2026-09-14 APIRouter 整理）。

**产品近况**：2026-09-14 用户指令 —— 需求二只保留**模型起草**；`mode=local` 已下线
（`assemble_proposal` 实现仍在 `app/proposal.py` 供单测与回退，但不再从 API 暴露）。

**monkeypatch 语义**：`readonly_db` 等辅助仍定义在 `app.api`（其 `__globals__` 指向 `app.api`），
`tests/` 对 `app.api.DEMO_DB` 的 patch 继续生效。本模块只是调用它们。

⚠️ 函数体与拆分前**逐字节相同**（AST 机械切割），只把装饰器 `@app.` 换成 `@router.`。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api import MODE_DEPRECATED_MSG, readonly_db

router = APIRouter()


class GenerateRequest(BaseModel):
    query: str = ""
    modules: str = ""
    constraints: str = ""
    # 产品线（2026-09-15 加，见 PLAN-20260915 §8-3）：**可选**，缺省时从 `query` 自动识别
    # （`app.api.detect_product`，与检索侧同一套别名口径）。用于让模型**只保留与该产品线相关**的条目
    # （典型：证据里并列「RNA/DNA/单细胞项目」的异常处理时，只写本次产品线那一条）。
    product: str = ""
    # 需求二只保留**模型起草**（2026-09-14 用户指令）；`mode=local`（本地抽取式装配）
    # 已从产品入口下线 —— API 不再接受，历史调用方会收到明确 400。
    mode: str = "llm"
    # **输出标题结构**（2026-09-17 加，需求方第二次对接的需求2）：
    # 用户指定「大标题 + 固定小标题清单」，模型必须**逐字**按它输出。
    # 接受两种形态：`{"title": "售后解决方案", "sections": ["售后服务团队", ...]}`
    #   或一份 Markdown 文本（`# 大标题` + `## 小标题`…）。
    # **可选**：不传 = 不约束，行为与加此参数前逐字节一致。
    # ⚠️ 只约束**输出结构**，不改变证据召回（召回仍按 `MODULE_KEYWORDS` 的模块名走）。
    # 外发面不变：这是**用户自己敲的标题文本**，无正文，仍在既有生成授权范围内。
    outline: dict | str | None = None


# MODE_DEPRECATED_MSG 定义在 `app.api`（权威一份），本模块从顶部 import —— 不在此重复。


@router.get("/api/modules")
def modules():
    """**方案模块**的词表入口（给页面做「点一下填进输入框」）。

    词表**就是生成用的那一份**（`app/proposal.py::MODULE_KEYWORDS` ← `app/module_keywords.json`）——
    前端不另抄一份，抄了就会像「仪器设备 546 vs 722」那样两处分叉。

    **不碰 ES、不碰库**：章节索引暂时不可达时这个入口照样能渲染（真去生成时才报 ES 的错）。
    """
    from app.proposal import MODULE_KEYWORDS

    return {"count": len(MODULE_KEYWORDS),
            "modules": [{"module": m, "keywords": list(k)} for m, k in MODULE_KEYWORDS.items()],
            "scope_note": "方案模块的定义来自 app/module_keywords.json（与生成用的是同一份词表）。"
                          "点模块名只是把名字填进输入框，**不会自动生成**。"}


@router.get("/api/module-kb")
def module_kb(modules: str = ""):
    """**模块化经验**：把历史响应文件按方案模块整理的「我们历史上写过什么」。
    供业务同事**直接查看/复用**，也是 `mode=llm` 生成时提示词里的结构与口径那一段。

    与 `/api/proposal-generate` 的分工：这里给的是**跨项目归纳**（常用小节、承诺时限），
    给的是**这一次可引用的 3~5 段原文**。

    ⚠️ **零外发**：整理全程在本地（ES 召回 + 确定性归纳），不调用任何模型。
    ⚠️ `commitments` 里 `conflict=true` 表示**历史材料自身不一致** —— 如实并列，**不自动择一**。
    """
    from app.proposal import MODULE_KEYWORDS, build_module_kb

    mods = [m.strip() for m in modules.split(",") if m.strip()] or list(MODULE_KEYWORDS)
    unknown = [m for m in mods if m not in MODULE_KEYWORDS]
    if unknown:
        raise HTTPException(400, f"未知方案小节：{unknown}；可选：{list(MODULE_KEYWORDS)}")
    try:
        with readonly_db() as con:
            kb = build_module_kb(con, mods)
    except Exception as exc:  # noqa: BLE001 —— ES 不可达时如实报错，不静默返回空
        raise HTTPException(503, f"模块化整理失败（方案章节索引不可达？）：{str(exc)[:160]}") from exc
    return {
        "count": len(kb), "modules": kb,
        "scope_note": "这是**跨项目归纳**出来的模块化经验：常用小节标题、承诺时限槽位、"
                      "去重后的要点。`conflict=true` 的条目说明**历史材料自身不一致**，"
                      "系统只并列不择一，请人工确认。归纳**零外发**，全程在本地完成。",
    }


@router.post("/api/proposal-generate")
def proposal_generate(request: GenerateRequest):
    """**需求二 R7：模块级方案生成** —— 目前只有**模型起草**一条路径。

    - 走模型生成。**会把我方响应正文送往外发网关**，默认关闭（`PROPOSAL_GEN_ENABLED`）；
      未开启时返回 403 且**不会有任何外发**（授权记录见 `docs/authorizations/llm-generation-authorization.md`）。

    本地抽取式装配（`mode=local`）已于 2026-09-14 **下线**（用户指令只保留模型生成）；
    `assemble_proposal` 实现仍在 `app/proposal.py`（含单元测试），但不再从 API 暴露。

    产物附 `validation`（编造引用 / 缺小节 / 无法回溯的数字）、
    `warnings`（数值冲突，**不自动择一**）、`gaps`（证据不足的小节）。
    """
    from app.proposal import (MODULE_KEYWORDS, ProposalGenError, ProposalGenNotAuthorized,
                              unrecognized_requirements,
                              build_evidence_packs, build_module_kb,
                              extract_required_sections, generate_proposal, packs_to_payload,
                              parse_outline, plan_sections)

    if request.mode != "llm":
        raise HTTPException(400, MODE_DEPRECATED_MSG)
    # —— 结构规划（2026-09-18 需求方：「要做意图识别…大标题/小标题…不要输出多余的内容」）——
    # 优先级：显式 `modules` > 从 `query` 推的结构 > 老路径 `extract_required_sections`。
    # `plan_sections` 是**零外发**的本地解析。
    explicit = [m.strip() for m in request.modules.split(",") if m.strip()]
    plan = {} if explicit else plan_sections(request.query)
    section_map: dict[str, tuple[str, tuple[str, ...]]] = {}
    if explicit:
        mods = explicit
    elif plan.get("sections"):
        # 小节名用**用户措辞**（`服务周期`），归属模块另存 —— 召回按归属模块走。
        mods = [x["name"] for x in plan["sections"]]
        section_map = {x["name"]: (x["module"], tuple(x["terms"])) for x in plan["sections"]}
    else:
        mods = extract_required_sections(request.query)
    unknown = [m for m in mods if m not in MODULE_KEYWORDS and m not in section_map]
    if unknown:
        raise HTTPException(400, f"未知方案小节：{unknown}；可选：{list(MODULE_KEYWORDS)}")
    if not mods:
        if request.query.strip():
            raise HTTPException(400, "没能从这句话里认出任何方案小节，因此**没有执行生成**"
                                     "（不是生成失败）。请改用系统认得的写法，例如："
                                     "「售后服务方案，必须包含服务周期和应急预案」。")
        raise HTTPException(400, "请指定方案小节，例如："
                                 "{\"query\": \"售后服务方案，必须包含服务周期\"}")
    with readonly_db() as con:
        packs = build_evidence_packs(con, mods, section_map=section_map or None)
        # 「模块化经验」（历史跨项目归纳）—— **零外发**的本地整理，随提示词发给模型。
        # ES 不可达时降级为 None（生成仍可跑，只是少了结构与口径那一段），**不因此 500**。
        # ⚠️ 按**归属模块**取经验（`服务周期` 这类用户措辞在模块表里查不到）。
        kb = build_module_kb(con, list(dict.fromkeys(
            [section_map[m][0] if m in section_map else m for m in mods])))
    payload = packs_to_payload(packs)
    # 产品线：显式传入优先；否则从 `query`/`modules` 自动识别（**与检索侧同一套别名口径**）。
    # 识别不出 → 空串 → 提示词不加裁剪规则（不猜、不误裁）。
    from app.api import detect_product
    product = request.product.strip() or detect_product(f"{request.query} {request.modules}")
    # 标题结构：`request.outline`（用户手填）> **从 query 推的**（需求方 2026-09-18 要求）。
    # 都拿不到 → 空 dict → **不施加约束**（不猜、不误裁结构）。
    user_outline = parse_outline(request.outline)
    auto_outline = ({} if user_outline else
                    {"title": plan.get("title", ""),
                     "sections": [x["name"] for x in plan.get("sections", [])]})
    outline = user_outline or auto_outline
    if not (outline.get("title") or outline.get("sections")):
        outline = {}
    try:
        result = generate_proposal(payload, request.constraints, kb,
                                   product=product, outline=outline)
    except ProposalGenNotAuthorized as exc:
        raise HTTPException(403, str(exc)) from exc
    except ProposalGenError as exc:
        raise HTTPException(502, str(exc)) from exc
    result["modules"] = payload["modules"]
    result["coverage"] = payload["coverage"]
    result["kb_used"] = bool(kb)
    result["outline_used"] = outline or None
    # **结构来源如实回显**（2026-09-18）：用户要能看到「大标题/小标题是谁定的」——
    # `user` = 手填的文本框；`query` = 系统从他那句话里推出来的（含逐条归属与推断标记）；
    # 无 == 没施加结构约束（按模块自由成节）。
    result["outline_source"] = ("user" if user_outline else
                                "query" if auto_outline else "none")
    if plan:
        # ⚠️ **归属模块是系统推的**（`module_inferred`）时必须让用户看得见 ——
        # 「服务周期」在模块词表里一个字都不命中，它被归到「售后方案」是系统推的，不是用户指定的。
        result["sections_planned"] = [
            {"name": x["name"], "module": x["module"],
             **({"module_inferred": True} if x.get("module_inferred") else {})}
            for x in plan.get("sections", [])]
        result["title_planned"] = plan.get("title", "")
    # ⚠️ 用户**点名要求**、但没被纳入结构的 → 如实告警，不静默丢（项目红线）。
    # ⚠️ 走 `plan["dropped"]`（**按切分后的小标题**逐个核对）而不是 `unrecognized_requirements`：
    # 后者拿 `_REQ_CLAUSE` 抓到的**整片段**（`服务周期和应急预案`）去判，片段里任一模块命中
    # 就算「已认出」→ 片段里的「服务周期」被**连带跳过、一条告警都没有**（2026-09-18 实测）。
    _missed = plan.get("dropped", []) if plan else \
        unrecognized_requirements(request.query or request.modules or "", mods)
    if _missed:
        result.setdefault("warnings", []).append({
            "module": "（未识别的小节）", "slot": "要求未生效",
            "values": _missed, "sources": {},
            "message": f"你这句里点名要包含的内容有 {len(_missed)} 项**没被识别成方案小节**："
                       f"{'、'.join(_missed)} —— 它们**没有被纳入本次生成**。"
                       f"请改用系统认得的写法（如「质量控制方案」而不是「质控要求」），"
                       f"或忽略本条以确认接受不带这段。",
        })
    result["scope_note"] = (
        "这是**草稿**，不是最终稿：每个关键数字都带引用编号，可回到源文件核对；"
        "校验结果里列出的问题（编造引用／缺小节／数字无法回溯）必须逐条处理；"
        "标为证据不足的小节，正文里应写明材料不足，请勿直接采用。")
    return result


@router.post("/api/tender-check")
async def tender_check(request: Request):
    """**招标要求核对**：把一份**新**招标文件里的服务时限要求逐条抽出来，
    对照历史响应文件里的同类承诺，标出「有先例 / 比历史都严 / 历史没覆盖」。

    **为什么这么做**（2026-09-14 实测）：响应文件里的服务时限**不是各项目自己拍的** ——
    招标文件写了时限的 **56% 被逐字照抄**进响应文件、38% 同类改写。
    即依据是**该项目的招标文件**；历史材料的作用是当模板与底账，**不是答案来源**。

    **两种输入都收**（手动读 body，不用 FastAPI 的 `File`/`Form` 声明 ——
    那会**强制要求 multipart**，纯 JSON 调用方会直接 400）：
      · `multipart/form-data` 带 `file`（.docx/.pdf/.xlsx/.txt）或 `text`；
      · `application/json` 带 `{"text": "..."}`。

    ⚠️ **上传的文件不落库、不写 NAS**：读进内存解析一次即弃；解析全程本地（零外发）。
    """
    from app.parser import UnsupportedNative, _compute_native
    from app.proposal import MODULE_KEYWORDS, build_module_kb
    from app.tender import (build_check_table, compare_with_history,
                            extract_time_requirements, summarize)

    ctype = (request.headers.get("content-type") or "").lower()
    body = ""
    src_name = "（粘贴的文本）"
    if "multipart/form-data" in ctype:
        form = await request.form()
        text = str(form.get("text") or "")
        up = form.get("file")
        if up is not None and getattr(up, "filename", ""):
            raw = await up.read()
            src_name = up.filename
            suffix = Path(src_name).suffix.lower()
            if suffix in (".txt", ".md"):
                body = raw.decode("utf-8", errors="replace")
            else:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                    tf.write(raw)
                    tmp_path = tf.name
                try:
                    body, _c, _m, _f = _compute_native({"file_ext": suffix}, tmp_path)
                except UnsupportedNative as exc:
                    raise HTTPException(400, f"这个格式暂不支持（{suffix}）：{exc}") from exc
                finally:
                    try:
                        Path(tmp_path).unlink()
                    except OSError:
                        pass
        elif text.strip():
            body = text
    else:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = {}
        body = str((payload or {}).get("text") or "")
    if not body.strip():
        raise HTTPException(400, "请上传招标文件，或粘贴其中的服务要求文本")

    reqs = extract_time_requirements(body)
    with readonly_db() as con:
        kb = build_module_kb(con, list(MODULE_KEYWORDS))
        table = build_check_table(con, body)
    compared = compare_with_history(reqs, kb)
    s = summarize(compared)
    return {
        "source": src_name,
        # —— 逐条应答核对表：**全部** ★/▲ 实质性条款（资质/业绩/交付/商务…），别再漏条款 ——
        "checklist": table["checklist"],
        "by_category": table["by_category"],
        "checklist_total": table["total"],
        # —— 时限类另有「该承诺多少」的对照 ——
        "requirements": compared,
        "summary": s,
        "scope_note": (
            f"从「{src_name}」抽出 **{table['total']} 条实质性条款**（★/▲ 标记 —"
            f"不响应即废标）与 **{s['total']} 条服务时限要求**（其中 {s['mandatory']} 条带硬性措辞）。"
            f"服务时限对照历史响应文件：**{s['covered']} 条有同等或更严的先例**、"
            f"**{s['stricter']} 条比历史任何一次都严（需确认能否做到）**、"
            f"**{s['uncovered']} 条历史材料里没有对应承诺**。"
            "核对表里的「材料池」是**粗粒度线索**（我司库里有多少份这类材料），"
            "**不等于某一条要求已满足** —— 具体某条（如「具备 CNAS 认证」）仍须人工打开文件核对。"
        ),
    }

