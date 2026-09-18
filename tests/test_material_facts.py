"""材料事实提取（「现附上…」确定性切片）单测。

重点验证：**模板占位符不得被当成真实期间**——这是对抗复核预警的误抓项。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract import extract_material_facts, scan_periods


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
        " source_root_id TEXT, project_folder TEXT, document_role TEXT,"
        " content_format TEXT);"          # ⚠️ 真实 schema 有它：端点 SELECT 含 `d.content_format`
        "CREATE TABLE material_facts (document_id TEXT, fact_type TEXT, fact_value TEXT,"
        " evidence_text TEXT);")
    con.execute("INSERT INTO documents (document_id, relative_path, source_root_id,"
                " project_folder, document_role, content_format)"
                " VALUES ('d1','P/a.docx','2026年','proj','our_response','native_text')")
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




# —— scan_periods：期间扫描（三类材料定位用）——
# 由来（2026-09-15 需求方反馈）：郑州市中心医院那份的正文里有法定代表人授权书
# `本授权书有效期限为：2025年3月14日至2026年3月14日`，到期日被抽成
# `social_security_month=2026-03`（伪造值）—— 授权书期限不是社保缴纳月份。

def test_scan_periods_ignores_validity_period_context():
    """**授权书有效期不得当成材料期间**（本次修的真实 bug）。"""
    text = "法定代表人姓名：王树伟\n本授权书有效期限为：2025年3月14日至2026年3月14日，特此声明。"
    # 授权书里的两个日期都被剔除 → 无候选（宁缺毋滥）
    assert scan_periods("响应文件.docx", text) == []
    assert scan_periods("响应文件-20250311.docx", text) == []
    # 对照：同样文本若把「有效期」去掉，日期即被采（证明确实是上下文过滤在起作用）
    assert scan_periods("x.docx", "期限为：2025年3月14日至2026年3月14日") == ["2025-03", "2026-03"]


def test_scan_periods_keeps_real_declaration_period():
    """正常社保声明句的期间**不得被误伤**。"""
    text = "现附上自2025年2月1日至2025年2月28日我方缴纳的社会保险凭据复印件。"
    assert scan_periods("社保.docx", text) == ["2025-02"]


def test_scan_periods_rejects_implausible_years():
    """区间外年份（如 2046 / 2009）一律剔除 —— 实测脏值来源。"""
    assert scan_periods("x.docx", "合同期限至2046年6月，财务报告2024年度") == ["2024"]
    assert scan_periods("y.docx", "成立于2009年3月") == []
    # 全区间外 → 空（宁缺毋滥）
    assert scan_periods("z.docx", "有效期至2029年3月") == []


def test_scan_periods_falls_back_to_year_then_range():
    """月份优先 → 年度 → 年度区间（**顺序即优先级**）。"""
    assert scan_periods("a.docx", "（2023年度）财务报告") == ["2023"]
    # 带「年」时年度分支先命中，只取 2023（不是区间）—— 既有行为，此处钉住
    assert scan_periods("b.docx", "2021-2023 年审计") == ["2023"]
    # 不带「年」才落到区间分支
    assert scan_periods("c.docx", "2021-2023 审计") == ["2021", "2023"]


# —— R1-3 仪器「通称 → 型号」检索（2026-09-15 需求方反馈）——
# 由来：业务按**通称**搜仪器（「找质谱仪」「测序仪」），而库里存的是**具体型号**
# （`instrument_name`：`Bruker timsTOF HT`/`TapeStation 4200`…）。
# 此前 `质谱仪` **完全不被识别**；`测序仪` 被 `_FACT_KW` 抢先归到 `instrument`（那类存**期间**）。

def test_instrument_category_maps_to_real_models():
    """通称 → 型号关键词：**词表必须来自库内真实型号**，不是凭空编的。"""
    from app.api import INSTRUMENT_ALIASES
    assert "质谱仪" in INSTRUMENT_ALIASES
    assert "Bruker" in INSTRUMENT_ALIASES["质谱仪"]
    assert "TapeStation" in INSTRUMENT_ALIASES["生物分析仪"]
    # 词表条目不得以 `_` 开头（那是 JSON 里的说明字段，加载时应被过滤掉）
    assert all(not k.startswith("_") for k in INSTRUMENT_ALIASES)


def test_resolve_instrument_query_prefers_category_then_model():
    """通称命中优先；直接写型号也能命中；识别不出返回空（不猜）。"""
    from app.api import resolve_instrument_query
    got = resolve_instrument_query("找质谱仪")
    assert "Bruker" in got and "Orbitrap" in got
    assert set(resolve_instrument_query("找生物分析仪")) == {"Bioanalyzer", "TapeStation"}
    assert resolve_instrument_query("找完全不相干的东西") == ()


def test_generic_instrument_query_keeps_legacy_mapping():
    """⚠️ 泛问「找仪器」**保持**原有映射（→ `instrument`），不被本此改动带走。

    `instrument` 存的是**期间**、`instrument_name` 才是型号 —— 泛问走哪条是既有口径；
    本此只新增「通称/型号 → instrument_name」这一路，不动泛问（有既有测试钉住）。
    """
    from app.api import parse_fact_query, resolve_instrument_query
    assert resolve_instrument_query("找仪器") == ()          # 泛问不解析成型号（不越权改口径）
    assert parse_fact_query("找仪器")[0] == "instrument"


# —— 社保月份不得来自「招标要求句 / 未来日期」（2026-09-16 需求方第二轮反馈）——

def test_periods_must_not_be_after_project_date():
    """**材料期间不得晚于项目日期** —— 证书/身份证的**有效期**紧邻社保段落时会被收进来。

    实测：2025-10 的项目报出 `2026-06`、2026-08 的项目报出 `2028-04`（全是未来社保月份）。
    项目目录名自带登记日期（`20260814-…`），用它做独立校验。`margin_months=3` 留给
    「登记日 → 实际投标」的正常间隔（实测 23 例只差 1 个月，属正常）。
    """
    from app.extract import filter_periods_by_project_date as F
    assert F(["2028-04", "2026-06", "2027-05"], "20260814-标书-王玮-徐州") == ["2026-06"]
    assert F(["2026-07", "2026-08", "2026-11"], "20260814-标书-X") == ["2026-07", "2026-08", "2026-11"]
    # 取不到项目日期 → **不筛**（宁可不筛，不可误删）
    assert F(["2030-01"], "无日期目录") == ["2030-01"]
    assert F(["2030-01"], "") == ["2030-01"]
    # None（期间未知）原样保留
    assert F([None, "2028-01"], "20260814-X") == [None]


# —— 期间必须与材料关键词「同一行」（2026-09-16 需求方第三轮反馈：不能只改个例）——

def test_scan_periods_near_same_line_excludes_nearby_signature_dates():
    """**宽标记必须走「同一行」** —— 否则同段里的「签署日期」会被当成社保月份。

    实测（第三轮）：2025-12 的项目报出 `2026-01`，来源是同一段里的 `日期：2026年1月4日`；
    统一 ±150 窗口下实测**约 650 条**是「签署时间/日期」，只有 0–49 字那批是真声明期间。
    """
    from app.extract import scan_periods_near, SS_MARKERS
    text = ("3、法定代表人身份证复印件、被授权人公司社保缴纳证明；\n"
            "日期：2026年1月4日\n")                     # 要求句无日期；签署日期在**另一行**
    assert scan_periods_near(text, SS_MARKERS, same_line=True) == []
    # 同段窗口（默认）会把它收进来 —— 这正是要避免的
    assert scan_periods_near(text, SS_MARKERS) == ["2026-01"]
    # 真声明句：期间与关键词**同行** → 正常取到
    dec = "现附上自2025年2月1日至2025年2月28日我方缴纳的社会保险凭据复印件。"
    assert scan_periods_near(dec, SS_MARKERS, same_line=True) == ["2025-02"]


def test_fold_facts_by_file_merges_periods():
    """同一份文件的多条期间**折成一条**（2026-09-16 需求方反馈「返回很多相同的文件」）。"""
    from app.api import _fold_facts_by_file
    rows = [{"relative_path": "P/a.docx", "fact_value": "2026-05", "fact_type": "social_security_month"},
            {"relative_path": "P/a.docx", "fact_value": "2026-04", "fact_type": "social_security_month"},
            {"relative_path": "P/b.docx", "fact_value": None, "fact_type": "social_security_month"}]
    out = _fold_facts_by_file(rows)
    assert [r["relative_path"] for r in out] == ["P/a.docx", "P/b.docx"]   # 保序、去重
    assert out[0]["fact_values"] == ["2026-04", "2026-05"]                 # 期间按时间序
    assert out[0]["record_count"] == 2
    assert out[1]["fact_values"] == [None]                                 # 期间未知如实保留


# ============================================================================
# 2026-09-17 第二次需求对接新增：可复制正文段 / 空壳行沉底 / 凭证合计金额
# ============================================================================

def test_cut_snippet_uses_strong_anchor_and_returns_original():
    """`content_snippet` 必须**按材料自身标题**裁原文；找不到锚点如实返回空。"""
    from app.api import cut_snippet, SNIPPET_ANCHORS
    text = "目录\n第一章 …\n社会保险费缴费记录\n缴费人名称：某公司\n2025-10至2025-10 351,727.90\n" + "x" * 800
    snip = cut_snippet(text, SNIPPET_ANCHORS["social_security_month"])
    # 锚点必须在段内；起点对齐**换行边界**（允许带一行上文，避免把半句话切出来）
    assert "社会保险费缴费记录" in snip and "351,727.90" in snip
    assert snip.startswith("第一章") or snip.startswith("社会保险费缴费记录")
    assert "目录" not in snip                       # 不会从头吞整篇
    assert cut_snippet(text, ("根本不存在的锚点",)) == ""  # 找不到 → 空（不猜）


def test_shell_row_sql_matches_python_predicate():
    """**两处判据必须等价**：SQL `SHELL_ROW_SQL` vs `_annotate_fact_role` 的 Python 判据。

    分开写死过正是本仓库反复踩的坑（概览卡 546 vs 点进去 722）。这里用合成库钉住等价性。
    """
    import sqlite3
    from app.api import SHELL_ROW_SQL, _annotate_fact_role
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE documents (document_id TEXT, relative_path TEXT, document_role TEXT);"
        "CREATE TABLE material_facts (document_id TEXT, fact_type TEXT, fact_value TEXT,"
        " evidence_text TEXT);")
    con.execute("INSERT INTO documents VALUES ('d1','P/甲.docx','our_response')")
    con.executemany("INSERT INTO material_facts VALUES (?,?,?,?)", [
        ("d1", "instrument", None, "[our_response] 甲.docx"),        # 空壳
        ("d1", "social_security_month", None, "[our_response] 甲.docx"),  # 空壳
        ("d1", "instrument_name", "Q Exactive", "[our_response] 型号 Q Exactive 2台"),  # 有内容
        ("d1", "qualification", None, "[our_response] 营业执照编号 123"),             # 有内容
    ])
    sql = list(con.execute(
        f"SELECT {SHELL_ROW_SQL} FROM material_facts f JOIN documents d "
        "ON d.document_id=f.document_id"))
    py = []
    for ev, ft, fv in con.execute("SELECT evidence_text, fact_type, fact_value FROM material_facts"):
        r = {"document_role": "our_response", "evidence_text": ev, "file_name": "甲.docx",
             "fact_type": ft, "fact_value": fv}
        _annotate_fact_role(r)
        py.append(int(bool(r["evidence_is_filename"])))
    assert [s[0] for s in sql] == py == [1, 1, 0, 0], (sql, py)
    con.close()


def test_find_voucher_total_is_conservative():
    """凭证合计金额判据：**必须有税务机关痕迹 + 合计锚点**，否则不产出（宁缺毋滥）。

    ⚠️ 反例（实测漏网）：高德打车电子发票同样有 `价税合计`，但**没有税务机关痕迹** ——
    不挡就会把打车费当成「纳税金额」。明细行的数字（`4,226.88`）也不得被当合计。
    """
    from app.extract import find_voucher_total
    # 形态①：税务完税证明（¥ 紧邻）
    a = "税种 … 实缴（退）金额\n1,000.00\n金额合计\n（大写）人民币贰拾柒万贰仟叁佰壹拾圆肆角肆分\n¥272310.44\n税务机关"
    assert find_voucher_total(a) == ("272310.44", "¥")
    # 形态②：社保完税凭证（大写行换行后取数）
    b = "金额合计（大写）叁万肆仟肆佰贰拾元零玖角贰分\n34,420.92\n税务机关"
    assert find_voucher_total(b) == ("34,420.92", "大写行")
    # 反例①：普通发票（无税务机关/社保痕迹）→ 不产出
    c = "价税合计（大写）壹佰贰拾柒圆叁角肆分\n¥ 127.34\n开票人：张玲"
    assert find_voucher_total(c) is None
    # 反例②：有机构痕迹但无合计锚点（社保缴费记录表，只有明细）→ 不产出，**不按明细求和**
    d = "社会保险费缴费记录\n基本医疗保险费 2025-10 ¥ 351,727.90\n企业职工基本养老保险费 ¥331,037.60\n上海市社会保险事业管理中心"
    assert find_voucher_total(d) is None
    assert find_voucher_total("") is None


def test_finance_amount_query_maps_before_social_security():
    """「纳税社保总金额」必须落到 `finance_amount`（**金额**），不能被「社保」抢先归到**期间**类。"""
    from app.api import parse_fact_query
    assert parse_fact_query("纳税社保总金额")[0] == "finance_amount"
    assert parse_fact_query("社保总金额")[0] == "finance_amount"
    assert parse_fact_query("完税总额")[0] == "finance_amount"
    # 既有的期间类问法一条不动
    assert parse_fact_query("2025年的社保")[0] == "social_security_month"
    assert parse_fact_query("找仪器照片")[0] == "instrument_photo"


def test_finance_amount_rows_carry_amount_note():
    """`finance_amount` 行**必须带 `amount_note`** —— 不给口径说明会被读成「合同金额」。

    与业绩行（`amount_note` 写明「合同总额、非产品明细金额」）同一诚实性原则：
    凭证上的合计数字与合同金额**毫无关系**，上屏时必须说清楚。
    """
    from app.api import _annotate_fact_role
    r = {"document_role": "qualification_evidence", "evidence_text": "[qualification_evidence] 完税证明.pdf",
         "file_name": "完税证明.pdf", "fact_type": "finance_amount", "fact_value": "272310.44"}
    _annotate_fact_role(r)
    assert "amount_note" in r and "非合同金额" in r["amount_note"] and "不参与金额筛选" in r["amount_note"]
    # 其他材料类型**不得**被加上这条（口径说明只对金额类有意义）
    r2 = {"document_role": "our_response", "evidence_text": "[our_response] 社保.docx",
          "file_name": "社保.docx", "fact_type": "social_security_month", "fact_value": "2025-02"}
    _annotate_fact_role(r2)
    assert "amount_note" not in r2


def test_both_fact_endpoints_annotate_identically(monkeypatch):
    """**两个端点必须用同一套行标注** —— 分叉过一次，后果很具体（2026-09-18 实测）。

    由来：`/api/three-modules` 原先**就地手写**了一遍角色分层，与 `/api/material-facts`
    调用的 `_annotate_fact_role` 分叉，后果是
      ① `evidence_text` 里的内部枚举前缀（`[our_response] …`）**没被剥掉** ——
         645/645 条仪器行的正文证据带着英文枚举上屏（同 2026-09-14 评审 P0 的老问题）；
      ② `finance_amount` 行的 `amount_note`（「非合同金额」口径说明）**整段丢失** ——
         那正是防把凭证金额误读成合同金额的关键说明。
    本测试用合成库钉住：两条路径产出的关键字段**逐字相同**。
    """
    import sqlite3
    import app.api as A
    app = A

    db = Path(__file__).resolve().parent / "_tmp_annotate.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
        " source_root_id TEXT, project_folder TEXT, document_role TEXT, content_format TEXT);"
        "CREATE TABLE material_facts (document_id TEXT, fact_type TEXT, fact_value TEXT,"
        " evidence_text TEXT);")
    con.execute("INSERT INTO documents VALUES "
                "('d1','P/完税证明.pdf','2026年','proj','qualification_evidence','native_pdf_text')")
    con.executemany("INSERT INTO material_facts VALUES (?,?,?,?)", [
        ("d1", "finance_amount", "272310.44",
         "[qualification_evidence] 完税证明.pdf｜合计金额（判据：¥）：…¥272,310.44…"),
        # 空壳行：evidence 只是 `[角色] 文件名` → 必须被判为「无正文证据」
        ("d1", "instrument", None, "[qualification_evidence] 完税证明.pdf"),
    ])
    con.commit(); con.close()
    monkeypatch.setattr(A, "DEMO_DB", db)
    try:
        from fastapi.testclient import TestClient
        c = TestClient(A.app)
        # 两个端点各取一次同类行：金额类用自然问句（同时验证查询映射），仪器类用精确参数
        one = (c.get("/api/material-facts", params={"q": "纳税社保总金额"}).json()["facts"]
               + c.get("/api/material-facts", params={"fact_type": "instrument"}).json()["facts"])
        # 仪器那一类在「仪器设备清单」模块下（三模块按类别分端点，不多不少）
        three = (c.get("/api/three-modules", params={"module": "财务社保数据", "limit": 50}).json()["records"]
                 + c.get("/api/three-modules", params={"module": "仪器设备清单", "limit": 50}).json()["records"])
        by_type_one = {r["fact_type"]: r for r in one}
        by_type_three = {r["fact_type"]: r for r in three}
        for ft in ("finance_amount", "instrument"):
            a, b = by_type_one.get(ft), by_type_three.get(ft)
            assert a and b, (by_type_one.keys(), by_type_three.keys())
            # ① 内部枚举前缀必须被剥掉
            assert not (a["evidence_text"] or "").startswith("[")
            assert not (b["evidence_text"] or "").startswith("["), b["evidence_text"]
            # ② 三个标注字段逐字一致
            for field in ("role_scope", "role_label", "evidence_is_filename"):
                assert a[field] == b[field], (ft, field, a[field], b[field])
        # ③ 金额行两处都带口径说明（防误读为合同金额）
        for r in (by_type_one["finance_amount"], by_type_three["finance_amount"]):
            assert "非合同金额" in r.get("amount_note", ""), r.get("amount_note")
        # ④ 空壳行两处都被判为「无正文证据」
        assert by_type_one["instrument"]["evidence_is_filename"] is True
        assert by_type_three["instrument"]["evidence_is_filename"] is True
    finally:
        db.unlink(missing_ok=True)
