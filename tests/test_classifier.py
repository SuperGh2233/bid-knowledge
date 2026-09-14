"""表驱动测试 V2：classify(project_folder, rel) 显式接口 + classify_path 兼容。
覆盖：独立目录段（父段全段等值）、项目名不参与、文件名供应商、冲突 ambiguous、
报价表随目录、响应关键词、招标/合同/资质、系统文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.classifier import classify, classify_path, confirm_our_from_content  # noqa: E402


def _c(rel: str, project: str = "项目") -> str:
    return classify(project_folder=project, relative_path_within_project=rel).role


def test_table_driven():
    cases = [
        # (rel, project, expected)
        # 1) 独立供应商目录
        ("吉凯/01-响应文件.docx", "项目", "competitor_response"),
        ("百趣/报价表.pdf", "项目", "competitor_response"),
        ("中科/商务技术文件.docx", "20250520-标书-程耀-…", "competitor_response"),
        ("中科新/盖章扫描件.pdf", "20260318-标书-和笑笑-…", "unknown"),  # “中科新”≠“中科”，且文件名无供应商词 → unknown
        # 2) 项目名含竞品 ≠ 竞品目录；我方目录=our
        ("欧易/响应文件.docx", "20260611-标书-中科-河南中医药…", "our_response"),
        # 3) 冲突 → unknown + vendor_conflict（v3 语义：不由 ambiguous 角色，以 unknown+reason 表达）
        ("欧易---投标文件 6.26 拜谱.docx", "20260731-…拜谱…", "unknown"),
        ("华大Stereo-seq-欧易生物.docx", "20260831-…华大…", "unknown"),
        ("吉凯/欧易响应文件.docx", "项目", "unknown"),   # 竞品目录+文件名我方 → vendor_conflict
        # 4) 竞品目录中的报价表
        ("百趣/03-最后报价表-签字盖章.docx", "项目", "competitor_response"),
        ("中科/报价单.pdf", "项目", "competitor_response"),
        # 5) 文件名供应商（项目内相对，父目录无竞品）
        ("响应文件-诺禾.pdf", "项目", "competitor_response"),
        ("响应文件-欧易.pdf", "项目", "our_response"),
        # 6) 响应关键词（无供应商）
        ("响应文件.docx", "20250326-标书-张烨-…", "our_response"),
        ("商务技术文件.docx", "20250520-…", "our_response"),
        # 7) 招标/合同/资质/回单
        ("招标文件.pdf", "项目", "tender_requirement"),
        ("合同/BOE2025110208-多组学-2720000.pdf", "项目", "contract_evidence"),
        ("合同/营业执照.pdf", "项目", "qualification_evidence"),
        ("银行回单/BOE2025110208.png", "项目", "process_material"),
        # 8) 系统
        ("~$报名.docx", "项目", "system_or_temp"),
        ("Thumbs.db", "项目", "system_or_temp"),
    ]
    failed = []
    for rel, project, expected in cases:
        got = _c(rel, project)
        if got != expected:
            failed.append((rel, project, expected, got))
    assert not failed, "\n".join(f"{p!r}/{r!r}: expect {e}, got {g}" for r, p, e, g in failed)


def test_project_name_not_used_for_vendor():
    """项目名中科不参与供应商判定；中科必须是独立目录段。"""
    assert _c("中科/商务技术文件.docx", "20260611-标书-中科-河南…") == "competitor_response"
    assert _c("欧易/响应文件.docx", "20260611-标书-中科-河南…") == "our_response"
    assert _c("响应文件.docx", "20260611-标书-中科-河南…") == "our_response"  # 无竞品目录段


def test_dir_segment_exact_not_substring():
    """“中科没用/中科新”≠“中科”；全段等值才命中竞品目录。"""
    assert _c("中科/商务技术文件.docx", "项目") == "competitor_response"
    assert _c("中科没问题/商务技术文件.docx", "项目") != "competitor_response"
    assert _c("中科新/盖章扫描件.pdf", "项目") != "competitor_response"


def test_conflict_never_forced():
    """冲突必须进 unknown + vendor_conflict，不强归类。"""
    r1 = classify(project_folder="项目", relative_path_within_project="吉凯/欧易响应文件.docx")
    assert r1.role == "unknown" and r1.conflict is True
    r2 = classify(project_folder="项目", relative_path_within_project="欧易响应文件-拜谱.docx")
    assert r2.role == "unknown" and r2.conflict is True


def test_classify_path_compat():
    """旧式全路径兼容：首段当 project、其余当 rel。"""
    assert classify_path("20260611-标书-中科-河南…/欧易/响应文件.docx").role == "our_response"
    assert classify_path("20250520-标书-程耀-…/中科/商务技术文件.docx").role == "competitor_response"


def test_r3_semantics():
    """任务3 拍板语义固化为表驱动。"""
    cases = [
        ("商务技术部分.pdf", "普通项目", "our_response"),
        ("资格证明分册.pdf", "普通项目", "our_response"),
        ("吉凯/商务技术部分.pdf", "普通项目", "competitor_response"),   # 竞品目录优先
        ("项目推介文件.docx", "调研项目", "unknown"),                     # 推介不猜
        ("欧易---投标文件 拜谱.docx", "普通项目", "unknown"),              # 冲突 → unknown(vendor_conflict)
        ("欧易/响应文件.docx", "20251127-标书-诺禾-…", "our_response"),    # 项目名含诺禾不参与; 我方目录
        ("响应文件-诺禾.docx", "普通项目", "competitor_response"),         # basename 仅含诺禾
    ]
    failed = []
    for rel, project, expected in cases:
        got = classify(project_folder=project, relative_path_within_project=rel).role
        if got != expected:
            failed.append((rel, project, expected, got))
    assert not failed, "\n".join(f"{p!r}/{r!r}: expect {e}, got {g}" for r, p, e, g in failed)


def test_content_confirm_overlay():
    """内容确认覆盖层：明确我方主体的首页证据才能确证响应。"""
    assert confirm_our_from_content("投标单位：上海欧易生物医学科技有限公司 地址：…") is True
    assert confirm_our_from_content("供应商名称：上海欧易") is True
    assert confirm_our_from_content("拜谱生物 投标文件") is False          # 无我方主体
    assert confirm_our_from_content("上海欧易 采购邀请函") is False         # 有欧易但无投标人/供应商等语境


def test_final_signed_requires_response_identity_and_final_state():
    cases = [
        ("欧易/响应文件盖章版.pdf", "final_signed"),
        ("鹿明/正本扫描件.pdf", "final_signed"),
        ("上海欧易投标文件 正本.pdf", "final_signed"),
        ("欧易生物投标文件 扫描盖章.pdf", "final_signed"),
        ("上海欧易投标文件 定稿版.docx", "final_signed"),
        ("社保凭证/欧易生物盖章扫描件_00.jpg", "qualification_evidence"),
        ("欧易/社保凭证/盖章版.pdf", "qualification_evidence"),
        ("欧易/合同签章页.pdf", "contract_evidence"),
        ("合同/欧易盖章版.pdf", "contract_evidence"),
        ("合同/欧易投标文件盖章版.pdf", "final_signed"),
        ("欧易专用发票盖章版.pdf", "process_material"),
        ("欧易/外包装封皮（正本1份）.docx", "process_material"),
        ("02 投标文件封皮 正本1份副本5份.docx", "process_material"),
        ("附件5报价单（提前盖章，现场填写）欧易.docx", "process_material"),
        ("欧易/盖章说明.docx", "process_material"),
        ("招标文件/欧易盖章要求.docx", "tender_requirement"),
        ("上海欧易投标文件-待定稿.docx", "our_response"),
        ("欧易/投标文件盖章版作废.pdf", "our_response"),
        ("欧易/过往作废/响应文件正本.pdf", "our_response"),
        ("欧易/响应文件未盖章.pdf", "our_response"),
        ("欧易生物盖章扫描件（多家公司关联）.pdf", "unknown"),
        ("欧易生物盖章扫描件（多家公司关联）_00.jpg", "unknown"),
        ("欧易生物盖章扫描件.pdf", "unknown"),
        ("盖章版.pdf", "unknown"),
        ("拜谱/正本扫描件.pdf", "competitor_response"),
        ("响应文件-诺禾盖章版.pdf", "competitor_response"),
    ]
    failures = [(rel, expected, _c(rel)) for rel, expected in cases if _c(rel) != expected]
    assert not failures, str(failures)
    assert _c("投标文件盖章版.pdf") != "final_signed"  # 通用响应词不证明供应商身份


def test_final_mark_cannot_bypass_vendor_conflicts():
    for rel in (
        "吉凯/欧易响应文件盖章版.pdf",
        "欧易/诺禾响应文件正本.pdf",
        "欧易投标文件盖章版-拜谱.pdf",
        "欧易/中科/投标文件正本.pdf",
    ):
        result = classify("项目", rel)
        assert result.role == "unknown" and result.conflict, (rel, result)


def test_final_rule_does_not_override_manual_decision():
    result = classify("项目", "欧易/响应文件盖章版.pdf", manual_override=True)
    assert result.role == "unknown" and result.reason.startswith("manual_override")


def test_final_audit_preserves_candidates_without_self_scoring():
    from audit_final_signed import audit_records

    names = ["社保凭证/欧易盖章版.jpg", "欧易/响应文件盖章版.pdf", "招标文件定稿版.pdf"]
    inventory = {
        "source_roots": [r"\\nas\bids\2026年"],
        "projects": [{"root_id": "2026年", "project_folder": "项目", "files": [
            {"rel": name, "name": name.split("/")[-1]} for name in names
        ]}],
    }
    catalog = [{
        "document_id": str(i), "source_root_id": "2026年", "relative_path": "项目/" + name,
        "document_role": "final_signed" if i < 2 else "tender_requirement", "manual_override": 0,
    } for i, name in enumerate(names)]
    report = audit_records(inventory, catalog)
    assert report["registered_final_signed_count"] == 2
    assert report["filename_trigger_candidate_count"] == 3
    assert report["baseline_final_new_roles"] == {"qualification_evidence": 1, "final_signed": 1}
    assert len(report["other_candidates"]) == 1
    assert report["metrics"]["precision"] is None
    assert all(not row["scoring_enabled"] for row in report["baseline_final_signed"])
    assert report["baseline_final_signed"][0]["source_path"] == r"\\nas\bids\2026年\项目\社保凭证\欧易盖章版.jpg"
