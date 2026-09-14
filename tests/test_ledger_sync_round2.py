"""R4 增量更新第二轮收紧：完整性与父变化检测（补充单测）。

新增场景（用户拍板：表头命中≠完整清单；父变化检测不只看采购人+金额）：
- 仅有表头（无数据/无闭包/无空证据）→ incomplete，不清空
- 表头后截断 → incomplete，不清空
- 候选行解析失败（序号行有，但解析不出采购人/金额）→ incomplete，不清空
- 明确确认的空清单（表头+闭包+空证据）→ cleared，定向清空
- 相同采购人+金额、项目内容改变（如 蛋白组→代谢组）→ 旧子记录不能保留
- 两行真正交换顺序 → 子记录不串挂
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config as app_config  # noqa: E402
import app.db as db  # noqa: E402
from app.extract import extract_and_sync, extract_contract_ledger_state  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "r4c.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    con = db.connect()
    with con:
        con.execute("INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
                    " document_role, parse_status) VALUES (?,?,?,?,?,?)",
                    ("doc-A", "2026年", "P", "P/02 商务技术.docx", "our_response", "pending"))
    yield con
    con.close()


def _rows(con):
    return {int(r["ordinal"]): dict(r) for r in con.execute("SELECT * FROM contracts")}


def _items(con):
    return {r["item_id"]: dict(r) for r in con.execute("SELECT * FROM contract_items")}


HEADER = """3.2 合作单位证明（业绩6个）
序号 | 采购人
名称 | 项目名称 | 合同
金额
（万元） | 备注
（年份）
"""

CLOSE = "注：投标人可按上述的格式自行编制\n"

LEDGER_A = HEADER + """1 | 北京师范大学 | 全外显子捕获测序项目 | 270 | 2023年
2 | 中国福利会国际和平妇幼保健院 | LC-MS/MS 全谱代谢组检测 | 21.5 | 2024年
""" + CLOSE


# —— A) 仅有表头：不得清空 ——
def test_only_header_not_complete_keeps_records(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    only_header = HEADER  # 无数据行、无闭包、无空证据
    st = extract_contract_ledger_state(only_header)
    assert st["header_found"] is True and st["full_result"] is False and st["empty_confirmed"] is False
    r = extract_and_sync(fresh_db, "doc-A", only_header)
    assert r["status"] == "incomplete"
    assert r["deleted"] == 0 and r["cleared"] == 0
    assert len(_rows(fresh_db)) == 2  # 旧记录保留


# —— B) 表后截断：不得清空、不做缺行删除 ——
def test_truncated_after_header_keeps_records(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    truncated = HEADER + "1 | 北京师范大学 | 全外显子捕获测序项目 | 270 | 2023年\n"  # 第二行被截断，无闭包
    st = extract_contract_ledger_state(truncated)
    assert st["header_found"] is True and st["full_result"] is False
    r = extract_and_sync(fresh_db, "doc-A", truncated)
    assert r["status"] == "incomplete"
    assert r["deleted"] == 0
    assert len(_rows(fresh_db)) == 2


# —— C) 候选行解析失败（序号行存在但解析不出采购人/金额）→ 不清空 ——
def test_unparsable_row_keeps_records(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    bad = HEADER + "7 | ？？？？ | ！！！ | ！ | ！！\n" + CLOSE
    st = extract_contract_ledger_state(bad)
    assert st["header_found"] is True
    assert st["unparsed_rows"] >= 1
    assert st["full_result"] is False
    r = extract_and_sync(fresh_db, "doc-A", bad)
    assert r["status"] == "incomplete"
    assert r["deleted"] == 0
    assert len(_rows(fresh_db)) == 2


# —— D) 明确确认的空清单 → 定向清空 ——
def test_confirmed_empty_clears(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    empty = HEADER + "注：本次无业绩\n"
    st = extract_contract_ledger_state(empty)
    assert st["empty_confirmed"] is True
    r = extract_and_sync(fresh_db, "doc-A", empty)
    assert r["status"] == "cleared" and r["cleared"] == 2
    assert len(_rows(fresh_db)) == 0


# —— E) 相同采购人+金额、项目内容改变 → 旧子记录不能保留 ——
def test_project_change_same_party_amount_drops_children(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    with fresh_db:
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES ('c1','LEDGER-doc-A-1','蛋白组明细',123,'detail')")
    # 客户A/蛋白组/50万 → 客户A/代谢组/50万（采购人=金额未变，项目变）
    changed = HEADER + "1 | 上海中医药大学 | 中药代谢检测及分析 | 50 | 2025年\n" + CLOSE
    extract_and_sync(fresh_db, "doc-A", changed)
    rows = _rows(fresh_db)
    assert rows[1]["party_a"] == "上海中医药大学"
    assert rows[1]["total_amount"] == 500000.00
    # 旧子记录必须失效（父业务内容变了）
    assert len(_items(fresh_db)) == 0


# —— F) 两行真正交换顺序：不串挂子记录 ——
def test_true_swap_order_no_child_across(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    with fresh_db:
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES ('c1','LEDGER-doc-A-1','全外显子',500,'detail')")
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES ('c2','LEDGER-doc-A-2','代谢组',210,'detail')")
    # 两行真正交换顺序：序号重编号（中国福利会→第1行，北京→第2行）
    swapped = HEADER + """1 | 中国福利会国际和平妇幼保健院 | LC-MS/MS 全谱代谢组检测 | 21.5 | 2024年
2 | 北京师范大学 | 全外显子捕获测序项目 | 270 | 2023年
""" + CLOSE
    extract_and_sync(fresh_db, "doc-A", swapped)
    rows = _rows(fresh_db)
    # 行1=中国福利会（内容原为 ordinal2），行2=北京（内容原为 ordinal1）→ 序号身份已交换
    assert rows[1]["party_a"] == "中国福利会国际和平妇幼保健院"
    assert rows[2]["party_a"] == "北京师范大学"
    # 旧子记录不能串挂：c2(代谢组) 原是 ordinal2 的子，现在 ordinal1 是中国福利会；
    # 保守规则"完全相同输入才保留"，交换后父业务内容位置已变 → 子记录全部失效。
    items = _items(fresh_db)
    assert len(items) == 0, "交换顺序后旧子记录不得串挂"


# —— G) 写库失败整事务回滚 ——
def test_write_failure_full_rollback(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    before = "|".join(str(dict(r)) for r in fresh_db.execute("SELECT * FROM contracts"))
    class _BoomCon:
        def __init__(self, real):
            self._real = real
            self.row_factory = real.row_factory
        def execute(self, *a, **k):
            if a and isinstance(a[0], str) and "UPDATE contracts SET" in a[0]:
                raise RuntimeError("写库崩溃")
            return self._real.execute(*a, **k)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            self._real.rollback()
            return False
        def commit(self): ...
        def rollback(self): ...

    with pytest.raises(Exception, match="写库崩溃"):
        extract_and_sync(_BoomCon(fresh_db), "doc-A", LEDGER_A.replace("| 270 |", "| 999 |"))
    after = "|".join(str(dict(r)) for r in fresh_db.execute("SELECT * FROM contracts"))
    assert before == after