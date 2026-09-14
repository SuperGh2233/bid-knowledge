"""R4 增量更新边界修正单测（真实入口 extract_and_sync / sync_contract_ledger）。

关键语义（用户拍板）：
- None / 空串 / 纯空白 / 无可用原生正文 / 读取不完整 → status=no_native_text，不得删除旧记录，不计成功0条。
- 只有成功取得完整清单结构（header_found）且明确确认空清单才清空；
  "未匹配表头" 或 "extract 返回 []" 不单独构成清空依据。
- 同 ordinal 内容变化（合同变）→ 旧子记录失效先删，避免子记录挂在已变成另一份合同的父下；
  同输入重放（内容一致）→ 保留仍有效的子记录。
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
    path = tmp_path / "r4b.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    con = db.connect()
    with con:
        con.execute("INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
                    " document_role, parse_status) VALUES (?,?,?,?,?,?)",
                    ("doc-A", "2026年", "P", "P/02 商务技术.docx", "our_response", "pending"))
    yield con
    con.close()


LEDGER_A = """3.2 合作单位证明（业绩6个）
序号 | 采购人
名称 | 项目名称 | 合同
金额
（万元） | 备注
（年份）
1 | 北京师范大学 | 全外显子捕获测序项目 | 270 | 2023年
2 | 中国福利会国际和平妇幼保健院 | LC-MS/MS 全谱代谢组检测 | 21.5 | 2024年
注：投标人可按上述的格式自行编制
"""


def _rows(con):
    return {int(r["ordinal"]): dict(r) for r in con.execute("SELECT * FROM contracts")}


def _items(con):
    return {r["item_id"]: dict(r) for r in con.execute("SELECT * FROM contract_items")}


# —— 1) 无正文/空/空白 不清空，不计成功0条 ——
def test_no_native_text_preserves_old_records(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    stale = _rows(fresh_db)
    for bad in (None, "", "   ", "\n\t  "):
        r = extract_and_sync(fresh_db, "doc-A", bad)
        assert r["status"] == "no_native_text", bad
        assert r["deleted"] == 0 and r["cleared"] == 0
        assert _rows(fresh_db) == stale
    assert "成功0条" not in str(r)


# —— 2) 无表头（无完整结构）不清空 ——
def test_missing_header_keeps_old_records(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    # 一段无"序号|采购人…"表头但含"业绩"字样的文本
    no_header = "3.2 合作单位证明（业绩6个）\n供应商全称（公章）：欧易\n说明：本次未附业绩清单表格。"
    r = extract_and_sync(fresh_db, "doc-A", no_header)
    assert r["status"] == "no_ledger_confirmed"
    assert r["deleted"] == 0 and r["cleared"] == 0
    assert len(_rows(fresh_db)) == 2  # 旧记录保留


# —— 3) 表头命中 + 空 → 明确确认空清单才清空 ——
def test_header_found_empty_confirmed_clears(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    # 表头确实存在（含"序号|采购人"），但下面无数据行 → 明确确认空清单
    empty_header = """3.2 合作单位证明（业绩6个）
序号 | 采购人
名称 | 项目名称 | 合同
金额
（万元） | 备注
（年份）
注：本次无业绩"""
    state = extract_contract_ledger_state(empty_header)
    assert state["header_found"] is True
    assert state["records"] == []
    r = extract_and_sync(fresh_db, "doc-A", empty_header)
    assert r["status"] == "cleared" and r["cleared"] == 2
    assert len(_rows(fresh_db)) == 0


# —— 4) 同 ordinal 内容变化 → 子记录失效先删 ——
def test_same_ordinal_content_change_drops_stale_children(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    # 给第1行加两条子记录
    with fresh_db:
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES ('c1','LEDGER-doc-A-1','全外显子',500,'detail')")
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES ('c2','LEDGER-doc-A-1','测序',800,'detail')")
    assert len(_items(fresh_db)) == 2
    # 第1行金额 270->500 变化
    changed = LEDGER_A.replace("| 270 |", "| 500 |")
    extract_and_sync(fresh_db, "doc-A", changed)
    rows = _rows(fresh_db)
    assert rows[1]["total_amount"] == 5000000.00
    # 旧子记录必须失效（合同已变）
    assert len(_items(fresh_db)) == 0


# —— 5) 同输入重放保留有效子记录 ——
def test_same_input_replay_keeps_valid_children(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    with fresh_db:
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES ('c1','LEDGER-doc-A-1','全外显子',500,'detail')")
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)  # 同输入重放
    items = _items(fresh_db)
    assert len(items) == 1 and "c1" in items  # 子记录不丢


# —— 6) 重排行（删第1插新3）→ 子记录不挂在变了的父下 ——
def test_reorder_deletes_children_on_removed_parent(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    with fresh_db:
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES ('c1','LEDGER-doc-A-1','全外显子',500,'detail')")
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES ('c2','LEDGER-doc-A-2','代谢',210,'detail')")
    modified = "3.2 合作单位证明（业绩6个）\n" + LEDGER_A.replace(
        "2 | 中国福利会国际和平妇幼保健院 | LC-MS/MS 全谱代谢组检测 | 21.5 | 2024年\n"
        "1 | 北京师范大学 | 全外显子捕获测序项目 | 270 | 2023年\n", "")  # 这场不复杂处理，改用下面重建
    # 更稳定：明确 新清单只有第3行（新增北京=重排），带闭合锚点
    new_ledger = """3.2 合作单位证明（业绩6个）
序号 | 采购人
名称 | 项目名称 | 合同
金额
（万元） | 备注
（年份）
1 | 北京师范大学 | 全外显子捕获测序项目（大单） | 360 | 2025年
3 | 安徽医科大学 | Pro定量蛋白质组 | 88.2 | 2025年
注：投标人可按上述的格式自行编制
"""
    extract_and_sync(fresh_db, "doc-A", new_ledger)
    rows = _rows(fresh_db)
    assert set(rows) == {1, 3}
    # 旧 ordinal2（代谢）已删 → 其子记录 c2 必须删；旧 ordinal1 已变(360万) → c1 必须删
    items = _items(fresh_db)
    assert len(items) == 0


# —— 7) 写库失败完整回滚 ——
def test_write_failure_rolls_back(fresh_db):
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
    assert before == after, "半份数据不得提交"