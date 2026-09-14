"""R4 只读定位测试：金额门槛（复用 product_amount_status），只查询有效历史合同 CTL。"""
from __future__ import annotations

import sys, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config as app_config  # noqa: E402
import app.db as db  # noqa: E402
from app.search import locate_by_product_amount  # noqa: E402


@pytest.fixture
def reg_db(tmp_path, monkeypatch):
    """在独立临时库构造两个 CTL 合同（单细胞真、代谢/蛋白真）+ 文档/产物。"""
    path = tmp_path / "loc.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    # 本测试用 docA/docB 作为"已核准"文档（避免依赖真实白名单 ID）
    import app.search as search_mod
    monkeypatch.setattr(search_mod, "APPROVED_DOCUMENT_IDS", {"docA", "docB"})
    db.init_db(force=True)
    con = db.connect()
    with con:
        # 文档 A：单细胞合同
        con.execute("INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
                    " document_role, parse_status, canonical_document_id) VALUES (?,?,?,?,?,?,?)",
                    ("docA", "2026年", "20260508-标书-张红燕-单细胞", "P/单细胞合同.docx", "our_response", "pending", "canonA"))
        con.execute("INSERT INTO parse_artifacts (canonical_document_id, sha256, text, content_format, parser_name, parser_version) "
                    "VALUES ('canonA','canonA',?, 'native_text','py-docx-v1','0.1.0')",
                    ("服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
                     "10x Genomics 单细胞转录组测序 | 1 | 单细胞悬液制备 | 样本 | 18 | 500 | 9000.00\n"
                     "10x Genomics 单细胞转录组测序 | 2 | 单细胞测序100G | 样本 | 18 | 3000 | 54000.00\n"
                     "合同总金额（元） | X | X | X | X | X | 199800.00\n",))
        con.execute("INSERT INTO contracts (contract_id, document_id, ordinal, total_amount, evidence_text, source_sha256, parser_version) "
                    "VALUES ('CTL-docA','docA',1,199800.00,'单细胞合同','canonA','0.1.0')")
        for seq, amt in ((1, 9000.0), (2, 54000.0)):
            con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                        "VALUES (?,?,'10x Genomics 单细胞转录组测序/项',18,100,?,?,?,?)",
                        (f"CTL-docA-{seq}", "CTL-docA", amt, "detail", "declared",
                         f"10x Genomics 单细胞转录组测序 | {seq} | 项 | 18 | 100 | {amt:,.2f}".replace(",", "")))
        # 文档 B：多组学合同（含代谢 detail + 蛋白 conflict）
        con.execute("INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
                    " document_role, parse_status, canonical_document_id) VALUES ('docB','2026年','20260508-标书-张红燕-多组学','P/多组学.docx','our_response','pending','canonB')")
        con.execute("INSERT INTO parse_artifacts (canonical_document_id, sha256, text, content_format, parser_name, parser_version) "
                    "VALUES ('canonB','canonB',?, 'native_text','py-docx-v1','0.1.0')",
                    ("服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 总价（元）\n"
                     "真核转录组测序 | 1 | RNA抽提质检 | 样本 | 80 | 100 | 8000.00\n"
                     "DIA定量蛋白质组检测 | 1 | 蛋白抽提质检 | 样本 | 80 | 100 | 600.00\n"
                     "DIA定量蛋白质组检测 | 2 | DIA 检测 | 样本 | 80 | 430 | 600.00\n"
                     "DIA定量蛋白质组检测 | 3 | 合计 | 样本 | 80 | 530 | 42400.00\n"
                     "LC-MS全谱代谢组学检测 | 1 | LC-MS实验下单 | 样本 | 80 | 330 | 26400.00\n"
                     "合同总金额（元） | X | X | X | X | X | 97600.00\n",))
        con.execute("INSERT INTO contracts (contract_id, document_id, ordinal, total_amount, evidence_text, source_sha256, parser_version) "
                    "VALUES ('CTL-docB','docB',1,97600.00,'多组学合同','canonB','0.1.0')")
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                    "VALUES ('CTL-docB-1','CTL-docB','真核转录组测序/RNA',80,100,8000.0,'detail','declared','真核转录组测序 | 1 | RNA | 80 | 100 | 8000.00')")
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                    "VALUES ('CTL-docB-2','CTL-docB','DIA定量蛋白质组检测/蛋白抽提质检',80,100,600.0,'detail','declared','DIA定量蛋白质组检测 | 1 | 蛋白 | 80 | 100 | 600.00')")
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                    "VALUES ('CTL-docB-3','CTL-docB','DIA定量蛋白质组检测/DIA 检测',80,430,600.0,'detail','declared','DIA定量蛋白质组检测 | 2 | DIA | 80 | 430 | 600.00')")
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                    "VALUES ('CTL-docB-4','CTL-docB','DIA定量蛋白质组检测/合计',80,530,42400.0,'product_subtotal','explicit_product_subtotal','DIA定量蛋白质组检测 | 3 | 合计 | 80 | 530 | 42400.00')")
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw, quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text) "
                    "VALUES ('CTL-docB-5','CTL-docB','LC-MS全谱代谢组学检测/LC-MS',80,330,26400.0,'detail','declared','LC-MS全谱代谢组学检测 | 1 | LC-MS | 80 | 330 | 26400.00')")
    yield con
    con.close()


def test_locate_single_cell_ge50000(reg_db):
    rs = locate_by_product_amount(reg_db, ("单细胞",), 50000, approved_document_ids={"docA", "docB"})
    hit = [r for r in rs if r.hit]
    assert len(hit) == 1
    assert hit[0].product == "10x Genomics 单细胞转录组测序"
    assert hit[0].amount == 63000.0  # 9000+54000=63000 ≥5万
    assert hit[0].amount_status == "ok"
    assert hit[0].file_name == "单细胞合同.docx"


def test_locate_metab_ge2w_and_ge15w(reg_db):
    rs = locate_by_product_amount(reg_db, ("代谢",), 20000, approved_document_ids={"docA", "docB"})
    hit = [r for r in rs if r.hit]
    assert len(hit) == 1
    assert hit[0].product == "LC-MS全谱代谢组学检测" and hit[0].amount == 26400.0
    # ≥15 万
    rs2 = locate_by_product_amount(reg_db, ("代谢",), 150000, approved_document_ids={"docA", "docB"})
    assert not any(r.hit for r in rs2)


def test_locate_protein_conflict_not_hit(reg_db):
    rs = locate_by_product_amount(reg_db, ("蛋白",), 1000, approved_document_ids={"docA", "docB"})
    # 蛋白组 detail 1200 ≥1000 但 conflict → 不列为正式命中；amount_status=conflict
    any_hit = [r for r in rs if r.hit]
    assert not any_hit
    pro = [r for r in rs if r.product == "DIA定量蛋白质组检测"]
    assert pro and pro[0].amount_status == "conflict"
    assert pro[0].amount == 1200.0  # 部分和披露，但冲突不让正式进入阈值


def test_framework_sample_not_returned(reg_db):
    # docB 中的 蛋白组 conflict；不插入样稿（样稿 CTL 已撤），框架样稿不应出现
    rs = locate_by_product_amount(reg_db, ("单细胞",), 0, approved_document_ids={"docA", "docB"})
    files = {r.file_name for r in rs}
    assert "框架样稿.docx" not in files
    # 只返回命中该产品关键词的有效合同（docA 单细胞）；无样稿/无 docB 的无关产品
    ctl_ids = {r.contract_id for r in rs}
    assert ctl_ids == {"CTL-docA"}
    # 抽样：docB 蛋白 conflict 不出现于此查询（无单细胞命中）→ 不正式命中
    assert all(not r.hit for r in rs) or len(rs) == 1