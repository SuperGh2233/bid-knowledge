"""材料事实提取（「现附上…」确定性切片）单测。

重点验证：**模板占位符不得被当成真实期间**——这是对抗复核预警的误抓项。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract import extract_material_facts


def _facts(text):
    return extract_material_facts(text, "doc1")


def test_real_periods_are_extracted():
    text = (
        "☑企业适用：现附上我方（2023年度）财务报告复印件，包括资产负债表、利润表。\n"
        "现附上自2024年12月1日至2024年12月31日期间我方缴纳（包括但不限于税务机关出具的专用收据）等税收凭据复印件。\n"
        "现附上自2025年2月1日至2025年2月28日我方缴纳的社会保险凭据（限：税务机关/社会保障资金管理机关的专用收据）复印件。\n"
    )
    f = _facts(text)
    assert len(f) == 3, f
    kinds = {x["fact_type"] for x in f}
    assert kinds == {"finance_period", "social_security_month"}
    vals = {x["fact_value"] for x in f}
    assert "2023" in vals
    assert "2024-12-01~2024-12-31" in vals
    assert "2025-02-01~2025-02-28" in vals


def test_template_placeholders_are_skipped():
    """占位符一律不产出——实测同一文档里真值与占位并存。"""
    text = (
        "现附上我方（填写“具体的年度、或半年度、或季度”）财务报告复印件。\n"
        "现附上自  年  月  日至  年  月  日期间我方缴纳等税收凭据复印件。\n"
        "现附上我方银行：（填写“开户银行全称”）出具的资信证明复印件。\n"
    )
    assert _facts(text) == []


def test_declaration_without_period_is_skipped():
    """有声明句但取不到真实期间 → 不产出（宁缺毋滥），也不因无期间而误报。"""
    text = "现附上我方依法免税证明材料复印件，上述证明材料真实有效。\n"
    assert _facts(text) == []


def test_open_ended_period_is_captured():
    text = "现附上自2023年5月至今我方缴纳的社会保险凭据复印件。\n"
    f = _facts(text)
    assert f and f[0]["fact_value"] == "2023-05~", f


def test_non_declaration_lines_are_ignored():
    """普通承诺函复述资格条件，不是材料声明，不应产出。"""
    text = (
        "我公司具有良好的商业信誉和健全的财务会计制度；\n"
        "有依法缴纳税收和社会保障资金的良好记录；\n"
        "4、2024年度财务报告\n"
    )
    assert _facts(text) == []


# —— 自然问句 → fact_type 的映射顺序（实测踩过的坑）——

def test_photo_query_maps_to_instrument_photo_not_instrument():
    """「找仪器照片」必须映射到 `instrument_photo`。

    实测坑：映射按顺序取**第一个**命中的关键词，而「仪器照片」同时含「照片」和「仪器」。
    若 `仪器` 规则排在前面，问「仪器照片」会变成查 `instrument` —— 同一坑在
    `scripts/r4_sync_classified_facts.py` 的 `MAP` 里也犯过（`instrument_photo` 因此曾 **0 产出**）。
    """
    from app.api import parse_fact_query
    assert parse_fact_query("找仪器照片")[0] == "instrument_photo"
    assert parse_fact_query("看设备照片")[0] == "instrument_photo"
    assert parse_fact_query("有没有现场实拍")[0] == "instrument_photo"
    # 普通仪器查询不受影响
    assert parse_fact_query("找仪器")[0] == "instrument"
    assert parse_fact_query("找测序仪")[0] == "instrument"


# —— 期间覆盖判定（`'2021~'` 这类「起始式」）——

def test_period_covers_open_ended_start():
    """起始式期间：起始不晚于查询点即算覆盖（字典序即时间序）。"""
    from app.api import period_covers
    assert period_covers("2025-12", "2021~") is True
    assert period_covers("2021-06", "2021~") is True     # 同年更早的月份也算覆盖
    assert period_covers("2020-01", "2021~") is False    # 起始晚于查询点 → 不覆盖
    assert period_covers("2024-01", "2023-05~") is True
    assert period_covers("2021-06", "2023-05~") is False


def test_period_covers_closed_range_and_point():
    from app.api import period_covers
    assert period_covers("2024-06", "2024-01~2024-12") is True
    assert period_covers("2025-01", "2024-01~2024-12") is False
    # 点值不在此判定 —— 由精确 LIKE 负责，避免重复计入
    assert period_covers("2024", "2024") is False
    assert period_covers("", "2021~") is False
    assert period_covers("2025-12", "") is False


def test_month_query_returns_covering_periods_separately(monkeypatch):
    """月度查询无精确命中时，**起始式**条目单列到 `related`，不计入 `count`，
    且 scope_note 必须点破「未记终期、不能断言该月一定有」。"""
    import sqlite3
    from pathlib import Path as _P
    import app.api as A

    db = _P(__file__).resolve().parent / "_tmp_facts.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
        " source_root_id TEXT, project_folder TEXT, document_role TEXT);"
        "CREATE TABLE material_facts (document_id TEXT, fact_type TEXT, fact_value TEXT,"
        " evidence_text TEXT);")
    con.execute("INSERT INTO documents VALUES ('d1','P/a.docx','2026年','proj','our_response')")
    con.executemany("INSERT INTO material_facts VALUES (?,?,?,?)", [
        ("d1", "social_security_month", "2021~", "附所有员工社保缴纳记录（自2021年起）"),
        ("d1", "social_security_month", "2023-05~", "社保缴纳记录（2023年5月起）"),
        ("d1", "social_security_month", "2025-01", "2025年1月社保"),
    ])
    con.commit(); con.close()
    monkeypatch.setattr(A, "DEMO_DB", db)
    try:
        from fastapi.testclient import TestClient
        c = TestClient(A.app)
        d = c.get("/api/material-facts",
                  params={"fact_type": "social_security_month", "fact_value": "2025-12"}).json()
        assert d["count"] == 0                                  # 精确无命中
        assert {r["fact_value"] for r in d["related"]} == {"2021~", "2023-05~"}
        assert "未记终期" in d["scope_note"] and "人工核对" in d["scope_note"]
        # 查询点早于某条起始式 → 该条不得出现
        d2 = c.get("/api/material-facts",
                   params={"fact_type": "social_security_month", "fact_value": "2021-06"}).json()
        assert {r["fact_value"] for r in d2["related"]} == {"2021~"}
        # 精确命中时 related 不与 facts 重复
        d3 = c.get("/api/material-facts",
                   params={"fact_type": "social_security_month", "fact_value": "2025-01"}).json()
        assert d3["count"] == 1
        assert all(r["fact_value"] != "2025-01" for r in d3["related"])
        # 宽泛查询（非年/月）不产生 related 组
        d4 = c.get("/api/material-facts", params={"fact_type": "social_security_month"}).json()
        assert d4["related_count"] == 0
    finally:
        db.unlink(missing_ok=True)


