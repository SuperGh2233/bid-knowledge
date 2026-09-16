"""文件身份说明（2026-09-15）：`project_summary_of` / `doc_purpose_of` 单测 + 证据链路透传。

背景：需求二证据原本只带章节 heading+正文，LLM 召回后不知道这份文件属于什么项目、是干什么的。
本测试钉住：
  1) 确定性函数输出正确（纯规则、零外发）；
  2) evidence 项包含新字段；
  3) packs_to_payload 白名单透传（不带默认惰性缺失）；
  4) build_gen_prompt **包含** 项目简介/文件用途，且**不含** NAS 路径（红线）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.proposal as P  # noqa: E402


def test_project_summary_of_strips_date_prefix():
    """`20250103-客户-联系人-项目名` 去日期前缀 → 项目简介。"""
    assert P.project_summary_of("20250103-肖前程-重庆市人民医院检验科人血清全谱代谢组双平台检测") \
        == "肖前程-重庆市人民医院检验科人血清全谱代谢组双平台检测"
    # 无日期 → 原样；空 → 空串
    assert P.project_summary_of("20251216-韩雪梅-包虫检测") == "韩雪梅-包虫检测"
    assert P.project_summary_of("无日期目录名") == "无日期目录名"
    assert P.project_summary_of("") == ""
    assert P.project_summary_of(None) == ""


def test_doc_purpose_of_our_response():
    """our_response 响应文件：角色标签 + 文件名主干（剥厂商尾缀/扩展名）。"""
    got = P.doc_purpose_of("20260902-标书-朱之发-10x/欧易响应文件_20260912151213.pdf",
                           "our_response")
    # 角色标签在前；版本戳 _20260912151213 保留（不是厂商署名）
    assert got.startswith("我方响应（")
    assert "欧易响应文件_20260912151213" in got


def test_doc_purpose_of_contract_evidence():
    """contract_evidence 订单号合同：角色标签 + 文件名（剥厂商尾缀）。"""
    got = P.doc_purpose_of("…/合同/4 BOE2026011108-10 上海欧易生物医学科技有限公司合同.pdf",
                           "contract_evidence")
    assert got.startswith("合同证据（")


def test_doc_purpose_strips_company_suffix_only_at_end():
    """厂商署名段只在文件名**末尾**或**中段独立段**被剥；抬头/嵌入词保留（那是业务主体）。"""
    # 末尾「+上海欧易生物医学科技有限公司」被剥（先剥扩展名后剥厂商）
    assert P._strip_company_suffix("报价单+上海欧易生物医学科技有限公司.pdf") == "报价单"
    assert P._strip_company_suffix("欧易响应文件-上海欧易生物.pdf") == "欧易响应文件"
    # 中段独立段（前后都是分隔符）→ 剥掉该厂商段
    assert P._strip_company_suffix("…项目-欧易生物-响应文件-20250103.docx") \
        == "…项目-响应文件-20250103"
    # 抬头厂名后随字（业务主体，非独立段）→ 不剥
    assert P._strip_company_suffix("欧易生物报名文件.docx") == "欧易生物报名文件"
    # 厂商嵌入中文词（前随字非分隔符）→ 不剥
    assert P._strip_company_suffix("(加密)上海欧易生物医学科技有限公司-报价.docx")\
        .startswith("(加密)上海欧易生物医学科技有限公司")


def test_doc_purpose_none_relative_path_falls_back_to_role():
    """无 relative_path → 只给角色标签；未知角色 → 原样。"""
    assert P.doc_purpose_of(None, "our_response") == "我方响应"
    assert P.doc_purpose_of("x.pdf", "some_unknown_role") == "some_unknown_role（x）"


def _con(rows: list[tuple[str, str, str]]) -> sqlite3.Connection:
    """与 test_proposal._con 同构：6 列 fixture（本方案不改 SELECT，故无需加列）。"""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
                " source_root_id TEXT, project_folder TEXT, document_role TEXT, parse_status TEXT)")
    con.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?)",
                    [(d, rel, "2026年", proj, role, "pending") for d, rel, proj, role in rows])
    return con


def _cand(sid: str, doc: str, heading: str, text: str) -> dict:
    return {"section_id": sid, "document_id": doc, "project_key": "2026年/x",
            "heading": heading, "text": text, "content_format": "native_text", "score": 1.0}


def test_evidence_carries_identity_fields(monkeypatch):
    """build_evidence_packs 的 evidence 项含 project_summary / doc_purpose。"""
    con = _con([("ours", "P/欧易响应文件.docx", "20260902-标书-朱之发-10x 项目", "our_response")])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand("s1", "ours", "1、售后服务体系", "售后 响应 时限")])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    e = pack.evidence[0]
    assert e["project_summary"] == "标书-朱之发-10x 项目"      # 目录名剥日期前缀（含类型段）
    assert e["doc_purpose"] == "我方响应（欧易响应文件）"


def test_payload_whitelist_keeps_identity(monkeypatch):
    """packs_to_payload 白名单必须透传两个新字段（不在白名单就静默丢）。"""
    con = _con([("ours", "P/欧易响应文件.docx", "20260902-标书-朱之发-10x 项目", "our_response")])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand("s1", "ours", "1、售后服务", "售后 响应")])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    payload = P.packs_to_payload([pack])
    e = payload["modules"][0]["evidence"][0]
    assert e["project_summary"] == "标书-朱之发-10x 项目"
    assert e["doc_purpose"] == "我方响应（欧易响应文件）"


def test_gen_prompt_includes_identity_and_no_nas_path(monkeypatch):
    """提示词含 项目简介/文件用途；且**不含** source_path/NAS 路径（红线）。"""
    con = _con([("ours", "P/欧易响应文件.docx", "20260902-标书-朱之发-10x 项目", "our_response")])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand("s1", "ours", "1、售后服务", "售后 响应 24小时")])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    payload = P.packs_to_payload([pack])
    prompt = P.build_gen_prompt(payload, "必须包含服务周期")
    assert "项目简介" in prompt and "标书-朱之发-10x 项目" in prompt
    assert "文件用途" in prompt and "我方响应（欧易响应文件）" in prompt
    # 红线：NAS 路径不外发（test_build_gen_prompt_only_contains_evidence_and_constraints 同样钉住）
    assert "\\\\nas" not in prompt and "192.168" not in prompt
    assert "source_path" not in prompt


def test_assemble_output_mentions_identity(monkeypatch):
    """本地拼装路径（已下线但单测仍用）出处行也含项目简介（不回归）。"""
    con = _con([("ours", "P/欧易响应文件.docx", "20260902-标书-朱之发-10x 项目", "our_response")])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand("s1", "ours", "1、售后服务", "售后 响应")])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    payload = P.packs_to_payload([pack])
    out = P.assemble_proposal(payload, "")
    md = out.get("markdown") if isinstance(out, dict) else out   # assemble 返回 dict（含 markdown key）
    assert "项目简介" in md and "标书-朱之发-10x 项目" in md
    assert "文件用途" in md and "我方响应（欧易响应文件）" in md

# —— R2-1 方案产品维度（2026-09-15，PLAN-20260915 §8-3）——
# 用户裁定：**全部模块按产品特异处理**，不维护人工的通用/特异清单，**调用时交给 LLM 区分**。

def test_detect_product_uses_same_aliases_as_search():
    """产品识别必须与检索侧**同一套别名口径** —— 两处各写一套会静默分叉。"""
    from app.api import detect_product
    assert detect_product("单细胞转录组，应急管理措施") == "单细胞"
    assert detect_product("代谢组 质量控制方案") == "代谢组"
    assert detect_product("Visium HD 空间转录组") == "空间转录组"   # R1-1.c 并入的平台
    assert detect_product("Xenium") == "Xenium"                    # 单列，不并入空间转录组
    # 识别不出 → 空串（**不猜**：无产品线的通用方案照常生成，不加裁剪规则）
    assert detect_product("售后服务方案，必须包含服务周期") == ""


def test_gen_prompt_declares_product_line_and_filter_rule():
    """指定产品线时，提示词必须**如实告知产品线**并给出裁剪规则（规则在 `GEN_SYSTEM`）。"""
    payload = {"modules": [{"module": "应急预案", "status": "ok", "evidence": []}]}
    p = P.build_gen_prompt(payload, "", None, product="单细胞")
    assert "【本次方案的产品线】单细胞" in p
    assert "只保留与本产品线相关的" in p
    # 未指定产品线时不加该段（避免对通用方案误裁）
    p2 = P.build_gen_prompt(payload, "", None)
    assert "【本次方案的产品线】" not in p2
    # 裁剪规则写在系统提示里（GEN_SYSTEM 规则 9）
    assert "只保留与该产品线相关的条目" in P.GEN_SYSTEM
