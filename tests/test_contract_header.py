"""合同头字段（编号／甲方／乙方／合同总额）提取回归测试。

全部用例取自**实测文本**（98 份 CTL 合同全量回放时发现的坑），不是构造的理想输入。
每条断言背后都有一个具体误抓/漏抓案例，改 `_PARTY`/`_TEXT_TOTAL_*` 前先看这里。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract import contract_header_facts, extract_contract_date, _pick_best_date  # noqa: E402


def test_number_and_amount_from_filename():
    """编号与总额**优先取文件名**：人工命名＝成交价（项目 CLAUDE.md 约定）。"""
    h = contract_header_facts("", "ZOE2025010001-代谢组-浙江大学-100,000.00.pdf")
    assert h["contract_number"] == "ZOE2025010001"
    assert h["total_amount"] == 100000.00


def test_filename_amount_beats_text_declaration():
    """文件名金额优先于正文表价（正文常是优惠前标价，实测 2 处 48,380 vs 50,000）。"""
    h = contract_header_facts("合同总金额为：人民币50000元",
                              "ZOE2025010001-代谢组-浙江大学-48,380.00.pdf")
    assert h["total_amount"] == 48380.00


def test_two_column_header_does_not_capture_neighbour_label():
    """两栏表头 `需方（甲方） | 供方（乙方）` 不得把**隔壁列的标签**当成甲方。

    实测案例：YOE2024112104-BD Rhapsody-陆军军医大学-180,000.00.pdf
    曾被抓成 `party_a='供方（乙方）'`——标签不是单位名，取错比不取更糟。
    """
    h = contract_header_facts(
        "第 1 条 合同当事人\n"
        "需方（甲方） | 供方（乙方）\n"
        "单位名称（公章）：陆军军医大学药学与检验医学系 | 单位名称（公章）：上海欧易生物医学科技有限公司\n",
        "YOE2024112104-BD Rhapsody-陆军军医大学-180,000.00.pdf")
    assert h["party_a"] != "供方（乙方）"
    assert h["party_a"] is None          # 该布局的甲方在 `单位名称（公章）：` 列，当前不解析 → 诚实留空
    assert h["total_amount"] == 180000.00


def test_party_value_must_look_like_org():
    """付款条款不得被当成单位名（实测：`下方式支付给乙方： 1.4.1分首尾款2期支付`）。"""
    h = contract_header_facts("下方式支付给乙方： 1.4.1分首尾款2期支付", "x.pdf")
    assert h["party_b"] is None


def test_party_with_chinese_parenthetical():
    """`甲方（使用部门）：X` 这类**中文括注**必须能取到（实测漏抓 1 例）。

    案例：YOE2025010758-Pro DIA定量蛋白质组-昆明理工大学-551,700.00.pdf
    """
    h = contract_header_facts(
        "甲方（使用部门）：昆明理工大学灵长类转化医学研究院\n"
        "乙方（供应商）：上海欧易生物医学有限公司\n",
        "YOE2025010758-Pro DIA定量蛋白质组-昆明理工大学-551,700.00.pdf")
    assert h["party_a"] == "昆明理工大学灵长类转化医学研究院"
    assert h["party_b"] == "上海欧易生物医学有限公司"


def test_party_plain_form():
    h = contract_header_facts("甲方：浙江大学\n乙方：上海欧易生物医学科技有限公司", "x.pdf")
    assert h["party_a"] == "浙江大学"
    assert h["party_b"] == "上海欧易生物医学科技有限公司"


def test_percentage_in_payment_clause_is_not_total():
    """付款条款里的百分比不得当成合同总额。

    实测反例：`向乙方支付合同总金额的 100 %，即人民币 180000 元` —— 不要求「元」时
    会把 `100` 抓成总额 **100 元**。收紧后该句取不到值，这是**刻意的**：
    「即人民币X元」不在「关键词 + ≤14 字 + 数字 + 元」的声明式窗口内，
    属已知边界；实务上这类合同的文件名带金额（92/98），由文件名通道兜住。
    """
    text = "向乙方支付合同总金额的 100 %，即人民币 180000 元"
    assert contract_header_facts(text, "合同.pdf")["total_amount"] is None
    # 只有百分比、无「元」金额时同样不取（不得回落到 100）
    assert contract_header_facts("向乙方支付合同总金额的 100 %", "合同.pdf")["total_amount"] is None


def test_text_declaration_and_table_row_forms():
    assert contract_header_facts("合同总金额为：人民币732000元", "x.pdf")["total_amount"] == 732000.00
    assert contract_header_facts("合同总金额（元） | 199,800.00", "x.pdf")["total_amount"] == 199800.00


def test_missing_fields_stay_none():
    """取不到一律 None，不猜（宁缺毋滥）。"""
    h = contract_header_facts("本合同文本双面打印，要素内容可增加不可删减。", "扫描件.pdf")
    assert h == {"contract_number": None, "party_a": None, "party_b": None, "total_amount": None}


# —— 以下四条来自对抗性复核（workflow）报告的实测触发用例 ——

def test_party_with_internal_linebreak_is_not_truncated():
    """机构名内部的断行/空格不得把名字**静默截断**成半截。

    库内实证：`单细胞-技术开发项目委托合同-欧易.docx` 等 2 行的 party_a 曾是
    `中国人民解放军空军军医大学军事预`（漏了「防医学系」，看不出错）。
    """
    h = contract_header_facts(
        "委托方（甲方）：中国人民解放军空军军医大学军事预 防医学系\n"
        "受托方（乙方）：上海欧易生物医学科技有限公司", "x.pdf")
    assert h["party_a"] == "中国人民解放军空军军医大学军事预防医学系"
    assert h["party_b"] == "上海欧易生物医学科技有限公司"


def test_foundation_is_recognized_as_org():
    """`基金会/协会/学会` 等也是合法主体，不得因机构词表缺项被判误抓而丢弃。"""
    h = contract_header_facts("（委托方）甲方： | 北京陈菊梅公益基金会", "x.pdf")
    assert h["party_a"] == "北京陈菊梅公益基金会"


def test_column_label_with_org_word_is_rejected():
    """两栏表头里**含机构词的隔壁列标签**不得被当成单位名。

    真实布局 `需方（甲方） | 供方（乙方）` 恰好因「供方（乙方）」不含机构词而被挡住——
    属侥幸。`需方（甲方） | 招标代理公司（乙方）` 含「公司」就会漏过，故显式拒绝栏目标签。
    """
    assert contract_header_facts("需方（甲方） | 招标代理公司（乙方）", "x.pdf")["party_a"] is None
    assert contract_header_facts("需方（甲方） | 供方（乙方）", "x.pdf")["party_a"] is None


# —— 合同日期：年份交叉校验与合并 ——

_FN_2025 = "ZOE2025103717-Level One Pro 定量蛋白质组（富集）-安徽医科大学-882000.00.pdf"


def test_ocr_misread_year_is_rejected_by_number_crosscheck():
    """OCR 把 2025 读成 2015 必须被挡下 —— **只靠 2015–2030 区间挡不住**（2015 正在区间内）。

    库内实证：`ZOE2025103717` 的 contract_date 曾是 `2015-11-25`。
    修法是拿**合同编号内嵌年份**做交叉校验，不一致就回退编号年月。
    """
    assert extract_contract_date("签署日期：2015年11月25日", _FN_2025) == ("2025-10", "contract_id")
    # 正常签署日不受影响
    assert extract_contract_date("签署日期：2025年11月25日", _FN_2025) == ("2025-11-25", "signature_page")
    # 无编号可比对时，区间校验仍然生效（2010 超出下界）
    assert extract_contract_date("签署日期：2010年11月25日", "无编号.pdf")[0] is None


def test_best_date_prefers_year_consistent_then_precision():
    """合并规则：**先剔年份与编号不符的**，再按精度「只升不降」。

    原实现只有后半条，于是错的长日期一旦写入就再也无法被正确值取代。
    """
    assert _pick_best_date("2015-11-25", "2025-10", "2025-10") == "2025-10"   # 旧错新对
    assert _pick_best_date("2025-10", "2015-11-25", "2025-10") == "2025-10"   # 旧对新错
    assert _pick_best_date("2025-03-25", "2025-03", "2025-03") == "2025-03-25"  # 精度只升不降
    assert _pick_best_date(None, "2025-03", "2025-03") == "2025-03"
    assert _pick_best_date("2025-03-25", None, "2025-03") == "2025-03-25"


