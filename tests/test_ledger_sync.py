"""R4 增量更新单测：sync_contract_ledger / extract_and_sync 真实入口（独立临时库）。

场景：
1. 相同输入重放 → 记录与字段均不变；
2. 同一行金额变化 → 更新为新金额，不保留旧值；
3. 删除/新增/重排行 → 与当前清单一致，不重复不串配采购人和金额；
4. 成功提取且确认清单为空 → 清除该文件对应旧清单记录；
5. 提取/写入失败 → 不提交半份数据，异常完整回滚；失败不能伪装成"成功提取0条"。
6. contract_items 关联子记录删除先于父。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config as app_config  # noqa: E402
import app.db as db  # noqa: E402
from app.extract import extract_contract_ledger, extract_and_sync, sync_contract_ledger  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "r4.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    con = db.connect()
    # 父文档
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


def test_same_input_replay_idempotent(fresh_db):
    s1 = extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    assert (s1["upserted"], s1["deleted"], s1["kept_updated"]) == (2, 0, 0), s1
    before = [dict(r) for r in fresh_db.execute("SELECT * FROM contracts ORDER BY ordinal")]
    # 相同输入重放
    s2 = extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    after = [dict(r) for r in fresh_db.execute("SELECT * FROM contracts ORDER BY ordinal")]
    assert s2["upserted"] == 0 and s2["deleted"] == 0 and s2["kept_updated"] == 2
    # 字段逐项不变
    for a, b in zip(before, after):
        for k in ("contract_id", "ordinal", "party_a", "total_amount", "evidence_text"):
            assert a[k] == b[k], (k, a[k], b[k])


def test_amount_change_updates_not_append(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    new = LEDGER_A.replace("| 270 |", "| 360 |")
    s = extract_and_sync(fresh_db, "doc-A", new)
    assert s["kept_updated"] == 2 and s["upserted"] == 0 and s["deleted"] == 0
    rows = {int(r["ordinal"]): dict(r) for r in fresh_db.execute("SELECT * FROM contracts")}
    assert rows[1]["total_amount"] == 3600000.00
    assert rows[2]["total_amount"] == 215000.00
    # 旧值不保留
    assert "2700000" not in str(rows[1]["evidence_text"])
    assert fresh_db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 2


def test_row_add_delete_not_misalign(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    # 删除第 2 行、新增第 3 行（带闭合锚点）
    modified = """3.2 合作单位证明（业绩6个）
序号 | 采购人
名称 | 项目名称 | 合同
金额
（万元） | 备注
（年份）
1 | 北京师范大学 | 全外显子捕获测序项目（1000例样本以上） | 270 | 2023年
3 | 安徽医科大学 | Pro定量蛋白质组 | 88.2 | 2025年
注：投标人可按上述的格式自行编制
"""
    s = extract_and_sync(fresh_db, "doc-A", modified)
    assert s["deleted"] == 1 and s["upserted"] == 1 and s["kept_updated"] == 1
    rows = {int(r["ordinal"]): dict(r) for r in fresh_db.execute("SELECT * FROM contracts")}
    assert set(rows) == {1, 3}
    assert rows[1]["party_a"] == "北京师范大学"
    assert rows[3]["party_a"] == "安徽医科大学"
    assert rows[3]["total_amount"] == 882000.00
    # 未串配
    assert rows[3]["total_amount"] != rows[1]["total_amount"]
    assert fresh_db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 2


def test_empty_ledger_clears_old_records(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    # 成功取得完整清单结构且确认清单为空 → 清除该文件旧记录
    empty_header = """3.2 合作单位证明（业绩6个）
序号 | 采购人
名称 | 项目名称 | 合同
金额
（万元）
注：本次无业绩"""
    s = extract_and_sync(fresh_db, "doc-A", empty_header)
    assert s["status"] == "cleared" and s["deleted"] == 2 and s["total"] == 0
    assert fresh_db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0


def test_extract_failure_rolls_back_no_partial(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    before = [dict(r) for r in fresh_db.execute("SELECT * FROM contracts ORDER BY ordinal")]
    # 写入失败：注入会抛错的 con（sync_contract_ledger 内部 with con 事务）
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
            self._real.rollback()  # 反正是注入：触发事务回滚
            return False
        def commit(self): ...
        def rollback(self): ...

    with pytest.raises(Exception, match="写库崩溃"):
        extract_and_sync(_BoomCon(fresh_db), "doc-A", LEDGER_A.replace("| 270 |", "| 500 |"))
    # 事务回滚 → 无半份数据；数据仍为旧值
    after = [dict(r) for r in fresh_db.execute("SELECT * FROM contracts ORDER BY ordinal")]
    assert after == before
    assert after[0]["total_amount"] == 2700000.00


def test_no_native_text_raises_not_zero_success(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    # native_text=None（无原生正文）→ 明确失败/待核，不能伪装"成功提取0条"
    r = extract_and_sync(fresh_db, "doc-A", None)
    assert r["status"] == "no_native_text"
    assert r["deleted"] == 0
    # 旧记录仍在（未因"0条"而清空）
    assert fresh_db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 2


def test_item_children_deleted_before_parent(fresh_db):
    extract_and_sync(fresh_db, "doc-A", LEDGER_A)
    # 人为给第1行加一条 contract_items 子记录
    with fresh_db:
        fresh_db.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, line_amount, row_type)"
                         " VALUES (?,?,?,?,?)",
                         ("i1", f"LEDGER-doc-A-1", "全外显子", 500.0, "detail"))
    assert fresh_db.execute("SELECT COUNT(*) FROM contract_items").fetchone()[0] == 1
    # 删除该行 → 子记录必须先于父删除，无悬挂
    modified = LEDGER_A.replace(
        "1 | 北京师范大学 | 全外显子捕获测序项目 | 270 | 2023年\n", "")
    extract_and_sync(fresh_db, "doc-A", modified)
    assert fresh_db.execute("SELECT COUNT(*) FROM contract_items").fetchone()[0] == 0
    assert fresh_db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 1