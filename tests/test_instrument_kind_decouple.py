"""护栏：**仪器名抽取与文档类别解耦**（PLAN-20260921-instrument-kind-gate）。

背景（2026-09-21 全库摸底证实是**系统性缺陷**，不是 Xenium 个例）：
原实现把 `instrument_names_in` 挂在 `if kind in ("instrument", "instrument_photo"):` 门后，
而 `kind_of` 把 social_security_month 排第 1 位且正文全文扫描 —— 几乎每份完整响应文件都含
「社保/完税/医疗」等词 → 整份文档被判成社保类 → 正文里明明有 `N台<名字>` 也整体跳过。
全库摸底：可抽 80 种/199 份文档，仅 16 种/168 条入库，**92%（182/199）被此门挡掉**。

本文件用**纯函数**（不连库、不写库、不 import 触发脚本主流程）钉住：
  ① 文档类别是社保/发票等非仪器类时，`N台<名字>` 仍抽得出 —— 解耦已生效；
  ② 噪声名仍被护栏拒（护栏不因解耦放松）；
  ③ 既有合法型号抽法不回归。

⚠️ `scripts/extract_three_modules.py` 已加 `__main__` 守卫（2026-09-18），import 不写库；
本测试只 import 其纯函数，安全。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.extract_three_modules import (  # noqa: E402
    instrument_names_in,
    kind_of,
)


def test_instrument_names_decoupled_from_doc_kind():
    """社保类文档正文含 `N台Xenium` → 仍抽得出型号（解耦核心：判别不依赖文档类别）。"""
    text = ("社会保险缴费记录（样例）\n"
            "单位名称：欧易生物\n"
            "现有 1台 10x Genomics Xenium 分析仪\n")
    # 前提：这类文档过去会被 kind_of 判成 social_security_month
    assert kind_of("欧易生物响应文件.docx", text) == "social_security_month"
    names = instrument_names_in(text)
    assert any("Xenium" in n for n, _ in names), names


def test_instrument_names_in_invoice_kind_doc():
    """发票类文档含 `N台<名字>` → 同样抽得出（不只社保，任意非仪器类别都应解耦）。"""
    text = ("购置发票\n"
            "发票总额：xxxx\n"
            "设备配置：2台 AB SCIEX QTRAP 质谱\n")
    assert kind_of("发票.docx", text) == "invoice"
    names = instrument_names_in(text)
    assert any("AB SCIEX" in n for n, _ in names), names


def test_noise_names_still_rejected_after_decoupling():
    """解耦不放松护栏：前缀残渣/噪声名仍被拒（`流式细胞仪的`、`IPP` 这类 2026-09-21 摸底看到的）。"""
    # 「的」开头残渣 → NAME_STOP_PREFIX 拒
    names = instrument_names_in("拥有3台的流式细胞仪的小室")
    assert all(not n.startswith("的") for n, _ in names)
    # 「平台」等 NAME_BAD_WORDS 词出现在名字里 → 拒
    names = instrument_names_in("拥有1台Xenium分析平台")
    assert not any("平台" in n for n, _ in names)
    # 长度 <2 或纯数字堆 → 拒
    names = instrument_names_in("拥有1台A和2台123456789")
    assert not any(n in ("A", "123456789") for n, _ in names)


def test_legitimate_model_still_extracted():
    """既有合法抽法不回归：`192 通道HBH192` 等带空格型号仍抽得出完整名。"""
    text = ("设备清单\n"
            "拥有1台 192 通道HBH192 自动化建库仪，2台96通道Auto-Pure 96\n")
    names = [n for n, _ in instrument_names_in(text)]
    # 前缀残渣去重原则下，应保留最长的完整名
    assert any("192 通道HBH192" in n or "192通道HBH192" in n for n in names), names
    assert any("Auto-Pure" in n for n in names), names


# —— 2026-09-21 残渣规则（PLAN-20260921-instrument-kind-gate §3，dry-run 实测 9 类噪声）——
# 解耦 kind 门后候选更多，这些通用残渣必须被新护栏拒掉；真信号（含闭合括号的型号）不得误伤。

def test_reject_score_clause_noise():
    """招标评分条款（`的，得X分` / `≤设备数`）→ 拒（dry-run 实测 `流式细胞仪的`12 份）。"""
    from scripts.extract_three_modules import _reject_instrument_name
    assert _reject_instrument_name("流式细胞仪的")
    assert _reject_instrument_name("≤设备数＜10台的")
    assert _reject_instrument_name("/套得1分")
    assert _reject_instrument_name("以下得2分")
    assert not _reject_instrument_name("AB SCIEX QTRAP")     # 真信号不误伤


def test_reject_seq_and_unclosed_noise():
    """序列号粘连 + 括号未闭合截断残渣 → 拒；括号闭合的真型号 → 保留。"""
    from scripts.extract_three_modules import _reject_instrument_name
    assert _reject_instrument_name("Bruker timsTOF HT设备序列号为")
    assert _reject_instrument_name("液质联用仪器（LC-MS/MS")      # 括号未闭合（截断残渣）
    assert not _reject_instrument_name("液质联用仪器（LC-MS/MS）")  # 括号闭合 → 真型号保留
    assert not _reject_instrument_name("Waters SYNAPT XS 质谱成像仪")


def test_reject_short_and_ordered_noise():
    """短非设备词（预备/备用）与圈序号/长残渣 → 拒。"""
    from scripts.extract_three_modules import _reject_instrument_name
    assert _reject_instrument_name("预备")
    assert _reject_instrument_name("备用")
    assert _reject_instrument_name("备用）")            # 带收尾括号的短噪声词（2026-09-21 实测残留）
    assert _reject_instrument_name("③联系维修工程师4小时内到场④若故障时间>2小时")
    assert not _reject_instrument_name("10x Genomics Xenium")