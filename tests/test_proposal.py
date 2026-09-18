"""R7 模块级方案生成（本地机械件）单测。

**不需要 ES / key**：`_recall` 被 monkeypatch 成固定候选，
`build_evidence_packs` 的角色回查走内存 SQLite。
覆盖的是计划的硬约束，不是实现细节：
  - 方案模块的词面映射（R7-01）；
  - 角色过滤：竞品/招标/未知**不得**成为方案证据（R5-01/D2A）；
  - 近重复只算一条：同模板跨项目复制**不得**视为独立证据（R7-03）；
  - `ok` 需 ≥3 条**标题命中**；否则 `sparse`；无命中 `insufficient`；
  - 正文兜底只补到软下限，**不向硬上限填充**；
  - 生成步骤默认**拒绝**（未授权不外发）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config as config  # noqa: E402
import app.proposal as P  # noqa: E402


# —— R7-01 小节映射 ——

def test_extract_required_sections_by_name_and_keyword():
    assert P.extract_required_sections("售后服务方案") == ["售后方案"]
    assert P.extract_required_sections("必须包含应急预案和培训方案") == ["应急预案", "培训方案"]
    # 顺序固定为 MODULE_KEYWORDS 定义顺序（确定性）
    assert P.extract_required_sections("培训方案、售后方案") == ["售后方案", "培训方案"]


def test_extract_required_sections_no_match_returns_empty():
    """匹配不到就返回空 —— 不猜。"""
    assert P.extract_required_sections("帮我写个投标函") == []
    assert P.extract_required_sections("") == []


# —— 本地近重复（不外发）——

def test_jaccard_identical_and_different():
    a = P.shingles("本项目提供售后服务，响应时间30分钟。")
    assert P.jaccard(a, P.shingles("本项目提供售后服务，响应时间30分钟。")) == 1.0
    b = P.shingles("完全无关的另一段内容，讲培训安排与考核。")
    assert P.jaccard(a, b) < 0.3


def test_cluster_groups_template_copies():
    """同一模板跨项目复制 → 落进同一簇（不得当作独立证据）。"""
    tpl = "售后服务体系：我司提供7×24小时技术支持，接到通知后30分钟内响应，重大问题24小时内到场处理。"
    other = "培训安排：项目验收后为客户提供两次线上培训，内容包括仪器操作、样本处理流程与实验记录规范。"
    cands = [{"section_id": "a", "text": tpl},
             {"section_id": "b", "text": tpl + "（某项目补充一句）"},
             {"section_id": "c", "text": other}]
    clusters = P._cluster(cands)
    assert len(clusters) == 2
    assert sorted(len(c) for c in clusters) == [1, 2]


def test_match_basis_heading_beats_text():
    """标题命中记为 heading（高精度）；仅正文提到记为 text（噪声大）。"""
    assert P._match_basis("售后方案", "3.1 售后服务体系", "") == "heading"
    assert P._match_basis("售后方案", "6 样品流转", "……售后……") == "text"
    assert P._match_basis("售后方案", "6 样品流转", "与本小节无关") is None


# —— Evidence Pack 组装（假 ES + 内存库）——

def _con(rows: list[tuple[str, str]]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
                " source_root_id TEXT, project_folder TEXT, document_role TEXT, parse_status TEXT)")
    con.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?)",
                    [(d, f"P/{d}.docx", "2026年", f"proj-{p}", role, "pending")
                     for d, role, p in rows])
    return con


def _cand(sid: str, doc: str, heading: str, text: str, project: str) -> dict:
    return {"section_id": sid, "document_id": doc, "project_key": project,
            "heading": heading, "text": text, "content_format": "native_text", "score": 1.0}


def test_role_filter_excludes_non_our_docs(monkeypatch):
    """竞品 / 招标 / 未知角色的章节**不得**进入方案证据（R5-01/D2A）。"""
    con = _con([("ours", "our_response", "p1"),
                ("comp", "competitor_response", "p2"),
                ("tender", "tender_requirement", "p3")])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand("s1", "ours", "1、售后服务体系", "售后 响应 时限", "p1"),
        _cand("s2", "comp", "2、售后服务方案", "售后 响应 时限", "p2"),
        _cand("s3", "tender", "3、售后要求", "售后 响应 时限", "p3"),
    ])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    assert {e["document_id"] for e in pack.evidence} == {"ours"}
    assert pack.role_rejected == 2


def test_dedup_before_counting_template_copies_do_not_pad(monkeypatch):
    """同模板跨项目复制**去重后才计数** → 只剩 1 条，据实报 sparse（不凑数）。"""
    tpl = "售后服务体系：7×24小时技术支持，接到通知后30分钟内响应，重大问题24小时内到场。"
    con = _con([(f"d{i}", "our_response", f"p{i}") for i in range(4)])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand(f"s{i}", f"d{i}", "1、售后服务", tpl, f"p{i}") for i in range(4)])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    assert pack.deduped == 1
    assert pack.dropped_duplicates == 3
    assert pack.status == "sparse"


def test_ok_requires_three_heading_hits(monkeypatch):
    con = _con([(f"d{i}", "our_response", f"p{i}") for i in range(4)])
    texts = ["售后服务体系：7×24小时技术支持，30分钟内响应，24小时内到场处理问题。",
             "培训安排：验收后提供两次线上培训，内容含仪器操作与实验记录规范要求。",
             "应急预案：针对试剂缺货与样本异常，制定补货与重采流程，保障项目按期交付。",
             "质量控制：设置阴阳性对照与重复样本，数据质控不合格即重跑，全程留痕可查。"]
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand(f"s{i}", f"d{i}", "1、售后服务", t, f"p{i}") for i, t in enumerate(texts)])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    assert pack.heading_hits == 4 and pack.status == "ok"


def test_text_fallback_only_fills_to_soft_floor(monkeypatch):
    """无标题命中时正文兜底**只补到软下限 3**，不向硬上限 5 填充。"""
    con = _con([(f"d{i}", "our_response", f"p{i}") for i in range(8)])
    texts = [f"第{i}段：本节讨论样品流转与实验室内部管理流程的第{i}项细节性说明内容。"
             for i in range(8)]
    # 标题都不含售后词 → 全部只能靠正文命中（正文里塞入「售后」）
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand(f"s{i}", f"d{i}", f"{i} 样品流转", texts[i] + "售后相关说明。", f"p{i}")
        for i in range(8)])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    assert pack.heading_hits == 0
    assert pack.deduped == 3            # 软下限，而非硬上限 5
    assert pack.status == "sparse"
    assert any("无任何标题命中" in n for n in pack.notes)


def test_insufficient_when_no_hits(monkeypatch):
    con = _con([("d1", "our_response", "p1")])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [])
    pack = P.build_evidence_packs(con, ["应急预案"])[0]
    assert pack.status == "insufficient" and pack.evidence == []


def test_same_project_cap(monkeypatch):
    """diversity 软策略：同项目默认 ≤2 条。"""
    con = _con([("d1", "our_response", "same")])
    texts = ["售后服务体系：7×24小时技术支持，30分钟内响应，24小时内到场处理问题。",
             "培训安排：验收后提供两次线上培训，内容含仪器操作与实验记录规范要求。",
             "应急预案：针对试剂缺货与样本异常，制定补货与重采流程，保障按期交付。"]
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand(f"s{i}", "d1", "1、售后服务", t, "same") for i, t in enumerate(texts)])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    assert pack.deduped <= 2


def test_score_lines_excluded(monkeypatch):
    """评分要求行不得进入正式证据（判据自 R5 阶段沿用）。"""
    con = _con([("d1", "our_response", "p1"), ("d2", "our_response", "p2")])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand("s1", "d1", "1、售后服务", "售后服务方案得2分，评分标准见下表。", "p1"),
        _cand("s2", "d2", "2、售后服务", "售后服务体系：7×24小时技术支持，30分钟内响应。", "p2")])
    pack = P.build_evidence_packs(con, ["售后方案"])[0]
    assert {e["section_id"] for e in pack.evidence} == {"s2"}


def test_payload_refs_are_system_generated(monkeypatch):
    """引用编号 E1..En 由系统生成、按序稳定（模型不得自造）。"""
    con = _con([("d1", "our_response", "p1")])
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand("s1", "d1", "1、售后服务", "售后 7×24 响应 30 分钟。", "p1")])
    pl = P.packs_to_payload(P.build_evidence_packs(con, ["售后方案"]))
    assert [e["ref"] for e in pl["modules"][0]["evidence"]] == ["E1"]
    assert pl["coverage"]["requested"] == 1


# —— 生成步骤默认拒绝（外发）——

def test_generate_proposal_refuses_when_not_authorized(monkeypatch):
    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", False)
    with pytest.raises(P.ProposalGenNotAuthorized):
        P.generate_proposal({"modules": []})


# —— R7-05/06/07 生成侧（LLM 全部 mock，不外发）——

def _payload():
    return {"modules": [
        {"module": "售后方案", "status": "ok", "notes": [],
         "evidence": [
             {"ref": "E1", "file_name": "a.docx", "project_folder": "P1", "heading": "售后",
              "source_path": "\\\\nas\\a.docx",
              "text": "我司提供售后服务，接到通知后 30 分钟响应，24 小时到场。"},
             {"ref": "E2", "file_name": "b.docx", "project_folder": "P2", "heading": "服务承诺",
              "source_path": "\\\\nas\\b.docx",
              "text": "服务响应时间为 2 小时，重大问题 24 小时到场处理。"},
         ]},
        {"module": "应急预案", "status": "sparse", "notes": ["标题命中不足"],
         "evidence": [{"ref": "E3", "file_name": "c.docx", "project_folder": "P3",
                       "heading": "应急", "source_path": "\\\\nas\\c.docx",
                       "text": "试剂缺货时紧急调货，样本异常时重新采集。"}]},
    ], "coverage": {"requested": 2, "ok": 1, "sparse": 1, "insufficient": 0}}


def test_build_gen_prompt_only_contains_evidence_and_constraints():
    """R7-05：交给模型的只有命中的原文、来源与用户约束。"""
    prompt = P.build_gen_prompt(_payload(), "必须包含服务周期")
    assert "必须包含服务周期" in prompt
    assert "[E1]" in prompt and "[E3]" in prompt
    assert "\\\\nas\\a.docx" not in prompt          # 路径不外发，只给文件名
    assert "a.docx" in prompt and "P1" in prompt
    assert "售后方案（证据状态：ok）" in prompt


def test_detect_conflicts_reports_without_choosing():
    """同一小节、**同一语义槽位**内的时限冲突 → 警告，**不自动择一**。

    E1「30 分钟响应」vs E2「2 小时响应」→ 同一件事的两种写法，单位归一后是冲突。
    """
    conflicts = P.detect_conflicts(_payload())
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["module"] == "售后方案" and c["slot"] == "响应"
    assert c["values"] == ["30分钟", "2小时"]
    assert "不自动择一" in c["message"]
    assert c["sources"]["30分钟"] == ["E1"] and c["sources"]["2小时"] == ["E2"]


def test_detect_conflicts_ignores_same_unit_different_slot():
    """**同单位 ≠ 同一件事**：「24 小时到场」与「2 小时响应」不得报成冲突（误报比漏报更糟）。"""
    payload = {"modules": [{"module": "售后方案", "status": "ok", "evidence": [
        {"ref": "E1", "text": "接到通知后 3 小时响应，24 小时到场处理。"},
        {"ref": "E2", "text": "重大问题 2 小时响应，24 小时到场处理。"},
    ]}]}
    conflicts = P.detect_conflicts(payload)
    assert [c["slot"] for c in conflicts] == ["响应"]      # 只有响应冲突，到场一致
    assert conflicts[0]["values"] == ["2小时", "3小时"]     # 按归一分钟后升序（确定性）


def test_detect_conflicts_same_slot_same_value_no_conflict():
    """同一槽位数值一致（含单位不同但等价）→ 不报冲突。"""
    payload = {"modules": [{"module": "售后方案", "status": "ok", "evidence": [
        {"ref": "E1", "text": "我方 30 分钟响应。"},
        {"ref": "E2", "text": "我司响应时间为 0.5 小时。"},
    ]}]}
    assert P.detect_conflicts(payload) == []



def test_validate_generation_catches_fabricated_citation():
    problems = P.validate_generation("## 售后方案\n我方 30 分钟内响应 [E1]。另见 [E99]。", _payload())
    assert any("编造" in p for p in problems)


def test_validate_generation_catches_missing_section_and_untraceable_number():
    problems = P.validate_generation("## 售后方案\n我方承诺 15 天内完成 [E1]。", _payload())
    assert any("缺少必要小节" in p and "应急预案" in p for p in problems)
    assert any("无法回溯" in p and "15" in p for p in problems)


def test_validate_generation_passes_on_grounded_output():
    md = ("## 售后方案\n我司接到通知后 30 分钟响应 [E1]，重大问题 24 小时到场 [E1][E2]；"
          "另一份历史文件写的是 2 小时 [E2]。\n## 应急预案\n历史材料不足，仅见试剂调货说明 [E3]。")
    assert P.validate_generation(md, _payload()) == []


def test_validate_ignores_numbers_inside_citation_markers():
    """**回归（对抗性复核发现）**：`[E23]` 里的 `23` 不得被当成正文数字。

    原实现用 `used`（存 `"E23"`，带前缀）去挡裸数字 `"23"`，永不匹配 →
    **任何 `[E10]`..[En] 引用都被伪报「无法回溯的数字」**。真实九模块证据包有 29 条引用，
    等于真实生成必踩；旧测试只用 E1–E3，完全没覆盖到。
    """
    payload = {"modules": [{"module": "项目管理与实施方案", "status": "ok", "evidence": [
        {"ref": f"E{i}", "file_name": f"f{i}.docx", "text": f"第{i}条做法。"} for i in range(1, 30)
    ]}]}
    # 正文零数字、只引用高位编号 → 必须通过
    md = "## 项目管理与实施方案\n本节承诺与历史一致 [E23]，另有 [E27][E29]。"
    assert P.validate_generation(md, payload, "") == []
    # 真正的不可回溯数字仍要抓住
    bad = "## 项目管理与实施方案\n本节承诺 99 天内完成，与历史一致 [E23]。"
    assert any("99" in p for p in P.validate_generation(bad, payload, ""))


def test_validate_accepts_constraint_titled_section():
    """真机实测：模型会把小节标题写成**用户要求的必含内容**，而不是模块名。

    实测输出（qwen3.7-flash，合成证据）：用户要求「必须包含服务周期」，
    模型标题写成「## 服务周期」而非「## 售后方案」→ 原来的严格判据**误报**「缺少必要小节：售后方案」。
    一个在真实输出上常态误报的校验器会被用户直接忽略，所以判据放宽为
    「模块名 / 模块关键词 / 用户声明的必含内容」任一命中。
    """
    md = ("## 服务周期\n不同证据存在冲突：[E1] 1 年、[E2] 2 年。\n"
          "## 应急预案\n历史材料不足，仅见片段 [E3]。")
    assert P.validate_generation(md, _payload(), "必须包含服务周期和应急预案") == []
    # 真的缺小节时仍然要报（未命中的模块名/关键词/约束词）
    assert P.validate_generation("## 服务周期\n无引用无小节。", _payload(), "必须包含服务周期") != []


class _FakeClient:
    def __init__(self, content): self._content = content
    class _Chat:
        def __init__(self, content): self._content = content
        @property
        def completions(self): return self
        def create(self, **kwargs):
            class M:  # noqa: N801
                pass
            msg = M(); msg.content = self._content
            ch = M(); ch.message = msg
            r = M(); r.choices = [ch]
            return r
    @property
    def chat(self): return _FakeClient._Chat(self._content)


def test_generate_proposal_returns_citations_warnings_and_validation(monkeypatch):
    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", True)
    md = "## 售后方案\n30 分钟响应 [E1]，另有 2 小时的说法 [E2]。\n## 应急预案\n材料不足 [E3]。"
    monkeypatch.setattr(P, "_gen_client", lambda: (_FakeClient(md), "fake-model"))
    out = P.generate_proposal(_payload(), "必须包含服务周期")
    assert out["markdown"] == md
    assert [c["ref"] for c in out["citations"]] == ["E1", "E2", "E3"]
    assert out["citations"][0]["source_path"] == "\\\\nas\\a.docx"
    assert len(out["warnings"]) == 1                      # 响应：30 分钟 vs 2 小时
    assert [g["module"] for g in out["gaps"]] == ["应急预案"]
    assert out["validation"] == []


def test_payload_records_prompt_budget_truncation(monkeypatch):
    """超提示词预算时按比例收缩摘录，并**如实记录**收缩情况（不能让人以为看到的是全文）。"""
    monkeypatch.setattr(config, "PROPOSAL_GEN_MAX_PROMPT_CHARS", 2000)
    con = _con([(f"d{i}", "our_response", f"p{i}") for i in range(6)])
    # 六条必须**互不相似**，否则会被近重复聚类并成一条（那是另一条规则的职责）。
    uniq = [
        "售后服务团队由专职工程师组成，接到通知后按约定时限响应并记录处置过程。",
        "培训安排包含线上与现场两种形式，覆盖设备操作、数据解读与安全规范要求。",
        "质量控制设置阴阳性对照与重复样本，数据不合格立即重跑并保留完整记录。",
        "应急预案针对试剂缺货与样本异常制定替补流程，确保整体项目按期交付使用。",
        "保密措施要求全员签署保密协议，数据分级授权访问并留存审计日志备查。",
        "样本接收核对数量与状态后入库登记，运输全程监测温度并及时补加干冰。",
    ]
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        _cand(f"s{i}", f"d{i}", f"{i}、售后服务", t * 30, f"p{i}")
        for i, t in enumerate(uniq)])
    packs = P.build_evidence_packs(con, ["售后方案"])
    # 硬上限 max_packs=5，故最多 5 条；这里只要求「不是被去重压成一条」
    assert packs[0].deduped == 5, f"用例前提不成立：去重后只有 {packs[0].deduped} 条"
    pl = P.packs_to_payload(packs)
    assert pl["truncation"] is not None
    assert pl["truncation"]["budget_chars"] == 2000
    assert pl["truncation"]["excerpt_per_item"] < 1200
    assert "摘录" in pl["truncation"]["note"]
    # 每条都记下**完整长度**，便于判断摘录丢了多少
    assert all(e["text_full_len"] > len(e["text"]) for m in pl["modules"] for e in m["evidence"])
    # 小载荷不触发收缩
    monkeypatch.setattr(config, "PROPOSAL_GEN_MAX_PROMPT_CHARS", 10_000_000)
    assert P.packs_to_payload(packs)["truncation"] is None


# —— HTTP 层（R7 入口）。LLM 走桩，**不外发** ——

def test_api_generate_defaults_to_llm_and_403_when_unauthorized(monkeypatch, tmp_path):
    """**默认走 `mode=llm`（模型而生）：未授权时 403，且连网关客户端都没构造。**

    行为变更（2026-09-14 用户指令）：需求二只保留模型生成 —— 默认从 `mode=local`
    （本地抽取式装配）改为 `mode=llm`。原来那个「默认 local 200 且不外发」的测试
    钉的是旧产品决策；本地装配现在从 API 下线（`assemble_proposal` 实现保留，
    其单元测试仍跑，只是不再从 `/api/proposal-generate` 暴露）。
    """
    import sqlite3
    from fastapi.testclient import TestClient
    import app.api as A

    db = tmp_path / "tl.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
                " source_root_id TEXT, project_folder TEXT, document_role TEXT, parse_status TEXT)")
    con.execute("INSERT INTO documents VALUES ('d1','P/a.docx','2026年','proj1','our_response','pending')")
    con.commit(); con.close()
    monkeypatch.setattr(A, "DEMO_DB", db)
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        {"section_id": "s1", "document_id": "d1", "project_key": "proj1",
         "heading": "1、售后服务", "text": "我司接到通知后 30 分钟响应，24 小时到场。",
         "content_format": "native_text", "score": 1.0}])
    called = []
    monkeypatch.setattr(P, "_gen_client", lambda: called.append(1) or (None, None))
    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", False)

    r = TestClient(A.app).post("/api/proposal-generate", json={"modules": "售后方案"})
    assert r.status_code == 403          # 默认 llm，未授权 → 403，不是 200 local
    assert "未授权" in r.json()["detail"]
    assert called == []                  # **关键：未授权时连网关客户端都不构造（无任何外发）**


def test_api_generate_rejects_deprecated_local_mode(monkeypatch):
    """`mode=local` 已下线（2026-09-14 用户指令只保留模型生成）→ 400 明确提示。"""
    from fastapi.testclient import TestClient
    import app.api as A

    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", True)
    r = TestClient(A.app).post("/api/proposal-generate",
                               json={"modules": "售后方案", "mode": "local"})
    assert r.status_code == 400
    assert "只保留模型起草" in r.json()["detail"]


def test_api_generate_llm_mode_refuses_with_403_when_not_authorized(monkeypatch):
    """**`mode=llm` 未授权时 403，且连网关客户端都没构造** —— 不得静默外发。"""
    from fastapi.testclient import TestClient
    import app.api as A

    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", False)
    called = []
    monkeypatch.setattr(P, "_gen_client", lambda: called.append(1) or (None, None))
    r = TestClient(A.app).post("/api/proposal-generate",
                               json={"modules": "售后方案", "mode": "llm"})
    assert r.status_code == 403
    assert "未授权" in r.json()["detail"]
    assert called == []


def test_api_generate_rejects_unknown_mode():
    from fastapi.testclient import TestClient
    import app.api as A
    r = TestClient(A.app).post("/api/proposal-generate",
                               json={"modules": "售后方案", "mode": "nonsense"})
    assert r.status_code == 400


def test_api_generate_full_path_with_stub(monkeypatch, tmp_path):
    """授权开启 + 桩客户端 → 走完 HTTP 全链路（仍不外发）。"""
    import os
    import sqlite3
    from fastapi.testclient import TestClient
    import app.api as A

    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
                " source_root_id TEXT, project_folder TEXT, document_role TEXT, parse_status TEXT)")
    con.execute("INSERT INTO documents VALUES ('d1','P/a.docx','2026年','proj1','our_response','pending')")
    con.commit(); con.close()
    monkeypatch.setenv("BID_AI_DEMO_DB", str(db))
    monkeypatch.setattr(A, "DEMO_DB", db)
    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", True)
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        {"section_id": "s1", "document_id": "d1", "project_key": "proj1",
         "heading": "1、售后服务", "text": "我司接到通知后 30 分钟响应，24 小时到场。",
         "content_format": "native_text", "score": 1.0}])
    md = "## 售后方案\n30 分钟响应 [E1]，24 小时到场 [E1]。"
    monkeypatch.setattr(P, "_gen_client", lambda: (_FakeClient(md), "stub"))
    client = TestClient(A.app)
    d = client.post("/api/proposal-generate",
                    json={"modules": "售后方案", "mode": "llm"}).json()
    assert d["markdown"] == md
    assert [c["ref"] for c in d["citations"]] == ["E1"]
    assert d["citations"][0]["source_path"].endswith("P\\a.docx") or \
        d["citations"][0]["source_path"].endswith("P/a.docx")
    assert d["coverage"]["requested"] == 1
    assert d["validation"] == []


def test_api_generate_rejects_unknown_module(monkeypatch):
    from fastapi.testclient import TestClient
    import app.api as A
    client = TestClient(A.app)
    assert client.post("/api/proposal-generate", json={"modules": "不存在的小节"}).status_code == 400
    assert client.post("/api/proposal-generate", json={}).status_code == 400



# —— 标题合理性守卫（实测索引 5.7% 的 heading 不是标题）——

def test_implausible_headings_are_rejected():
    """**非标题的 heading 不得进证据**（实测索引 9,437 条里 **541 条 = 5.7%** 属此类）。

    来源：OCR 与原生 PDF 在密集文档（合同条款/图表/参考文献）上的切分残渣。
    下列样本**全部取自实测索引**：电话号、表格数值、引文页码范围、DOI。

    ⚠️ 这是**安全网**：实测它在当前语料上触发 **0 次**（BM25 排序已把这类挡在候选池外），
    它防的是「噪声章节的正文恰好排进某模块前 25」——故仍须挂网并锁住行为。
    """
    assert not P._is_plausible_heading("18981846247")          # 电话号
    assert not P._is_plausible_heading("3.4300")               # 表格数值
    assert not P._is_plausible_heading("11.7499%")             # 百分比
    assert not P._is_plausible_heading("283-286,")             # 引文页码范围
    assert not P._is_plausible_heading("10.1016/j.oell.2024.04.040")   # DOI
    assert not P._is_plausible_heading("https://x.test/a")
    assert not P._is_plausible_heading("") and not P._is_plausible_heading(None)
    # 真标题一律放行
    for h in ("4、售后服务闭环处置流程", "3.1售后服务体系", "（六）技术服务团队",
              "8.6 质量控制与数据管理方案及承诺", "培训内容：项目管理方法"):
        assert P._is_plausible_heading(h), h


# —— 本地抽取式装配（零外发的默认路径）——

def _asm_payload():
    return {"modules": [
        {"module": "售后方案", "status": "ok", "notes": [], "heading_hits": 5, "deduped": 2,
         "evidence": [
             {"ref": "E1", "file_name": "a.docx", "project_folder": "P1", "heading": "3.1 售后服务",
              "source_path": "\\nas\a.docx", "text": "我司提供 7×24 小时技术支持，30 分钟响应。"},
             {"ref": "E2", "file_name": "b.docx", "project_folder": "P2", "heading": "售后承诺",
              "source_path": "\\nas\b.docx", "text": "服务周期为验收后 1 年。"}]},
        {"module": "应急预案", "status": "insufficient", "notes": [], "heading_hits": 0, "deduped": 0,
         "evidence": []},
    ], "coverage": {"requested": 2, "ok": 1, "sparse": 0, "insufficient": 1}}


def test_assemble_is_extractive_and_never_fabricates():
    """**核心性质**：装配产物里除了小节标题与程序性说明，**每一个字都来自证据原文**。

    这是它相对模型生成的优势 —— 「只用我方响应 / 逐句可溯源 / 不补造」**由构造保证**，
    不是靠提示词约束模型。
    """
    out = P.assemble_proposal(_asm_payload(), "必须包含服务周期")
    assert out["mode"] == "local_extractive"        # 与模型产物**永不混淆**
    md = out["markdown"]
    # 证据原文**逐字**出现在稿里
    assert "我司提供 7×24 小时技术支持，30 分钟响应。" in md
    assert "服务周期为验收后 1 年。" in md
    # 每条证据都带系统生成的引用编号与出处
    assert "[E1]" in md and "[E2]" in md
    assert "a.docx" in md and "b.docx" in md
    # 证据不足的小节**如实说明，不补写内容**
    assert "历史材料未覆盖本小节" in md
    # 校验器应当**由构造通过**
    assert out["validation"] == []


def test_assemble_marks_sparse_sections_as_incomplete():
    pl = _asm_payload()
    pl["modules"][0]["status"] = "sparse"
    pl["modules"][0]["deduped"] = 2
    out = P.assemble_proposal(pl)
    assert "历史材料不足" in out["markdown"] and "不构成完整小节" in out["markdown"]


def test_assemble_surfaces_conflicts_without_choosing():
    pl = _asm_payload()
    pl["modules"][0]["evidence"].append(
        {"ref": "E3", "file_name": "c.docx", "project_folder": "P3", "heading": "售后",
         "source_path": "\\nas\c.docx", "text": "我司 2 小时响应，服务周期为验收后 2 年。"})
    out = P.assemble_proposal(pl)
    assert out["warnings"], "同槽位数值冲突应被报出"
    assert "不自动择一" in out["markdown"]


def test_assemble_needs_no_authorization(monkeypatch):
    """**零外发**：不设 `PROPOSAL_GEN_ENABLED` 也能装配（这是它存在的理由）。"""
    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", False)
    assert P.assemble_proposal(_asm_payload())["markdown"]


def test_detect_conflicts_dedupes_repeated_refs():
    """同一份证据里同一时限被提到多次 → 引用**不重复**。

    实测曾产出 `sources: {'10分钟': ['E7','E7']}`（E7 正文里"10 分钟"出现两次），
    而告警是给人看的：`[E7][E7]` 只会让人怀疑数字算错、损伤可信度。
    **去重不能改变冲突本身的判定** —— 两个值仍各自列出、仍报冲突。
    """
    payload = {"modules": [{"module": "应急预案", "status": "ok", "evidence": [
        {"ref": "E7", "text": "10 分钟响应。重大故障 10 分钟内到场处理。"},
        {"ref": "E8", "text": "4 小时响应。"},
    ]}]}
    conflicts = P.detect_conflicts(payload)
    assert len(conflicts) == 1
    assert conflicts[0]["sources"]["10分钟"] == ["E7"], "同一证据不得重复计入"
    assert conflicts[0]["sources"]["4小时"] == ["E8"]
    assert conflicts[0]["values"] == ["10分钟", "4小时"], "去重不得吞掉冲突值"


def test_detect_conflicts_keeps_distinct_refs_for_same_value():
    """不同证据给出同一数值 → 两条引用都要保留（去重只针对**同一** ref）。"""
    payload = {"modules": [{"module": "应急预案", "status": "ok", "evidence": [
        {"ref": "E7", "text": "10 分钟响应。"},
        {"ref": "E8", "text": "响应 10 分钟，另见附件。"},
    ]}]}
    assert P.detect_conflicts(payload) == [], "同值不构成冲突"


# —— 2026-09-13 模块化整理（需求二：把历史经验按模块归纳成可复用的内容）——


def _kb_con(rows):
    """内存库：`build_evidence_packs` 要靠它查角色 —— 角色不符的章节不进证据。
    桩掉 `_recall` 后仍需真连接（否则 `None.execute`）。"""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row          # `build_evidence_packs` 用 dict(row) 取值
    con.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
                " source_root_id TEXT, project_folder TEXT, document_role TEXT, parse_status TEXT)")
    for r in rows:
        con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?)",
                    (r["document_id"], f"P/{r['document_id']}.docx", "2026年",
                     r.get("project_key") or "proj1", "our_response", "done"))
    return con


def _stub_recall(monkeypatch, text="我司接到通知后 30 分钟响应，24 小时到场。"):
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        {"section_id": "s1", "document_id": "d1", "project_key": "proj1",
         "heading": "1、售后服务", "text": text,
         "content_format": "native_text", "score": 1.0}])


def test_module_kb_groups_commitments_and_flags_conflicts(monkeypatch):
    """模块化经验：承诺时限必须按**语义槽位**归纳，同槽位多值**如实标冲突、不择一**。

    这是需求二的「整理」一步：生成时它作为提示词的一部分发给模型。零外发。
    """
    _stub_recall(monkeypatch, "接到通知后 30 分钟响应，2 小时到场。")
    kb = P.build_module_kb(_kb_con([{"document_id": "d1"}]), ["售后方案"])
    m = kb[0]
    assert m["module"] == "售后方案"
    assert set(m["coverage"]) >= {"files", "projects", "sections"}
    slots = {c["slot"]: c for c in m["commitments"]}
    assert "响应" in slots and "到场" in slots
    assert slots["响应"]["values"] == ["30分钟"]


def test_module_kb_marks_conflicting_commitments(monkeypatch):
    """历史材料自身不一致时（同一槽位多个值）必须标 `conflict=True`。"""
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        {"section_id": "s1", "document_id": "d1", "project_key": "p1",
         "heading": "售后", "text": "30 分钟响应。", "content_format": "native_text", "score": 1.0},
        {"section_id": "s2", "document_id": "d2", "project_key": "p2",
         "heading": "售后", "text": "4 小时响应。", "content_format": "native_text", "score": 1.0}])
    kb = P.build_module_kb(_kb_con([{"document_id": "d1"}, {"document_id": "d2"}]),
                           ["售后方案"])
    slot = {c["slot"]: c for c in kb[0]["commitments"]}["响应"]
    assert slot["conflict"] is True
    assert set(slot["values"]) == {"30分钟", "4小时"}


def test_kb_prompt_block_forbids_citing_the_summary():
    """提示词里那段经验**必须**写明「不得引用本段」——否则模型会把归纳句当原文引用。

    与证据包的分工：经验说「我们通常怎么写」，证据包说「这一次可引用哪些原文」。
    """
    kb = [{"module": "售后方案", "coverage": {"files": 3, "projects": 2},
           "outline": [{"heading": "1、售后服务", "count": 1}],
           "commitments": [{"slot": "响应", "values": ["30分钟", "4小时"], "conflict": True,
                            "section_ids": {}}],
           "key_points": [], "notes": []}]
    block = P.kb_to_prompt_block(kb)
    assert "不得引用本段" in block
    assert "不得自行择一" in block          # 冲突项要带禁令
    assert "30分钟 / 4小时" in block


def test_gen_prompt_includes_kb_when_given():
    payload = {"modules": [{"module": "售后方案", "status": "ok",
                            "evidence": [{"ref": "E1", "text": "正文", "file_name": "a.docx"}]}]}
    kb = [{"module": "售后方案", "coverage": {"files": 1, "projects": 1},
           "outline": [], "commitments": [], "key_points": [], "notes": []}]
    assert "我司历史模块化经验" in P.build_gen_prompt(payload, "", kb)
    assert "我司历史模块化经验" not in P.build_gen_prompt(payload, "", None)


def test_api_llm_mode_sends_module_kb_in_prompt(monkeypatch, tmp_path):
    """**需求二的完整链路**：模块化经验 → 提示词 → 模型。

    用户 2026-09-13 的目标原文：「用户让生成方案是要把这些模块化的内容当作提示词发给 llm，
    让他根据这些模块化的经验去生成方案」。这条测试钉住那个「当作提示词」——
    否则经验算出来了却没进 prompt，是静默失效。
    用桩客户端，**不外发**。
    """
    import sqlite3
    from fastapi.testclient import TestClient
    import app.api as A

    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
                " source_root_id TEXT, project_folder TEXT, document_role TEXT, parse_status TEXT)")
    con.execute("INSERT INTO documents VALUES ('d1','P/a.docx','2026年','proj1','our_response','done')")
    con.commit(); con.close()
    monkeypatch.setenv("BID_AI_DEMO_DB", str(db))
    monkeypatch.setattr(A, "DEMO_DB", db)
    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", True)
    monkeypatch.setattr(P, "_recall", lambda mod, n: [
        {"section_id": "s1", "document_id": "d1", "project_key": "proj1",
         "heading": "1、售后服务", "text": "我司接到通知后 30 分钟响应，24 小时到场。",
         "content_format": "native_text", "score": 1.0}])
    seen: dict = {}

    class _Fake:
        """自包含的假客户端：**记录收到的 prompt**，不联网。"""
        def __init__(self, content): self._content = content
        @property
        def chat(self): return self
        @property
        def completions(self): return self
        def create(self, **kwargs):
            seen["prompt"] = kwargs["messages"][-1]["content"]
            msg = type("M", (), {"content": self._content})()
            ch = type("C", (), {"message": msg})()
            return type("R", (), {"choices": [ch]})()

    md = "## 售后方案\n30 分钟响应 [E1]。"
    monkeypatch.setattr(P, "_gen_client", lambda: (_Fake(md), "stub"))
    d = TestClient(A.app).post("/api/proposal-generate",
                               json={"modules": "售后方案", "mode": "llm"}).json()
    assert d["kb_used"] is True
    assert "我司历史模块化经验" in seen["prompt"]          # ← 经验确实进了提示词
    assert "不得引用本段" in seen["prompt"]                # ← 禁令也在
    assert "[E1]" in seen["prompt"]                        # ← 原文证据仍在（可引用那部分）
    assert d["markdown"] == md


def test_llm_result_carries_mode_llm(monkeypatch):
    """**红线**：`mode` 必须回传，否则页面分不清「本地拼装」与「模型起草」。

    实测（2026-09-13 首次真实生成）：`generate_proposal` 原先**没有** `mode` 字段，
    返回 `mode=undefined` → 前端 `MODE_LABEL[undefined]` 取不到标签。
    该缺陷一直存在但从未暴露 —— 因为 LLM 路径此前从未被启用过。
    """
    monkeypatch.setattr(config, "PROPOSAL_GEN_ENABLED", True)

    class _Fake:
        def __init__(self, content): self._content = content
        @property
        def chat(self): return self
        @property
        def completions(self): return self
        def create(self, **kwargs):
            msg = type("M", (), {"content": self._content})()
            ch = type("C", (), {"message": msg})()
            return type("R", (), {"choices": [ch]})()

    monkeypatch.setattr(P, "_gen_client", lambda: (_Fake("## 方案\n正文 [E1]。"), "stub"))
    out = P.generate_proposal({"modules": [{"module": "售后方案", "status": "ok",
                                            "evidence": [{"ref": "E1", "text": "原文"}]}]})
    assert out["mode"] == "llm"
    # 与本地装配的取值**不同**，两者才能被区分
    assert out["mode"] != "local_extractive"


# —— 2026-09-14 招标要求核对（逐条应答核对表）——


def test_tender_extracts_star_clauses_both_formats():
    """★/▲ 是中文招标文件的**实质性条款**标记（不响应即废标）—— 两种排版都要抓到。

    实测 253 份招标文件里 ★ 出现 1076 次、▲ 480 次；而「投标人须/应…」的义务句有数千条，
    全抓会淹没真正致命的那几条，故**只抓 ★/▲**。
    """
    import app.tender as T
    text = (
        "1 | ★ | 交货时间 | 自合同签订之日起60日内交付。\n"
        "2 | ▲ | 交货地点 | 采购人指定地点\n"
        "★服务内容（服务量清单）\n"
        "完成4例人胃组织空间转录组测序及数据分析\n"
        "普通条款：投标人应提供营业执照\n"          # 无 ★ → 不抓
    )
    got = T.extract_hard_requirements(text)
    names = [x["name"] for x in got]
    assert "交货时间" in names and "交货地点" in names
    section = next(x for x in got if x["name"].startswith("服务内容"))
    assert "4例人胃组织" in section["text"], "小节式条目的**正文在下面几行**，必须接住"
    assert not any("营业执照" in n for n in names), "无 ★/▲ 的普通条款不抓"


def test_tender_required_time_follows_the_clause_not_the_doc():
    """时限必须按**语义槽位**归属，且要能纠正两类实测误判。

    ① `履约保证金：不缴纳` 曾因裸的「缴纳」被归到「财务社保」（实为商务条款）；
    ② `投标人资格条件` 曾因词表缺「资格」落到「其它」。
    """
    import app.tender as T
    assert T.classify_clause("履约保证金 不缴纳") == "商务条款"
    assert T.classify_clause("投标人资格条件") == "资格资质"
    assert T.classify_clause("结算方式 支付剩余合同金额") == "商务条款", "含「合同金额」不等于业绩"
    assert T.classify_clause("类似项目业绩一览表") == "业绩案例"
    assert T.classify_clause("完全认不出的一句话") == "其它", "归不了就如实报其它，不硬塞"


def test_tender_material_pool_uses_index_not_row_factory():
    """材料池查询必须能用**裸连接**（调用方不一定设了 `row_factory`）。

    实测：首版用 `row["n"]` 取值，传裸连接直接 `TypeError: tuple indices must be integers`。
    """
    import sqlite3
    import app.tender as T
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE material_facts (document_id TEXT, fact_type TEXT)")
    con.execute("INSERT INTO material_facts VALUES ('d1','qualification')")
    con.execute("INSERT INTO material_facts VALUES ('d1','qualification')")
    con.execute("CREATE TABLE contracts (contract_id TEXT)")
    con.commit()
    pools = T.material_pools(con)
    assert pools["qualification"]["count"] == 2 and pools["qualification"]["documents"] == 1
    assert pools["contracts"]["count"] == 0


# ============================================================================
# 2026-09-17 需求方第二次对接的需求2：**严格按指定标题结构生成**
# ============================================================================

_OUTLINE = {"title": "售后解决方案",
            "sections": ["售后服务团队", "售后服务方式", "整体技术支持服务方案", "服务质量承诺"]}


def test_parse_outline_accepts_dict_and_markdown():
    """outline 两种形态都收；**规整不出标题 → 空 dict**（调用方据此不施加约束，不猜）。"""
    assert P.parse_outline(_OUTLINE) == _OUTLINE
    md = P.parse_outline("# 售后解决方案\n## 售后服务团队\n## 售后服务方式")
    assert md == {"title": "售后解决方案", "sections": ["售后服务团队", "售后服务方式"]}
    plain = P.parse_outline("售后解决方案\n售后服务团队")
    assert plain == {"title": "售后解决方案", "sections": ["售后服务团队"]}
    assert P.parse_outline(None) == {} and P.parse_outline("") == {}
    assert P.parse_outline({"title": "  ", "sections": []}) == {}


def test_outline_block_is_injected_into_prompt():
    """提示词里必须出现**硬性结构块**（大标题 + 逐条小标题 + 顺序要求）。"""
    prompt = P.build_gen_prompt(_payload(), "", None, outline=_OUTLINE)
    assert "必须逐字遵守的标题结构" in prompt
    assert "售后解决方案" in prompt
    for s in _OUTLINE["sections"]:
        assert s in prompt
    # 不传 outline → 提示词里**不得**出现结构块（行为与加此参数前一致）
    assert "必须逐字遵守的标题结构" not in P.build_gen_prompt(_payload(), "")


def test_validate_outline_catches_drift_and_passes_on_exact():
    """漂移必须被报出来；逐字遵守（含模型自加的序号前缀）不得误报。"""
    payload = _payload()
    good = ("# 售后解决方案\n## 售后服务团队\n30 分钟响应 [E1]\n## 售后服务方式\n略\n"
            "## 整体技术支持服务方案\n略\n## 服务质量承诺\n略")
    assert P.validate_generation(good, payload, "", _OUTLINE) == []
    # 容忍模型自加序号（实测常见）：`## 1. 售后服务团队`
    numbered = good.replace("## 售后服务团队", "## 1、售后服务团队")
    assert P.validate_generation(numbered, payload, "", _OUTLINE) == []

    bad = "# 售后解决方案\n## 售后服务\n30 分钟响应 [E1]\n## 自造小节\n略"
    problems = P.validate_generation(bad, payload, "", _OUTLINE)
    assert any("缺少小标题" in p for p in problems)
    assert any("结构外的二级标题" in p for p in problems)

    wrong_title = good.replace("# 售后解决方案", "# 售后服务方案")
    assert any("大标题" in p for p in
               P.validate_generation(wrong_title, payload, "", _OUTLINE))
    no_title = good.replace("# 售后解决方案\n", "")
    assert any("缺少大标题" in p for p in
               P.validate_generation(no_title, payload, "", _OUTLINE))
    # 不传 outline → 校验器行为不变（不报结构类问题）
    assert not any("标题结构" in p for p in P.validate_generation(bad, payload, ""))


# ============================================================================
# 2026-09-18 需求方第二轮：**从 query 推输出结构**（大标题 + 小标题，不要多余内容）
# ============================================================================

def test_plan_sections_for_the_actual_request():
    """需求方原话必须拆成「大标题 售后服务方案 + 小标题 服务周期、应急预案」。

    ⚠️ 两个坑都是实测踩到的：
      · `售后服务方案` 里**没有**子串 `售后方案`（是「售后+服务+方案」）→ 必须按**关键词**定位，
        否则这句压根找不到大标题；
      · 「服务周期」在模块词表里**一个字都不命中** → 归属模块是**系统推的**（标记 `module_inferred`），
        不能因此把它丢掉（需求方要的就是这个小标题）。
    """
    p = P.plan_sections("售后服务方案，必须包含服务周期和应急预案")
    assert p["title"] == "售后服务方案" and p["title_module"] == "售后方案"
    assert [s["name"] for s in p["sections"]] == ["服务周期", "应急预案"]
    by = {s["name"]: s for s in p["sections"]}
    assert by["服务周期"]["module"] == "售后方案" and by["服务周期"]["module_inferred"] is True
    assert by["应急预案"]["module"] == "应急预案"
    # 小标题的判定词元必须**带上归属模块的关键词**（否则「服务周期」在历史章节里几乎召不到）
    assert "服务周期" in by["服务周期"]["terms"]
    assert any(k in by["服务周期"]["terms"] for k in P.MODULE_KEYWORDS["售后方案"])


def test_plan_sections_title_is_earliest_not_dict_order():
    """大标题取**句子里最早出现**的模块，不是词表顺序。

    反例（实测踩到）：`培训方案，必须包含讲师安排，保密方案` —— `保密方案` 在词表里更靠前，
    按词表顺序会把句尾的它当大标题，而用户显然说的是句首的「培训方案」。
    另：`售后服务方案和应急预案，…` 的大标题到「和」为止，不得吃成整串。
    """
    assert P.plan_sections("培训方案，必须包含讲师安排，保密方案")["title"] == "培训方案"
    assert P.plan_sections("售后服务方案和应急预案，必须包含服务周期")["title"] == "售后服务方案"
    # 句尾点名的模块仍要成为一个小节（不能因为它是模块名就被丢掉）
    p = P.plan_sections("质量控制方案，必须包含质控要求，保密方案")
    assert p["title"] == "质量控制方案"
    assert [s["name"] for s in p["sections"]] == ["质控要求", "保密方案"]


def test_plan_sections_reports_dropped_at_clause_granularity():
    """**点名但没纳入**的项要如实报出 —— 且粒度是**切分后的小标题**。

    ⚠️ 这是原 `unrecognized_requirements` 的缺陷（2026-09-18 实测）：
    它拿 `_REQ_CLAUSE` 抓到的**整片段**（`服务周期和应急预案`）去判，
    片段里任一模块命中就算「已认出」→ 片段里的「服务周期」被**连带跳过、一条告警都没有**。
    """
    from app.proposal import _split_req_clause
    q = "售后服务方案，必须包含服务周期和应急预案"
    assert _split_req_clause(q) == ["服务周期", "应急预案"]      # 切分符含「和」
    assert P.plan_sections(q)["dropped"] == []                    # 两项都被纳入 → 无告警
    # 挂不上任何模块、又没有大标题可依 → 不猜，如实报「未纳入」
    p = P.plan_sections("必须包含完全没听过的要求")
    assert p == {} or p.get("dropped") == ["完全没听过的要求"]


def test_plan_sections_returns_empty_when_nothing_recognized():
    """认不出 → 空 dict（调用方据此**不施加约束**，不猜）。"""
    assert P.plan_sections("帮我写个投标函") == {}
    assert P.plan_sections("") == {}


def test_custom_section_name_does_not_keyerror_in_evidence_packs(monkeypatch):
    """**用户自造小节名**（不在模块词表里）不得 KeyError。

    ⚠️ 实测踩到：`build_evidence_packs` 里若写成
    `section_map.get(mod, (mod, (mod,) + MODULE_KEYWORDS[mod]))`，
    **默认值是立即求值**的 → `MODULE_KEYWORDS['服务周期']` 直接 KeyError。
    """
    from app.api import readonly_db
    from app.proposal import build_evidence_packs
    # ⚠️ 必须用 `readonly_db()`（它设了 `row_factory=sqlite3.Row`）；
    # 裸 `sqlite3.connect` 的 `dict(row)` 在 tuple 上会 ValueError（踩过一次）。
    with readonly_db() as con:
        try:
            packs = build_evidence_packs(
                con, ["服务周期"], section_map={"服务周期": ("售后方案", ("服务周期", "售后"))})
        except Exception as exc:                   # noqa: BLE001
            raise AssertionError(f"自造小节名触发了异常：{type(exc).__name__}: {exc}")
    assert len(packs) == 1
    assert packs[0].module == "售后方案" and packs[0].display == "服务周期"


# ============================================================================
# 2026-09-18：标题结构**自动化**（需求方：「现在还需要用户自动填入」）
# ============================================================================

def test_plan_outline_endpoint_derives_from_query():
    """`/api/plan-outline?q=` 必须返回可**直接填入预览框**的大标题 + 小标题（零外发）。"""
    import app.api as A
    from fastapi.testclient import TestClient
    c = TestClient(A.app)
    d = c.get("/api/plan-outline",
              params={"q": "售后服务方案，必须包含服务周期和应急预案"}).json()
    assert d["planned"] is True
    assert d["title"] == "售后服务方案"
    assert d["sections"] == ["服务周期", "应急预案"]
    # 归属是系统推的必须回传（页面要如实标注）
    assert d["inferred"] == ["服务周期"]
    assert d["section_modules"][0] == {"name": "服务周期", "module": "售后方案"}
    # 前端**照这个顺序拼预览**：`[title, ...sections].join("\n")`
    assert "\n".join([d["title"], *d["sections"]]) == "售后服务方案\n服务周期\n应急预案"


def test_plan_outline_endpoint_accepts_manual_title_sections():
    """手选路径（`title=` + `sections=`）同样可用 —— 与自动路径共用同一响应形状。"""
    import app.api as A
    from fastapi.testclient import TestClient
    c = TestClient(A.app)
    d = c.get("/api/plan-outline", params={
        "title": "售后解决方案",
        "sections": "售后服务团队,售后服务方式,整体技术支持服务方案,服务质量承诺"}).json()
    assert d["planned"] is True and d["title"] == "售后解决方案"
    assert d["sections"] == ["售后服务团队", "售后服务方式", "整体技术支持服务方案", "服务质量承诺"]


def test_plan_outline_endpoint_is_honest_when_nothing_derived():
    """**拆不出就说拆不出**（`planned=false`）—— 不能假装填上了结构。

    页面据此显示「没能从这句话里拆出标题结构 —— 生成时会按系统模块名分节」。
    假装填上会让用户以为结构生效，实际没有。
    """
    import app.api as A
    from fastapi.testclient import TestClient
    c = TestClient(A.app)
    d = c.get("/api/plan-outline", params={"q": "帮我写个投标函"}).json()
    assert d["planned"] is False and d["sections"] == [] and d["title"] == ""
    assert c.get("/api/plan-outline", params={}).json()["planned"] is False


def test_auto_fill_does_not_overwrite_manual_edits():
    """前端**不得**用自动填入冲掉用户手改的内容（`_outlineDirty` 门）。

    行为级护栏：`autoFillOutline` 开头必须判 `_outlineDirty` 直接返回，
    且手改 `#proposal-outline` 会把该门置真。
    """
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    body = js.split("async function autoFillOutline()")[1].split("\n}")[0]
    assert "_outlineDirty" in body and body.index("_outlineDirty") < body.index("plan-outline"), \
        "autoFillOutline 必须在请求之前先判「用户是否手改过」"
    assert re.search(r'\$\("#proposal-outline"\)\?\.addEventListener\("input", \(\) => \{ _outlineDirty = true; \}\);', js), \
        "手改 #proposal-outline 必须置 _outlineDirty"
