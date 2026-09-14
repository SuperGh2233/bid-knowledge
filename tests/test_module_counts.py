"""护栏：**卡上的数字必须等于点进去的条数**。

背景（B2B 评审 P2，实测复现）：页签 01 概览卡写「仪器设备清单 546 条」，点进去 722 条。
根因是两处各写一份 `fact_type` 类型表 —— 概览那份漏了 `instrument_name` 与
`instrument_purchase_contract`。同一屏两个数都叫「有多少」，用户只会读成「数据不可信」。

同类缺陷还有一处：概览卡按 `COUNT(*) ... LIKE 'CTL-%'` 数合同（98），
侧栏与查询路径按白名单数（95）——**浏览与查询落在不同批文件上**。

本文件用合成库把这层一致性钉住：类型表被改窄、白名单被绕过，都会在这里失败。
不连 ES、不读真实库。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

import app.api as api  # noqa: E402
import app.config as app_config  # noqa: E402
import app.db as db  # noqa: E402

APPROVED = {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    """合成演示库：2 份已核对文档 + 1 份未核对文档（各带一份 CTL 合同）。"""
    path = tmp_path / "counts.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    con = db.connect()
    try:
        docs = [
            ("aaaaaaaaaaaa-01", "项目甲/合同/A.docx", "our_response"),
            ("bbbbbbbbbbbb-02", "项目乙/合同/B.docx", "our_response"),
            ("cccccccccccc-03", "项目丙/合同/C.docx", "contract_evidence"),  # 未核对
        ]
        for doc_id, rel, role in docs:
            con.execute(
                "INSERT INTO documents (document_id, source_root_id, project_folder, "
                "relative_path, document_role, content_format) VALUES (?,?,?,?,?,'docx')",
                (doc_id, "root1", rel.split("/")[0], rel, role))
        for n, doc_id in enumerate(("aaaaaaaaaaaa-01", "bbbbbbbbbbbb-02", "cccccccccccc-03"), 1):
            con.execute(
                "INSERT INTO contracts (contract_id, document_id, contract_number, "
                "contract_date, total_amount) VALUES (?,?,?,?,?)",
                (f"CTL-{n}", doc_id, f"NO-{n}", f"2025-0{n}-01", 10000.0 * n))
        # 仪器设备：6 类各一条。概览若再退回 4 类，本用例即失败。
        facts = [
            ("aaaaaaaaaaaa-01", "instrument_name", "Q Exactive"),
            ("aaaaaaaaaaaa-01", "instrument", "高分辨质谱仪"),
            ("aaaaaaaaaaaa-01", "purchase_contract", "仪器采购合同"),
            ("aaaaaaaaaaaa-01", "instrument_purchase_contract", "仪器采购合同"),
            ("aaaaaaaaaaaa-01", "invoice", "发票号 123"),
            ("aaaaaaaaaaaa-01", "instrument_photo", "仪器照片"),
            # 财务社保：2 类共 3 条
            ("aaaaaaaaaaaa-01", "finance_period", "2024-12"),
            ("bbbbbbbbbbbb-02", "finance_period", "2025-01"),
            ("aaaaaaaaaaaa-01", "social_security_month", "2025-02"),
        ]
        for doc_id, fact_type, value in facts:
            con.execute(
                "INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
                "VALUES (?,?,?,?)", (doc_id, fact_type, value, f"现附上{value}复印件"))
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr(api, "DEMO_DB", path)
    monkeypatch.setattr(api, "APPROVED_DOCUMENT_IDS", set(APPROVED))
    return path


def test_card_count_equals_detail_total(demo_db):
    """三类材料的卡上数字 == 点进去的总数（截断另有显式提示，不算矛盾）。"""
    overview = api.three_modules()["modules"]
    for name in overview:
        detail = api.three_modules(module=name, limit=1000)
        total = detail.get("total_available", detail["count"])
        assert overview[name]["count"] == total, (
            f"{name}：卡上 {overview[name]['count']}，点进去 {total} —— "
            "两处必须同源（概览与明细各写一份类型表/筛选条件就会这样分叉）")


def test_instrument_types_cover_all_six(demo_db):
    """仪器设备清单的概览计数必须覆盖 6 类（漏类正是 546 vs 722 的根因）。"""
    overview = api.three_modules()["modules"]["仪器设备清单"]
    assert overview["count"] == 6, f"实际 {overview['count']} —— 6 类各一条，应为 6"
    detail = api.three_modules(module="仪器设备清单", limit=1000)
    assert detail["total_available"] == 6
    assert set(detail["type_counts"]) == set(api._INSTRUMENT_FACT_TYPES)


def test_project_card_counts_only_approved_contracts(demo_db):
    """项目业绩卡 = 白名单内的 CTL 合同（与侧栏、查询路径同一批文件）。"""
    overview = api.three_modules()["modules"]["项目业绩"]
    detail = api.three_modules(module="项目业绩", limit=1000)
    assert overview["count"] == 2, f"库里 3 份 CTL，其中 1 份未核对 → 应为 2，实际 {overview['count']}"
    assert detail["count"] == 2
    assert {r["contract_id"] for r in detail["records"]} == {"CTL-1", "CTL-2"}


def test_project_card_matches_status_sidebar(demo_db):
    """卡上的「份合同」与侧栏「已完成核对、可以查询」必须是同一个数。"""
    with api.readonly_db() as con:
        scope = api.live_scope(con)
    assert scope["queryable_contracts"] == 2
    assert api.three_modules()["modules"]["项目业绩"]["count"] == scope["queryable_contracts"]
