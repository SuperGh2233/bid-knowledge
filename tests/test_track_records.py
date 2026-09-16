"""业绩清单（响应文件内的历史合同声明）检索 —— 护栏测试。

由来：PLAN-20260916-track-record-search。三条红线必须**可执行地**钉住：

1. **不参与金额过滤** —— `locate_track_records` 签名里**没有金额参数**；
   `_ledger_for` 在有金额条件时返回 `None`（金额查询里出现业绩行 = 把「合同总额」
   当成了「产品金额」，与项目既有红线直接冲突）。
2. **独立闸** —— 只收 `our_response` / `final_signed`；且**不查** `approved_documents.json`
   （那份白名单的语义是「已核**合同原件**」，混入会让 `/api/status` 的
   `queryable_contracts` 与"已核"含义一起失真）。
3. **单列来源标签** —— 每条必须带 `source_label`，且金额旁边必须有语义说明
   （它是**合同总额**）—— 否则展示层会把它当产品金额用。
"""
from __future__ import annotations

import inspect
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.api  # noqa: F401,E402 —— **必须先加载 app.api**：直接导入 routes_search 会触发
                # 循环导入（routes_search 顶部 from app.api import …，而 api.py 末尾又
                # include_router(routes_search.router)）。本项目既有测试同样这么写。
from app.routes_search import _ledger_for  # noqa: E402
from app.search import TRACK_SOURCE_LABEL, locate_track_records  # noqa: E402


def _con(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "_track.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(
        "CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
        " source_root_id TEXT, project_folder TEXT, document_role TEXT, content_format TEXT);"
        "CREATE TABLE contracts (contract_id TEXT PRIMARY KEY, document_id TEXT, ordinal INTEGER,"
        " contract_number TEXT, party_a TEXT, total_amount REAL, evidence_text TEXT);")
    con.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?)", [
        ("d1", "P/欧易响应文件-1.docx", "2025年", "proj-A", "our_response", "native_text"),
        ("d2", "P/欧易响应文件-2.docx", "2025年", "proj-B", "our_response", "native_text"),
        ("d3", "P/合同正本.pdf", "2025年", "proj-C", "contract_evidence", "scanned_ocr"),
    ])
    con.executemany("INSERT INTO contracts VALUES (?,?,?,?,?,?,?)", [
        ("LEDGER-d1-1", "d1", 1, None, "上海某医院", 308000.0,
         "[业绩清单] 近三年主要项目 | 行1: 业绩1 | 项目:单细胞转录组测序服务 "
         "采购人:上海某医院 金额:308,000元 | 位置:line12"),
        ("LEDGER-d1-2", "d1", 2, None, "北京某大学", None,
         "[业绩清单] 近三年主要项目 | 行2: 业绩2 | 项目:代谢组学检测 "
         "采购人:北京某大学 | 位置:line13"),
        # ⚠️ 角色是 contract_evidence 的业绩行**不得**返回（独立闸）
        ("LEDGER-d3-1", "d3", 1, None, "某医院", 999999.0,
         "[业绩清单] x | 行1: y | 项目:不该出现 采购人:某医院 金额:999,999元 | 位置:line9"),
        # 合同原件（CTL-*）不属于业绩清单通道
        ("CTL-abc123", "d3", None, "YOE2024001", "上海某医院", 500000.0, "合同正文"),
    ])
    con.commit()
    return con


# —— 红线 1：不参与金额过滤（结构 + 行为双重钉住）——

def test_amount_threshold_filters_but_surfaces_exclusions(tmp_path):
    """金额门槛**已下沉到业绩行**（2026-09-16 用户改判：非 PDF 的合同金额也要参与金额条件）。

    改判依据见 `locate_track_records` docstring（实测误返 0 份）。**必守的两条诚实性**：
      ① 未达门槛的**只报数**（不静默丢）；
      ② 金额未记载的**单列给出文件**（业务能自己核对），而不是消失。
    """
    con = _con(tmp_path)
    con.execute("INSERT INTO contracts VALUES ('LEDGER-d1-3','d1',3,NULL,'天津某院',1000.0,"
                "'[业绩清单] x | 行3: y | 项目:小额项目 采购人:天津某院 金额:1,000元')")
    con.commit()
    allrows = locate_track_records(con)
    assert allrows["count"] == 3 and allrows["excluded_below_amount"] == 0
    d = locate_track_records(con, min_amount=50000.0)
    assert {r["contract_id"] for r in d["records"]} == {"LEDGER-d1-1"}      # 只留 ≥5万 的
    assert d["excluded_below_amount"] == 1                                  # 1,000 元那条：**报数**
    assert d["excluded_no_amount_count"] == 1                               # 无金额那条：**单列**
    assert d["excluded_no_amount"][0]["party_a"] == "北京某大学"
    assert "已按金额门槛筛选" in d["scope_note"] and "合同总额" in d["scope_note"]


# —— 红线 2：独立闸（角色）——

def test_only_our_response_and_final_signed_roles(tmp_path):
    con = _con(tmp_path)
    got = locate_track_records(con)
    ids = {r["contract_id"] for r in got["records"]}
    assert ids == {"LEDGER-d1-1", "LEDGER-d1-2"}, ids          # d3（合同原件角色）被挡
    from app.search import TRACK_RECORD_ROLES
    assert {r["document_role"] for r in got["records"]} <= set(TRACK_RECORD_ROLES)


def test_never_returns_contract_originals(tmp_path):
    """合同原件（CTL-*）不走业绩通道 —— 两个来源不得混在同一个列表里。"""
    con = _con(tmp_path)
    assert all(r["contract_id"].startswith("LEDGER-")
               for r in locate_track_records(con)["records"])


# —— 红线 3：来源标签与金额语义说明必须同行 ——

def test_every_record_carries_source_label_and_amount_note(tmp_path):
    con = _con(tmp_path)
    d = locate_track_records(con)
    assert d["source_label"] == TRACK_SOURCE_LABEL
    assert "响应文件" in d["source_label"]
    for r in d["records"]:
        assert r["source_label"] == TRACK_SOURCE_LABEL
        assert "不参与金额筛选" in r["amount_note"]
    # 口径改判后：说明里**必须**仍写明金额是"合同总额、非产品明细金额"（诚实性不随口径变）
    assert "合同总额" in d["scope_note"] and "非产品明细金额" in d["scope_note"]


def test_project_name_extracted_from_evidence(tmp_path):
    """项目名从证据文本里解析出来（竖排格式的 row_text 只有「业绩N」，项目名在别处）。"""
    con = _con(tmp_path)
    rec = {r["contract_id"]: r for r in locate_track_records(con)["records"]}
    assert rec["LEDGER-d1-1"]["project"] == "单细胞转录组测序服务"
    assert rec["LEDGER-d1-1"]["amount"] == 308000.0
    assert rec["LEDGER-d1-2"]["amount"] is None      # 提不到 → None，不是 0


# —— 过滤口径 ——

def test_filters_by_party_product_and_year(tmp_path):
    con = _con(tmp_path)
    assert locate_track_records(con, party="上海")["count"] == 1
    assert locate_track_records(con, party="不存在")["count"] == 0
    assert locate_track_records(con, products=("单细胞",))["count"] == 1
    assert locate_track_records(con, products=("代谢组",))["count"] == 1
    assert locate_track_records(con, products=("宏基因组",))["count"] == 0
    # 年份按证据文本匹配（年份不在独立列里）
    assert locate_track_records(con, year="line")["count"] == 2


def test_truncation_is_reported(tmp_path):
    """截断必须**说出来** —— 静默截断会让用户以为「只有这几条」。"""
    con = _con(tmp_path)
    d = locate_track_records(con, limit=1)
    assert len(d["records"]) == 1 and d["count"] == 2 and d["truncated"] is True
    d2 = locate_track_records(con, limit=50)
    assert d2["truncated"] is False


# —— 接线测试（这一条是补的：曾把追加写在 return 之后成了死代码，端点静默不带 ledger）——

def test_contract_search_body_wires_ledger(monkeypatch):
    """`_contract_search_body` **真的把 `ledger` 放进返回体** —— 且位置在 return 之前。

    由来：2026-09-16 首次实现时把 `resp["ledger"] = …` 写在了 `return {…}` **之后**（不可达），
    单测只测 `_ledger_for` 本身 → 全绿，而端点不带 `ledger`。**"函数对"不等于"接线对"。**
    """
    import app.routes_search as R
    monkeypatch.setattr(R, "live_scope", lambda con: {"queryable_contracts": 136})
    monkeypatch.setattr(R, "locate_by_product_amount", lambda *a, **k: [])
    monkeypatch.setattr(R, "_ledger_for", lambda con, **k: {"records": [{"contract_id": "L1"}], "count": 1})

    out = R._contract_search_body(None, product="代谢组", keywords=("代谢组",), minimum=0.0,
                                  date_from=None, date_to=None, party_inc=(), party_exc=(),
                                  prod_exc_keys=(), prod_exc=())
    assert "ledger" in out, "接线断了：返回体里没有 ledger（检查是否写成了 return 之后的死代码）"
    assert out["ledger"]["count"] == 1
    assert out["hits"] == [] and out["excluded"] == []


def test_contract_search_body_keeps_ledger_out_of_hits(monkeypatch):
    """金额查询里：业绩段**在**（用户要的），但业绩行**绝不混进 `hits`**（红线）。"""
    import app.routes_search as R
    monkeypatch.setattr(R, "live_scope", lambda con: {"queryable_contracts": 136})
    monkeypatch.setattr(R, "locate_by_product_amount", lambda *a, **k: [])
    monkeypatch.setattr(R, "_ledger_for", lambda con, **k: {
        "records": [{"contract_id": "LEDGER-x-1", "source_label": "业绩清单声明（我方响应文件）"}],
        "count": 1, "amount_condition": True})

    out = R._contract_search_body(None, product="单细胞", keywords=("单细胞",), minimum=50000.0,
                                  date_from=None, date_to=None, party_inc=(), party_exc=(),
                                  prod_exc_keys=(), prod_exc=())
    assert out["ledger"]["amount_condition"] is True
    assert out["hits"] == [] and out["excluded"] == []          # 业绩行没有进任何合同命中集
    assert "LEDGER-" not in str(out["hits"])


def test_ledger_not_filtered_by_query_party(tmp_path):
    """业绩行**不按查询里的「乙方」过滤** —— 查询里的乙方是供应商，业绩行的 `party_a` 是客户。

    实测踩过：按 party 过滤后 `乙方是欧易的代谢组合同` 返回 0 条业绩，而库里确有 9 条代谢组业绩
    （业绩行的采购人是医院/大学，永远不等于「欧易」）。
    """
    con = _con(tmp_path)
    d = _ledger_for(con, minimum=0.0, keywords=("单细胞",))
    assert d is not None and d["count"] == 1        # 只按产品词筛，未被任何 party 杀掉


def test_duplicate_claims_are_folded(tmp_path):
    """**同一行业绩声明在多份文件里各存一份 → 只展示一次**（保留来源）。

    实测由来：同一标的的两次投递（`20260717` 与 `20260812` 两个项目文件夹）各有一份
    `02 商务技术部分.docx`，**canonical 不同**（文件被重新保存过）→ 按 canonical 去重拦不住，
    页面出现两张一模一样的卡片。业绩行的身份 = 它声明的内容本身 → 用证据文本折叠。
    """
    con = _con(tmp_path)
    dup = ("LEDGER-d2-1", "d2", 1, None, "上海某医院", 308000.0,
           "[业绩清单] 近三年主要项目 | 行1: 业绩1 | 项目:单细胞转录组测序服务 "
           "采购人:上海某医院 金额:308,000元 | 位置:line12")   # 与 d1 的第 1 行**逐字相同**
    con.execute("INSERT INTO contracts VALUES (?,?,?,?,?,?,?)", dup)
    con.commit()
    d = locate_track_records(con)
    ids = [r["contract_id"] for r in d["records"]]
    assert ids.count("LEDGER-d1-1") == 1 and "LEDGER-d2-1" not in ids, ids
    assert d["count"] == 2 and d["raw_count"] == 3 and d["folded_copies"] == 1
    folded = next(r for r in d["records"] if r["contract_id"] == "LEDGER-d1-1")
    assert folded["copy_count"] == 2 and len(folded["also_in"]) == 1
    assert folded["also_in"][0]["document_id"] == "d2"      # 副本位置**如实保留**
