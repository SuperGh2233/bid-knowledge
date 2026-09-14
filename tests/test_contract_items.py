"""R4 合同服务明细（截取真实合同表）提取 + D9 金额聚合单测（真实入口）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.extract import (
    parse_contract_service_table,
    aggregate_product_amount,
    has_unknown_amount,
    product_amount_status,
    extract_and_sync,
)
import app.config as app_config  # noqa: E402
import app.db as db  # noqa: E402


# —— 真实合同1（单细胞-欧易19.98万）服务明细表 ——
TBL_SINGLE = """附件2：服务明细
服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）
10x Genomics 单细胞转录组测序 | 1 | 单细胞悬液制备（动物组织） | 样本 | 18 | 500.00 | 9,000.00
10x Genomics 单细胞转录组测序 | 2 | RNA抽提质检-单细胞 | 样本 | 18 | 300.00 | 5,400.00
10x Genomics 单细胞转录组测序 | 3 | 微流控油包水（10×） | 样本 | 18 | 4,800.00 | 86,400.00
10x Genomics 单细胞转录组测序 | 4 | 10X3'细胞扩增与文库构建 | 样本 | 18 | 500.00 | 9,000.00
10x Genomics 单细胞转录组测序 | 5 | 单细胞测序100G (Illumina) | 样本 | 18 | 3,000.00 | 54,000.00
10x Genomics 单细胞转录组测序 | 6 | 单细胞转录组数据质控 | 样本 | 18 | 0.00 | 0.00
10x Genomics 单细胞转录组测序 | 7 | 10X单细胞转录组标准分析+高级分析 | 样本 | 18 | 2,000.00 | 36,000.00
合同总金额（元） | 合同总金额（元） | 合同总金额（元） | 199,800.00
"""


# —— 真实单细胞表结构验证 ——
def test_single_cell_table_rows():
    recs = parse_contract_service_table(TBL_SINGLE)
    assert len(recs) == 8  # 7 detail + 1 contract_total
    details = [r for r in recs if r["row_type"] == "detail"]
    assert len(details) == 7
    assert details[0]["service_name"] == "单细胞悬液制备（动物组织）"
    assert details[0]["quantity"] == 18
    assert details[0]["unit_price"] == 500.00
    assert details[0]["line_amount"] == 9000.00
    assert details[2]["line_amount"] == 86400.00
    # 合同总金额行
    total = [r for r in recs if r["row_type"] == "contract_total"]
    assert total and total[0]["line_amount"] == 199800.00


def test_amount_cell_with_trailing_unit_is_parsed():
    """金额列写成 `44,000.00 元` 时必须读出金额 —— **文档明写了，不能判成未知**。

    实测坑（金标准合同 `YOE2024114080`）：表头是 `服务项目|规格|单价（人民币：元）|数量|总价（人民币：元）`，
    行是 `转录组学 | 例 | 550.00 | 80 | 44,000.00 元` —— 单价/数量都读对了，
    唯独总价因为**结尾带「元」**导致 `float("44000.00元")` 抛错、被判为空。
    这是解析遗漏，不是「文档未记载」，修它不违反「金额未确认时不得猜测」——
    值就在文档里。实测金标准 Recall 80.8% → 84.6%。
    """
    recs = parse_contract_service_table(
        "服务项目 | 规格 | 单价（人民币：元） | 数量 | 总价（人民币：元）\n"
        "转录组学 | 例 | 550.00 | 80 | 44,000.00 元\n"
        "代谢组学 | 例 | 450.00 | 260 | 117,000.00 元\n")
    details = [r for r in recs if r["row_type"] == "detail"]
    assert [r["line_amount"] for r in details] == [44000.00, 117000.00]
    assert details[1]["quantity"] == 260 and details[1]["unit_price"] == 450.00


def test_non_numeric_amount_cell_still_unknown():
    """**保守边界不变**：不以数字开头的单元格仍**不得**被当成数字读出来。

    `小写：270,000.00` 这类带前缀的写法、以及含「万」的万元口径，一律返回 None
    （万元换算在业绩清单那边单独处理，这里不擅自换算）。
    ⚠️ 注意：该单元格判未知**不等于整行未知** —— 修订后整表数量单价齐全时仍会按 数量×单价 确认。
    故要单独验证「该单元格没被读成 270000」，需让整表**不可推导**（缺数量/单价）。
    """
    recs = parse_contract_service_table(
        "服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
        "代谢组 | 1 | 某项服务 | 样本 |  |  | 小写：270,000.00\n")
    assert recs[0]["line_amount"] is None, "带前缀的单元格不得被读成数字"
    # 含「万」的万元口径同样不擅自换算
    recs2 = parse_contract_service_table(
        "服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
        "代谢组 | 1 | 某项服务 | 样本 |  |  | 27万元\n")
    assert recs2[0]["line_amount"] is None


def test_total_row_with_daxiao_is_not_a_service_detail():
    """「同时含**大写**与**小写**」的行是**合同总额行**，不得当成服务明细。

    实测坑（金标准合同 `YOE2024091175`）：`合计（元）： | 大写：贰拾柒万元 | 小写：270,000.00`
    —— `_CONTRACT_TOTAL_TOKENS` 里没有 `合计（元）` 这种写法，于是它落成 `detail`：
      ① 污染 `has_unknown`（把整类产品判成「金额未知」而不参与命中）；
      ② 一旦解析出金额，会被**重复计入产品明细合计**（＝把合同总额混进产品金额，D9 禁止）。
    不用「合计」是因为它同时是产品小计词（`_SUBTOTAL_TOKENS`），加了会抢分类。
    """
    recs = parse_contract_service_table(
        "序号 | 测试项目 | 单价 | 数量 | 单位 | 备注\n"
        "3 | Level One 500 全谱代谢组 | 320 | 200 | 样 |\n"
        "合计（元）： | 大写：贰拾柒万元 | 小写：270,000.00\n")
    details = [r for r in recs if r["row_type"] == "detail"]
    totals = [r for r in recs if r["row_type"] == "contract_total"]
    assert [r["service_name"] for r in details] == ["Level One 500 全谱代谢组"]
    assert len(totals) == 1 and totals[0]["service_name"] == "合同总金额"


def test_derive_amount_flag_off_keeps_unknown(monkeypatch):
    """**回退路径**：设 `CONTRACT_DERIVE_AMOUNT=false` 即恢复修订前的 §2 口径
    （金额列为空 = 未知，即使数量单价齐全也不推导），Recall 回到 88.5%。

    保留这条测试，是为了让「旧口径」依然可执行、可验证 —— 需求方若否决 §2 修订，
    回退后这条仍成立。**它不再是默认口径**（默认已改为 true，见 `app/config.py`）。
    """
    import app.config as cfg
    monkeypatch.setattr(cfg, "CONTRACT_DERIVE_AMOUNT", False)
    recs = parse_contract_service_table(
        "序号 | 测试项目 | 单价 | 数量 | 单位\n"
        "3 | Level One 500 全谱代谢组 | 320 | 200 | 样\n")
    assert recs[0]["line_amount"] is None


def test_derive_amount_flag_on_computes_from_qty_price(monkeypatch):
    """**修订后的默认口径**（计划 §2 于 2026-09-11 修订并写明依据）：
    整表结构完整时用 数量×单价 补出金额，并打 `amount_source` 标记来源。

    ▲ 与「猜测」的区别：前提是**整张表**每条明细行都写着数量与单价（无部分 OCR、无串列），
      乘的是文档自身两个字段；且**不引用 `contracts.total_amount`**（D9 禁止它参与产品金额判断）。
    ▲ 实测：与文件名成交价完全一致的合同 80 → 82；金标准 Recall 88.5% → **92.3%（达到 ≥90% 门槛）**。
    """
    import app.config as cfg
    monkeypatch.setattr(cfg, "CONTRACT_DERIVE_AMOUNT", True)
    recs = parse_contract_service_table(
        "序号 | 测试项目 | 单价 | 数量 | 单位\n"
        "3 | Level One 500 全谱代谢组 | 320 | 200 | 样\n"
        "4 | 中药成分鉴定分析 | 4000 | 5 | 样\n")
    assert [r["line_amount"] for r in recs] == [64000.0, 20000.0]
    assert all(r.get("amount_source") == "derived_qty_x_price" for r in recs)
    # 前提不成立（**整表**要求：有一行缺数量/单价 → 不推导，保持未知）
    recs2 = parse_contract_service_table(
        "序号 | 测试项目 | 单价 | 数量 | 单位\n"
        "3 | Level One 500 全谱代谢组 | 320 | 200 | 样\n"
        "5 | 某项服务 | 100 |  | 样\n")
    assert all(r["line_amount"] is None for r in recs2 if r["row_type"] == "detail")


def test_single_cell_amount_aggregate():
    recs = parse_contract_service_table(TBL_SINGLE)
    total = aggregate_product_amount(recs)  # 全部 detail 求和（不含 0 金额行金额为 0 但对总无妨）
    # 9000+5400+86400+9000+54000+0+36000 = 199800
    assert total == 199800.00
    # 不包括 contract_total（不得重复累计）
    assert total != 199800.00 * 2
    # 未知金额不参与：构造一行 amount=None
    recs_x = recs + [{"row_type": "detail", "line_amount": None, "category": "未明"}]
    assert aggregate_product_amount(recs_x) == 199800.00


# —— 真实合同2（多组学-鹿明9.76万）：原文 detail 与 subtotal 存在真实不一致 ——
#   原文蛋白组 detail 金额列就是 600/600/0（原文档本身），而"合计"subtotal 声明 42,400。
TBL_MULTI = """附件2：服务明细
服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 总价（元）
真核转录组测序 | 1 | RNA抽提质检（2100） | 样本 | 80 | 100.00 | 8,000.00
真核转录组测序 | 2 | 高通量测序数据质控 | 样本 | 80 | 80.00 | 6,400.00
真核转录组测序 | 3 | 普通转录组建库（n≥6） | 次 | 80 | 0.00 | 0.00
真核转录组测序 | 4 | 高通量测序（illumina，6G） | 样本 | 80 | 100.00 | 8,000.00
真核转录组测序 | 5 | 有参转录组标准分析（n≥6） | 样本 | 80 | 80.00 | 6,400.00
真核转录组测序 | 6 | 总计 | 样本 | 80 | 360.00 | 28,800.00
DIA定量蛋白质组检测 | 1 | 蛋白抽提质检 | 样本 | 80 | 100.00 | 600.00
DIA定量蛋白质组检测 | 2 | DIA 定量蛋白质组检测 | 样本 | 80 | 430.00 | 600.00
DIA定量蛋白质组检测 | 3 | 蛋白组标准分析 | 样本 | 80 | 0.00 | 0.00
DIA定量蛋白质组检测 | 4 | 合计 | 样本 | 80 | 530.00 | 42,400.00
LC-MS全谱代谢组学检测 | 1 | LC-MS/MS全谱代谢实验下单 | 样本 | 80 | 330.00 | 26,400.00
LC-MS全谱代谢组学检测 | 2 | LC-MS/MS全谱代谢组检测代谢分 | 样本 | 80 | 0.00 | 0.00
LC-MS全谱代谢组学检测 | 3 | 合计 | 样本 | 80 | 330.00 | 26,400.00
合同总金额（元） | 合同总金额（元） | 合同总金额（元） | 97,600.00
"""


def test_multi_committee_totals_not_double_counted():
    recs = parse_contract_service_table(TBL_MULTI)
    details = [r for r in recs if r["row_type"] == "detail"]
    subtotals = [r for r in recs if r["row_type"] == "product_subtotal"]
    # 小计/总计行被识别为 product_subtotal，不进 detail 求和
    assert len(subtotals) == 3  # 真核总计、蛋白合计、代谢合计
    # 转录组 detail 求和：8000+6400+0+8000+6400 = 28800
    zw = aggregate_product_amount(recs, product_key="真核转录组测序")
    assert zw == 28800.00
    # 代谢组 detail 求和：26400（detail 行）
    met = aggregate_product_amount(recs, product_key="LC-MS全谱代谢组学检测")
    assert met == 26400.00
    # 蛋白组 detail 求和 = 600+600+0 = 1200（原文 detail 行即如此，不解释、不补值）
    pro_detail = aggregate_product_amount(recs, product_key="DIA定量蛋白质组检测")
    assert pro_detail == 1200.00
    # 蛋白组小计声明 42,400 与 detail 求和 1,200 不一致 → 冲突如实保留
    st = product_amount_status(recs, product_key="DIA定量蛋白质组检测")
    assert st["detail_sum"] == 1200.00
    assert st["subtotal_declared"] == 42400.00
    assert st["conflict"] is True  # 差异被识别，不校正不补差额
    # 合同总金额 97,600 不在任何产品 detail 内
    assert zw != 97600.00 and met != 97600.00
    # 全部 detail 求和（不含任何 subtotal）= 28800+1200+26400 = 56400
    assert aggregate_product_amount(recs) == 56400.00


def test_mixed_known_unknown_amount_partial_not_used_as_full():
    """同产品同时有已知与**不可推导的**未知金额：不能把部分和当作完整产品金额进入精确筛选。

    ⚠️ 口径修订后，构造用例必须让「未知行」**真的不可推导**（缺数量或单价）——
    否则整表完整、未知行会被数学补出，`has_unknown` 自然为假（那是正确的，不是本测试要守的性质）。
    """
    recs = parse_contract_service_table(
        "服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
        "代谢组 | 1 | 已知金额项目 | 样本 | 2 | 100 | 200.00\n"
        "代谢组 | 2 | 数量单价都缺 | 样本 |  |  | \n"  # 不可推导 → 真未知
    )
    # 部分和 200 ≠ 完整产品金额；调用方必须检查 has_unknown_amount
    assert aggregate_product_amount(recs) == 200.00
    assert has_unknown_amount(recs) is True
    # 精确阈值评估示例：存在未知金额 → 整个产品不可评估（不能把 200 当完整代谢组金额）
    assert (not has_unknown_amount(recs)) is False   # 有未知行 → 不参与精确筛选（宁不入，不误报）
    # 全部可确认（no unknown）时部分和才可作为完整产品金额
    recs2 = parse_contract_service_table(
        "服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
        "代谢组 | 1 | 项A | 样本 | 1 | 100 | 100.00\n"
        "代谢组 | 2 | 项B | 样本 | 1 | 100 | 200.00\n")
    assert aggregate_product_amount(recs2) == 300.00
    assert has_unknown_amount(recs2) is False


def test_same_content_different_position_not_deduped():
    """两条内容相同但来源位置不同的真实明细，不能仅凭内容相同去重；
    同一原始行被重复提取才去重。保留可核对的行身份（seq）与来源位置。"""
    recs = parse_contract_service_table(
        "服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
        "代谢组 | 1 | 已知金额项目 | 样本 | 2 | 100 | 200.00\n"
        "代谢组 | 2 | 已知金额项目 | 样本 | 2 | 100 | 200.00\n"  # 内容相同、位置不同（不同 seq）
    )
    assert len(recs) == 2
    # 两条都保留（不按内容去重）
    assert recs[0]["seq"] == 1 and recs[1]["seq"] == 2
    assert recs[0]["evidence_idx"] != recs[1]["evidence_idx"]
    assert aggregate_product_amount(recs) == 400.00  # 两条都计入
    # 同一起源重复提取去重：同一 evidence_idx 出现两次 → 去重后求和 200
    dup = [dict(recs[0]), dict(recs[0])]
    seen = set()
    uniq = []
    for r_ in dup:
        k = (r_["seq"], r_["evidence_idx"])
        if k not in seen:
            seen.add(k); uniq.append(r_)
    assert len(uniq) == 1 and aggregate_product_amount(uniq) == 200.00


def test_amount_unknown_stays_unknown_not_zero():
    """**未知不得当 0** —— 这条性质在 §2 金额口径修订后依然成立，只是「未知」的边界移动了。

    修订后（2026-09-11）：`金额` 列为空、但该行 数量 与 单价 都明写且**整表完整**时，
    金额取 数量×单价（是**读取**文档自身两个字段，不是猜）；**只有两者都取不到才算未知**。
    """
    # 数量与单价齐全 → 可确认（5×100=500），仍**不得当 0**
    recs = parse_contract_service_table(
        "服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
        "代谢组 | 1 | 金额列空但数量单价齐全 | 样本 | 5 | 100 | \n")
    assert recs[0]["line_amount"] == 500.00
    assert recs[0]["amount_source"] == "derived_qty_x_price"
    assert aggregate_product_amount(recs) == 500.00

    # 数量也缺 → 无法确认 → **未知（None），不是 0**
    recs2 = parse_contract_service_table(
        "服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
        "代谢组 | 1 | 数量单价都缺 | 样本 |  |  | \n")
    assert recs2[0]["line_amount"] is None
    assert aggregate_product_amount(recs2) is None


def test_ledger_sync_unaffected_by_new_funcs(fresh_db):
    # 确认新函数不破坏既有 extract_and_sync（回归）
    con = fresh_db
    LEDGER = """3.2 合作单位证明（业绩6个）
序号 | 采购人
名称 | 项目名称 | 合同
金额
（万元） | 备注
（年份）
1 | 北京师范大学 | 全外显子捕获测序项目 | 270 | 2023年
注：投标人可按上述的格式自行编制
"""
    r = extract_and_sync(con, "doc-A", LEDGER)
    assert r["status"] == "synced" and r["total"] == 1


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "r4d.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    con = db.connect()
    with con:
        con.execute("INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
                    " document_role, parse_status) VALUES (?,?,?,?,?,?)",
                    ("doc-A", "2026年", "P", "P/02 商务技术.docx", "our_response", "pending"))
    yield con
    con.close()