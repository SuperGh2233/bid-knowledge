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
    # 需求二只保留**模型起草**（2026-09-14 用户指令）；`mode=local`（本地抽取式装配）
    # 已从产品入口下线 —— API 不再接受，历史调用方会收到明确 400。
    mode: str = "llm"


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
      未开启时返回 403 且**不会有任何外发**（授权记录见 `docs/llm-generation-authorization.md`）。

    本地抽取式装配（`mode=local`）已于 2026-09-14 **下线**（用户指令只保留模型生成）；
    `assemble_proposal` 实现仍在 `app/proposal.py`（含单元测试），但不再从 API 暴露。

    产物附 `validation`（编造引用 / 缺小节 / 无法回溯的数字）、
    `warnings`（数值冲突，**不自动择一**）、`gaps`（证据不足的小节）。
    """
    from app.proposal import (MODULE_KEYWORDS, ProposalGenError, ProposalGenNotAuthorized,
                              unrecognized_requirements,
                              build_evidence_packs, build_module_kb,
                              extract_required_sections, generate_proposal, packs_to_payload)

    if request.mode != "llm":
        raise HTTPException(400, MODE_DEPRECATED_MSG)
    mods = [m.strip() for m in request.modules.split(",") if m.strip()] or \
        extract_required_sections(request.query)
    unknown = [m for m in mods if m not in MODULE_KEYWORDS]
    if unknown:
        raise HTTPException(400, f"未知方案小节：{unknown}；可选：{list(MODULE_KEYWORDS)}")
    if not mods:
        raise HTTPException(400, "请指定方案小节，例如："
                                 "{\"query\": \"售后服务方案，必须包含服务周期\"}")
    with readonly_db() as con:
        packs = build_evidence_packs(con, mods)
        # 「模块化经验」（历史跨项目归纳）—— **零外发**的本地整理，随提示词发给模型。
        # ES 不可达时降级为 None（生成仍可跑，只是少了结构与口径那一段），**不因此 500**。
        kb = build_module_kb(con, mods)
    payload = packs_to_payload(packs)
    try:
        result = generate_proposal(payload, request.constraints, kb)
    except ProposalGenNotAuthorized as exc:
        raise HTTPException(403, str(exc)) from exc
    except ProposalGenError as exc:
        raise HTTPException(502, str(exc)) from exc
    result["modules"] = payload["modules"]
    result["coverage"] = payload["coverage"]
    result["kb_used"] = bool(kb)
    # ⚠️ 用户**点名要求**、但没被识别成小节的 → 如实告警，不静默丢（项目红线）。
    # 实测：`售后服务方案，必须包含质控要求` 只认出「售后方案」，「质控要求」被丢掉且无任何提示。
    _missed = unrecognized_requirements(request.query or request.modules or "", mods)
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

