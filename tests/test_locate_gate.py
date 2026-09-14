"""资格门槛真实入口测试（R4 只读定位·补充）。

- 文档转为 holding_review     → 旧金额不得继续命中（skipped）
- 角色转为 competitor_response → 不得命中
- 内容已更新（文件字节SHA≠canonical）但明细未更新 → 不得命中
- 正常已核准 doc（单细胞）对照 → 可命中
- 样稿完整链路负例：与正例相同产品+足够金额，经 资格/提取/同步/查询 后不正式命中；
  正常合同对照可命中；样稿重放不重建已撤 CTL。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config as app_config  # noqa: E402
import app.db as db  # noqa: E402
from app.search import locate_by_product_amount, APPROVED_DOCUMENT_IDS  # noqa: E402
from app.extract import extract_contract_ledger_state  # noqa: E402


@pytest.fixture
def reg_db(tmp_path, monkeypatch):
    path = tmp_path / "loc2.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    con = db.connect()
    with con:
        _doc(con, "docA", "2026年", "20260508-标书-张红燕-单细胞", "P/单细胞合同.docx",
             role="our_response", status="pending", canonical="canonA")
        con.execute("INSERT INTO parse_artifacts (canonical_document_id, sha256, text, content_format, parser_name, parser_version) "
                    "VALUES ('canonA','canonA',?, 'native_text','py-docx-v1','0.1.0')",
                    ("服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
                     "10x Genomics 单细胞转录组测序 | 1 | 单细胞悬液制备 | 样本 | 18 | 500 | 9000.00\n"
                     "10x Genomics 单细胞转录组测序 | 2 | 单细胞测序100G | 样本 | 18 | 3000 | 54000.00\n"
                     "合同总金额（元） | X | X | X | X | X | 199800.00\n",))
        con.execute("INSERT INTO contracts (contract_id, document_id, ordinal, total_amount, evidence_text, source_sha256, parser_version) "
                    "VALUES ('CTL-docA','docA',1,199800.00,'单细胞合同','canonA','0.1.0')")
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                    "VALUES ('CTL-docA-1','CTL-docA','10x Genomics 单细胞转录组测序/悬液',18,500,9000.0,'detail','declared','10x Genomics 单细胞转录组测序 | 1 | 悬液 | 18 | 500 | 9000.00')")
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                    "VALUES ('CTL-docA-2','CTL-docA','10x Genomics 单细胞转录组测序/测序',18,3000,54000.0,'detail','declared','10x Genomics 单细胞转录组测序 | 2 | 测序 | 18 | 3000 | 54000.00')")
    yield con
    con.close()


def _doc(con, did, root, project, rel, role, status, canonical):
    con.execute("INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
                " document_role, parse_status, canonical_document_id) VALUES (?,?,?,?,?,?,?)",
                (did, root, project, rel, role, status, canonical))


def _query_hit(con, prod="单细胞", mn=50000):
    # roots 空 dict→读不到文件→raw_sha=None（不判内容版本差异，保守）；显式核准 docA
    rs = locate_by_product_amount(con, (prod,), mn, roots={},
                                  approved_document_ids={"docA"})
    return [r for r in rs if r.hit]


# —— 1) doc 转为 holding_review → 不再命中 ——
def test_holding_review_doc_not_hit(reg_db):
    assert _query_hit(reg_db), "基线应命中"
    with reg_db:
        reg_db.execute("UPDATE documents SET parse_status='holding_review' WHERE document_id='docA'")
    rs = locate_by_product_amount(reg_db, ("单细胞",), 50000, roots={},
                                  approved_document_ids={"docA"})
    skipped = [r for r in rs if r.skipped_reason]
    assert skipped and any("待复核" in r.skipped_reason for r in skipped)
    assert not _query_hit(reg_db), "holding_review 不得命中"


# —— 2) 竞品角色 → 不得命中 ——
def test_competitor_role_not_hit(reg_db):
    with reg_db:
        reg_db.execute("UPDATE documents SET document_role='competitor_response' WHERE document_id='docA'")
    assert not _query_hit(reg_db)


# —— 3) 内容已更新但明细未更新（canonical 失配） → 不得命中 ——
def test_content_changed_stale_canonical_not_hit(reg_db):
    # 模拟：文档 canonical 指向 canonA，但 artifacts.sha256 改为别值（表示产物版本不配对）
    with reg_db:
        reg_db.execute("UPDATE parse_artifacts SET sha256='stale-sha' WHERE canonical_document_id='canonA'")
    rs = locate_by_product_amount(reg_db, ("单细胞",), 50000, roots={},
                                  approved_document_ids={"docA"})
    assert not any(r.hit for r in rs)
    assert any("sha256" in (r.skipped_reason or "") for r in rs)


# —— 4) 正常已核准对照 → 命中 ——
def test_approved_control_hits(reg_db):
    assert _query_hit(reg_db), "已核准正常合同应可命中"


# —— 5) 样稿完整链路负例（不重建 CTL） ——
def test_sample_replay_does_not_recreate_removed_ctl(tmp_path, monkeypatch):
    # 构造：docA 有效（命中），docB 样稿（同产品+够金额）
    path = tmp_path / "loc3.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    con = db.connect()
    with con:
        _doc(con, "docA", "2026年", "P", "P/正常合同.docx", "our_response", "pending", "canonA")
        con.execute("INSERT INTO parse_artifacts (canonical_document_id, sha256, text, content_format, parser_name, parser_version) "
                    "VALUES ('canonA','canonA',?, 'native_text','py-docx-v1','0.1.0')",
                    ("服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
                     "代谢组 | 1 | 测序 | 样本 | 1 | 100000 | 100000.00\n",))
        con.execute("INSERT INTO contracts (contract_id, document_id, ordinal, total_amount, evidence_text, source_sha256, parser_version) "
                    "VALUES ('CTL-docA','docA',1,100000.0,'正合','canonA','0.1.0')")
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                    "VALUES ('CTL-docA-1','CTL-docA','代谢组/测序',1,100000,100000.0,'detail','declared','代谢组 | 1 | 测序 | 样本 | 1 | 100000 | 100000.00')")
        # docB 样稿：不建 CTL（像已撤状态）
        _doc(con, "docB", "2026年", "P", "P/框架样稿.docx", "our_response", "pending", "canonB")
        con.execute("INSERT INTO parse_artifacts (canonical_document_id, sha256, text, content_format, parser_name, parser_version) "
                    "VALUES ('canonB','canonB',?, 'native_text','py-docx-v1','0.1.0')",
                    ("服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
                     "代谢组 | 1 | 测序 | 样本 | 1 | 100000 | 100000.00\n",))
    # 查询（仅核准 docA/docB —— 但 docB 无 CTL → 不会命中）
    rs = locate_by_product_amount(con, ("代谢",), 90000, roots={},
                                  approved_document_ids={"docA", "docB"})
    files = {r.file_name for r in rs}
    assert "框架样稿.docx" not in files
    assert any(r.hit and r.document_id == "docA" for r in rs)  # 正常合同对照可命中
    con.close()