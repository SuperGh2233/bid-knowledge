"""把**用户实测报告的失败案例形状**钉成回归测试（2026-09-16）。

由来：用户连报两批"金额未记载"的卡片，要求"分析根本原因、解决源头"。修复后逐条复盘，
把每一类的**真实行形态**固化成测试 —— 以后任何改动若让这些形态再取不到金额，测试立刻红。

（每条的注释里写清"原文长什么样"，便于后来者对照。）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract import extract_contract_ledger_state  # noqa: E402


def _one(text: str) -> dict:
    st = extract_contract_ledger_state(text)
    assert st["header_found"] and st["records"], st
    return st["records"][0]


# —— ① 合并列：项目名与金额同格、用「、」分隔（百趣那份）——

def test_merged_cell_with_ideographic_comma():
    """`LC-MS/MS 全谱代谢组检测、35.5` —— 表头是 `项目名称及合同金额（万元）`。"""
    text = ("十二、投标人业绩情况表\n"
            "序号 | 客户名称 | 项目名称及合同金额（万元） | 签订合同时间 | 联系人及电话\n"
            "8 | 上海中医药大学附属曙光医院 | LC-MS/MS 全谱代谢组检测、35.5 | 2022年7月22日 | 韩煦，18616122427\n"
            "注：\n")
    rec = _one(text)
    assert rec["party_a_raw"] == "上海中医药大学附属曙光医院"
    assert rec["total_amount"] == 355000.0


def test_amount_glued_to_project_without_separator():
    """`LC-MS非靶向代谢31` —— 金额**紧贴**项目名结尾，无任何分隔符。"""
    text = ("十二、投标人业绩情况表\n"
            "序号 | 客户名称 | 项目名称及合同金额（万元） | 签订合同时间 | 联系人及电话\n"
            "7 | 复旦大学附属肿瘤医院 | LC-MS非靶向代谢31 | 2023年8月11日 | /\n"
            "注：\n")
    assert _one(text)["total_amount"] == 310000.0


# —— ② 逗号 + 裸「万」（曾只认「万元」，漏 3 条）——

def test_comma_separated_bare_wan_inside_project():
    """`…单细胞转录组测序服务，237万` —— 分隔符 + 裸「万」。"""
    text = ("投标人业绩情况表\n"
            "序号 | 采购人 | 项目名称 | 签订时间 | 完成时间 | 联系人\n"
            "12 | 广州医科大学附属妇女儿童医疗中心 | 空间多组学测序和单细胞转录组测序服务，237万 | 2025年4月27日 | 2025年5月31日 | 周文浩\n"
            "注：\n")
    assert _one(text)["total_amount"] == 2370000.0


# —— ③ 业绩表**没有金额列**：不得当成"取不到"，要如实标注（疾控那份）——

def test_table_without_amount_column_marks_reason():
    """`序号|合同或协议履约时间|服务内容|…|履约情况` —— 表里压根没有金额列。"""
    text = ("6．供应商近三年承担相关业绩一览表\n"
            "序号 | 合同或协议履约时间 | 服务内容 | 合同或协议签订时间 | 采购单位名称 | 履约情况\n"
            "1 | 2023年-2025年 | 队列高通量基因分型芯片检测服务项目（10000例样本以上） | 2023年10月24日 | 四川大学华西第四医院 | 完成\n"
            "注：\n")
    rec = _one(text)
    assert rec["total_amount"] is None
    assert rec["amount_absent"] == "表未设金额列"          # **如实标注**，不是含混的"未记载"
    assert rec["project_raw"] == "队列高通量基因分型芯片检测服务项目（10000例样本以上）"   # 不得是"2023年-2025年"


def test_table_with_amount_column_but_row_missing_marks_reason():
    """表里有金额列、但本行没取到 → 标 `本行未取到`（与"表未设金额列"区分开）。"""
    text = ("投标人业绩情况表\n"
            "序号 | 采购人 | 项目名称 | 合同金额（万元） | 签订时间\n"
            "6 | 四川大学华西医院 | 四川大学华西医院测序技术服务合同， | 2025年4月10日 | 2025年5月15日\n"
            "注：\n")
    rec = _one(text)
    assert rec["total_amount"] is None
    assert rec["amount_absent"] == "本行未取到"


# —— ④ 交叉引用：业绩表没金额，但同文档合同清单里有（含三种写法）——

def test_cross_ref_recovers_from_contract_list():
    """`6.9 血清样本全谱代谢组学项目项目合同（山西医科大学第一医院，4.75万）`。"""
    text = ("6．供应商近三年承担相关业绩一览表\n"
            "序号 | 服务内容 | 采购单位名称 | 履约情况\n"
            "9 | 血清样本全谱代谢组学项目 | 山西医科大学第一医院 | 完成\n"
            "注：\n"
            "6.9 血清样本全谱代谢组学项目项目合同（山西医科大学第一医院，4.75万）\n")
    rec = _one(text)
    assert rec["total_amount"] == 47500.0
    assert rec["amount_source"] == "同文档合同清单"
    assert "4.75万" in rec["amount_source_line"]       # 可审计：金额出自哪一行


def test_cross_ref_three_formats():
    """同一文档三种写法：`（…，270万）` / `(… 399万)` / `（… 80991元）`，且「元」不得按万算。"""
    text = ("6．业绩一览表\n"
            "序号 | 服务内容 | 采购单位名称 | 履约情况\n"
            "1 | 全外显子捕获测序项目 | 北京师范大学 | 完成\n"
            "2 | 队列高通量基因分型芯片检测服务项目 | 四川大学华西第四医院 | 完成\n"
            "3 | 角膜组织单细胞转录组测序项目 | 浙江大学医学院附第二医院 | 完成\n"
            "注：\n"
            "6.2 1000 例样本项目合同（北京师范大学，270万）\n"
            "6.1 10000 例样本项目合同(四川大学华西第四医院 399万)\n"
            "6.6 单细胞组学项目合同 （浙江大学医学院附第二医院 80991元）\n")
    st = extract_contract_ledger_state(text)
    got = {r["party_a_raw"]: r["total_amount"] for r in st["records"]}
    assert got["北京师范大学"] == 2700000.0
    assert got["四川大学华西第四医院"] == 3990000.0
    assert got["浙江大学医学院附第二医院"] == 80991.0        # 元 → 原值，不是 8 亿
