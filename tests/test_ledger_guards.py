"""业绩清单抽取的四条新护栏（2026-09-16，PLAN-20260916-track-record-search §5.2–§5.3）。

都是**实测踩出来的误抽/漏抽**，每条对应一个真实反例：

1. **表头必须有金额列** —— 扩展当事人词表后，「序号 | 单位名称 | 相互关系」这类
   **关联方表**被当成业绩表，把**电话号码**当成了金额（实测一条 4.46 亿）。
2. **行级可信性** —— 某文档表标题是「投标人业绩情况表」，表体却是**技术响应偏离表**
   （"我司完全响应/无偏离"），24 行全假（采购人=项目名、无金额）。
3. **金额上限** —— 同上，电话号码量级必须判为误读。
4. **受限放行（synced_partial）** —— 旧规则"一行解析失败 → 整份不写"拦下 14 份 / 312 条；
   仅当「有行 + 表已收尾 + **该文档当前无任何业绩行**」三者同时成立才放行（纯新增，无删改风险）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract import extract_and_sync, extract_contract_ledger_state  # noqa: E402


def _con(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "_ledger_guards.db")
    con.row_factory = sqlite3.Row
    con.executescript(
        # ⚠️ 必须与生产 INSERT 的列一致（少一列就 OperationalError —— 第一版就漏了 party_b 等）
        "CREATE TABLE contracts (contract_id TEXT PRIMARY KEY, document_id TEXT, ordinal INTEGER,"
        " contract_number TEXT, party_a TEXT, party_b TEXT, contract_vendor TEXT,"
        " vendor_scope TEXT, vendor_evidence TEXT, contract_date TEXT,"
        " total_amount REAL, evidence_text TEXT);"
        "CREATE TABLE contract_items (contract_id TEXT, item_id TEXT);")
    con.commit()
    return con


# —— ① 表头必须有金额列 ——

def test_header_without_amount_column_is_rejected():
    """关联方表（序号|单位名称|相互关系，无金额列）**不得**被当成业绩清单。"""
    text = ("关联方情况\n"
            "序号 | 单位名称 | 相互关系\n"
            "1 | 海南某科技有限公司 | 同一实际控制人 | 13800138000\n"
            "注：以上为关联方\n")
    st = extract_contract_ledger_state(text)
    assert st["header_found"] is False, st


def test_header_with_amount_column_still_accepted():
    """对照：同样带当事人列、但**有**金额列的，照常识别（不能因噎废食）。"""
    text = ("3.2 合作单位证明（业绩6个）\n"
            "序号 | 采购人\n名称 | 项目名称 | 合同\n金额\n（万元） | 备注（年份）\n"
            "1 | 北京师范大学 | 全外显子捕获测序项目 | 270 | 2023年\n"
            "注：投标人可按上述的格式自行编制\n")
    st = extract_contract_ledger_state(text)
    assert st["header_found"] is True and len(st["records"]) == 1, st
    assert st["records"][0]["total_amount"] == 2700000.0


# —— ② 行级可信性（采购人不像机构名 且 无金额 → 丢弃）——

def test_deviation_table_rows_are_dropped():
    """「投标人业绩情况表」标题 + 偏离表内容 → 一行都不产出（且**不计**解析失败）。"""
    text = ("投标人业绩情况表\n"
            "序号 | 客户名称 | 项目名称及合同金额（万元） | 签订合同时间 | 联系人及电话\n"
            "1 | 单细胞测序解析实体瘤疾病进展过程中的分子机制技术服务 |  |  |  |\n"
            "2 | 单细胞测序解析实体瘤疾病进展过程中的分子机制技术服务 |  |  |  |\n"
            "注：本表为响应偏离说明\n")
    st = extract_contract_ledger_state(text)
    assert st["records"] == [], st
    # ⚠️ 关键：丢弃**不计入 unparsed**，否则整份文档会被判"不完整"，伤及正常文档
    assert st["unparsed_rows"] == 0, st


def test_org_party_without_amount_is_kept():
    """对照：采购人是机构名、但表里没金额的**照留**（如实记 None，不猜）。"""
    text = ("近三年主要项目业绩清单\n"
            "序号 | 采购人 | 项目名称 | 合同金额（万元）\n"
            "1 | 上海市第六人民医院 | 空间转录组测序服务 | \n"
            "注：\n")
    st = extract_contract_ledger_state(text)
    assert len(st["records"]) == 1, st
    assert st["records"][0]["party_a_raw"] == "上海市第六人民医院"
    assert st["records"][0]["total_amount"] is None


# —— ③ 金额上限（电话号不得当金额）——

def test_phone_number_is_not_taken_as_amount():
    text = ("近三年业绩一览表\n"
            "序号 | 采购人 | 项目名称 | 合同金额（元）\n"
            "1 | 某医院 | 检测服务 | 13800138000 | 联系人\n"
            "注：\n")
    st = extract_contract_ledger_state(text)
    amts = [r["total_amount"] for r in st["records"]]
    assert all(a is None for a in amts), amts        # 11 位电话 → 判为误读，宁缺毋滥


def test_plausible_amount_still_kept():
    """对照：合理量级照常取（实测本语料最大 253.7 万）。"""
    text = ("近三年业绩一览表\n"
            "序号 | 采购人 | 项目名称 | 合同金额（万元）\n"
            "1 | 某医院 | 检测服务 | 253.7 |\n"
            "注：\n")
    st = extract_contract_ledger_state(text)
    assert st["records"][0]["total_amount"] == 2537000.0


# —— ④ 受限放行（纯新增才允许 partial 写入）——

# ⚠️ 造"解析失败行"必须用**编号行**：跟在数据行后面的普通文本会被并进上一行（不产生 unparsed）。
# `99 | - | - | -` 是编号行且解析不出采购人/金额 → unparsed=1（第一版就是这么写错的）。
PARTIAL = ("近三年主要项目业绩清单\n"
           "序号 | 采购人 | 项目名称 | 合同金额（万元）\n"
           "1 | 某医院 | 检测服务 | 30 |\n"
           "99 | - | - | -\n"
           "2 | 某某大学 | 测序服务 | 20 |\n"
           "注：\n")


def test_partial_write_allowed_for_doc_without_existing_rows(tmp_path):
    con = _con(tmp_path)
    st = extract_contract_ledger_state(PARTIAL)
    assert st["header_found"] and st["records"] and not st["full_result"], st   # 前提：不完整
    out = extract_and_sync(con, "docA", PARTIAL)
    assert out["status"] == "synced_partial", out
    assert out["upserted"] == 2
    assert con.execute("SELECT COUNT(*) FROM contracts WHERE contract_id LIKE 'LEDGER-docA%'"
                       ).fetchone()[0] == 2


def test_partial_write_refused_when_doc_has_existing_rows(tmp_path):
    """已有快照的文档**不放行** —— 保护既有行不被可疑解析删改（删除保护一字未动）。"""
    con = _con(tmp_path)
    con.execute("INSERT INTO contracts (contract_id, document_id, ordinal, party_a, total_amount,"
                " evidence_text) VALUES ('LEDGER-docB-1','docB',1,'旧医院',999999.0,'旧证据')")
    con.commit()
    out = extract_and_sync(con, "docB", PARTIAL)
    assert out["status"] == "incomplete", out
    # 旧行**原样保留**
    row = con.execute("SELECT party_a, total_amount FROM contracts WHERE contract_id='LEDGER-docB-1'"
                      ).fetchone()
    assert row["party_a"] == "旧医院" and row["total_amount"] == 999999.0


# —— 列定位（项目名在前、单位在后的表，不得把项目名当采购人）——

def test_column_position_mapping_when_party_column_is_last():
    text = ("十四、类似项目业绩一览表\n"
            "序号 | 项目名称 | 项目内容 | 签订时间 | 合同金额（万元） | 单位名称 | 经办人\n"
            "1 | 单细胞转录组测序 | 上门服务 | 2024.5.1 | 30 | 上海市第六人民医院 | 张三\n"
            "注：\n")
    st = extract_contract_ledger_state(text)
    rec = st["records"][0]
    assert rec["party_a_raw"] == "上海市第六人民医院", rec       # **不是**项目名
    assert rec["total_amount"] == 300000.0, rec
    assert rec["project_raw"] == "单细胞转录组测序", rec