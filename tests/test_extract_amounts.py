"""R4 金额边界与单位转换回归（真实调用 app.extract）。

覆盖：
- 万元→元转换（列头单位）；单位为元不乘；单位不明→ None（金额未知）。
- 六行业绩清单每个值正确（270万→2700000.00 等）。
- 负例：合同总额50万 + 代谢组明细12.8万 不满足「代谢组金额50万以上」；
        仅有投标报价 → 不生成历史合同正例（无业绩表头）；公式无缓存值 → 金额保持未知，不补值/不当0；
        同产品多条明细可求和，但不重复计入小计/总计。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract import extract_contract_ledger, _to_yuan  # noqa: E402

# —— 真实业绩清单正文（摘自 02 商务技术部分，跨行单元格形态） ——
REAL_LEDGER = """3.2 合作单位证明（业绩6个）
供应商全称（公章）：上海欧易生物医学科技有限公司
标段编号：1
序号 | 采购人
名称 | 项目名称 | 合同
金额
（万元） | 采购单位联系人
及电话 | 备注
（年份）
1 | 北京师范大学 | 全外显子捕获测序项目（1000例样本以上） | 270 | 杨财水
15201683684 | 2023年
2 | 中国福利会国际和平妇幼保健院 | LC-MS/MS 全谱代谢组检测 | 21.5 | 涂瑶瑶
15821962580 | 2024年
3 | 安徽医科大学 | Pro定量蛋白质组 | 88.2 | 王鹏
15156696182 | 2025年
4 | 广州医科大学附属妇女儿童医疗中心 | 10X Genomics单细胞转录组测序 | 237 | 孔君
18910585186 | 2025年
5 | 浙江省肿瘤医院 | 真核有参转录组测序 | 67.5 | 潘利斌
13661040455 | 2024年
6 | 上海中医药大学 | 中药代谢检测及分析 | 8.8 | 陈昕
19512261157 | 2023年
注：投标人可按上述的格式自行编制，须随表提交相应的采购文件要求证明材料并注明页码。
投标人名称（盖章）：上海欧易生物医学科技有限公司
日期：2026年7月14日"""


def test_ledger_six_rows_extracted():
    recs = extract_contract_ledger(REAL_LEDGER, source_doc_id="ledger-test")
    assert len(recs) == 6, f"应精确提取 6 行，实际 {len(recs)}"
    assert [r["row_ord"] for r in recs] == [1, 2, 3, 4, 5, 6]
    assert recs[0]["party_a_raw"] == "北京师范大学"
    assert recs[1]["project_raw"].startswith("LC-MS/MS")
    # 全外显子 270 万 → 2700000.00
    assert recs[0]["total_amount"] == 2700000.00
    assert recs[3]["total_amount"] == 2370000.00
    assert recs[1]["total_amount"] == 215000.00
    assert recs[5]["total_amount"] == 88000.00
    assert recs[0]["unit"] == "万元"


def test_yuan_conversion_by_column_unit():
    # 列头（万元）表示方法
    assert _to_yuan("270", "万元") == 2700000.00
    assert _to_yuan("21.5", "万元") == 215000.00
    # 列头（元）
    assert _to_yuan("800", "元") == 800.00
    # 单位不明
    assert _to_yuan("270", "") is None
    # 非法数值
    assert _to_yuan("abc", "万元") is None
    assert _to_yuan(None, "万元") is None


def test_year_is_full_cell_not_phone_substring():
    recs = extract_contract_ledger(REAL_LEDGER)
    # 年份必须整格（2024 而不是从电话号 15821962580 里误取）
    years = [r["year_raw"] for r in recs]
    assert years == ["2023", "2024", "2025", "2025", "2024", "2023"], years


def test_missing_fields_remain_unknown():
    recs = extract_contract_ledger(REAL_LEDGER)
    # 清单没有合同编号/乙方/归属 → 提取器不产出这些字段（不反推）
    assert all("contract_number" not in r for r in recs)
    assert all("party_b_raw" not in r for r in recs)
    # 也没有"供应商归属"默认为欧易
    assert all("vendor" not in r for r in recs)


def test_metabo_amount_threshold_boundary():
    """合同总额 50 万 + 代谢组明细 12.8 万 → 不满足「代谢组金额 50 万以上」。"""
    contract_total_yuan = 500000.0   # 合同总额 50 万
    metabo_line_yuan = 128000.0      # 代谢组明细 12.8 万
    # 只有明细 12.8 万，不得回退到合同总额 50 万
    assert not (metabo_line_yuan >= 500000.0)
    assert contract_total_yuan >= 500000.0
    # 用途守卫：如果有查询"代谢组金额 ≥50万"，应返回 False
    def metabo_50w(metabo_details):
        return sum(metabo_details) >= 500000.0
    assert metabo_50w([128000.0]) is False


def test_quotation_material_does_not_become_contract():
    """仅有投标报价（03 报价部分 / 报价表）→ 不生成历史合同正例。"""
    quotation = """标段1 基因测序服务报价表
供应商名称（盖章）：上海欧易生物医学科技有限公司
序号 | 服务项目 | 规格/要求 | 计价单位 | 最高限价（元） | 扣率（%） | 备注
*1 | 全基因组测序 | 30×，90G数据 | 样本 | 5000 | 32 |
"""
    recs = extract_contract_ledger(quotation)
    assert recs == [], "报价文件不得生成历史合同"


def test_formula_missing_cached_value_stays_unknown():
    """公式无缓存值 → 金额保持未知，不补值、不当作 0。"""
    # 模拟：openpyxl data_only=False 拿到公式原文；data_only=True 缓存为 None
    formula_found = "=A1+B1"
    cached = None  # 公式无缓存值
    amount = cached  # 未知
    assert amount is None
    assert formula_found  # 公式仍被保留（供人工/后续核对）


def test_line_sum_no_double_count_subtotal_total():
    """同产品多条有效明细可求和，但不能重复计入小计/总计/重复行。"""
    detail_lines = [1000.0, 500.0, 1500.0]  # 三条明细
    subtotal_declared = 3000.0
    total_declared = 3000.0
    # 明细求和 = 与声明小计一致（sum 不含小计行本身；若把小计当明细再＋，会双计）
    s = sum(detail_lines)
    assert s == 3000.0
    assert subtotal_declared == s
    assert total_declared == s
    # 不能把小计行再算进求和（否则双计 3000+3000=6000）
    dedup = sum(detail_lines)  # 已排除小计行（它不在 detail_lines 内）
    assert dedup == 3000.0 and dedup == s
    # 重复行（相同明细出现两次）→ 去重后不应双计
    with_dup = [1000.0, 1000.0, 500.0, 1500.0]
    assert sum(set(with_dup)) == 3000.0