"""响应材料包用途边界测试（用户拍板固化）。

覆盖（真实 classify + 真实 parse_candidates，独立临时库）：
1. 信封/标书封皮 → process_material，不是响应正文，不进解析候选。
2. 实填资格/商务/报价分册 → our_response；分册在无最终证据时不盲目升 final_signed。
3. 缺少最终状态证据 → 不升级 final_signed。
4. 我方主体（投标人=欧易）≠ 文件用途：单证身份不证明响应用途。
5. 入队守卫：final_signed+pending → 可入队；final_signed+holding_review → 不入队。

全部使用独立临时库（monkeypatch DB_PATH 到 tmp_path），不改 reg 库真实记录，
不 OCR / 不写 ES / 不读 NAS。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config as app_config  # noqa: E402
import app.db as db  # noqa: E402
from app.classifier import classify  # noqa: E402
from app.parser import parse_candidates, PARSER_NAME, PARSER_VERSION  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "boundary.db"
    monkeypatch.setattr(app_config, "DB_PATH", str(path))
    db.init_db(force=True)
    con = db.connect()
    yield con
    con.close()


def _register(con, *, doc_id, rel, role, status="pending", override=0,
              root="2026年", project="20260717-标书-王珂-某采购项目重招采购"):
    with con:
        con.execute(
            "INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
            " document_role, manual_override, parse_status) VALUES (?,?,?,?,?,?,?)",
            (doc_id, root, project, rel, role, override, status))


# —— 1) 封皮/信封 → process_material ——
def test_cover_envelope_is_process_material(fresh_db):
    for rel in ("04 信封封皮 一正四副.docx", "04 标书封皮 一正四副.docx"):
        r = classify("项目", rel)
        assert r.role == "process_material", (rel, r)
        _register(fresh_db, doc_id="c-" + str(abs(hash(rel))), rel=rel,
                  role=r.role, status="pending")
    # 封皮不进解析候选
    cand = parse_candidates(fresh_db)
    assert all("封皮" not in c["relative_path"] for c in cand)


# —— 2) 实填分册 → our_response（非 final） ——
def test_substantive_partition_is_our_response(fresh_db):
    for rel in ("01 资格文件部分.docx", "02 商务技术部分.docx", "03 报价部分.docx"):
        r = classify("20260717-标书-王珂-绍兴市中医院科研对外委托服务采购项目重招采购", rel)
        assert r.role == "our_response", (rel, r)
        assert r.role != "final_signed"
        _register(fresh_db, doc_id="d-" + str(abs(hash(rel))), rel=rel,
                  role=r.role, status="pending", root="2026年",
                  project="20260717-标书-王珂-绍兴市中医院科研对外委托服务采购项目重招采购")
    cand = parse_candidates(fresh_db)
    rels = {c["relative_path"] for c in cand}
    assert {"01 资格文件部分.docx", "02 商务技术部分.docx", "03 报价部分.docx"} <= rels


# —— 3) 缺少最终证据不升级 final_signed ——
def test_missing_final_evidence_does_not_upgrade(fresh_db):
    # 分册文件名本身带"正本份数"不是最终证据（仅正本份数要求）
    r = classify("项目", "02 商务技术部分（正本一份，副本四份）.docx")
    assert r.role == "our_response", r
    # 明确"待定稿"不升
    r2 = classify("项目", "欧易/投标文件-待定稿.docx")
    assert r2.role != "final_signed", r2
    # 有我方响应身份+最终状态才升
    r3 = classify("项目", "欧易/响应文件盖章版.pdf")
    assert r3.role == "final_signed", r3


# —— 4) 我方主体 ≠ 响应用途 ——
def test_our_subject_alone_does_not_prove_usage(fresh_db):
    # 社保凭证：盖章扫描件但用途是资质
    r = classify("项目", "社保凭证/欧易生物盖章扫描件_00.jpg")
    assert r.role == "qualification_evidence", r
    assert r.role != "final_signed"
    # 独立合同签章页 → contract_evidence
    r2 = classify("项目", "欧易/合同签章页.pdf")
    assert r2.role == "contract_evidence", r2
    # 发票/付款 → process_material
    r3 = classify("项目", "欧易专用发票盖章版.pdf")
    assert r3.role == "process_material", r3


# —— 5) 入队守卫：final_signed+pending 可入队；holding_review 不入队 ——
def test_final_signed_pending_queues_but_holding_review_not(fresh_db, monkeypatch):
    _register(fresh_db, doc_id="f-ok", rel="欧易/响应文件盖章版.pdf",
              role="final_signed", status="pending", root="2026年",
              project="20260101-标书-某人-某项目")
    _register(fresh_db, doc_id="f-hold", rel="投标文件盖章版-多主体.pdf",
              role="final_signed", status="holding_review", root="2026年",
              project="20260101-标书-某人-某项目")
    _register(fresh_db, doc_id="o-over", rel="欧易/资格文件部分.docx",
              role="our_response", status="pending", override=1, root="2026年",
              project="20260101-标书-某人-某项目")
    cand = parse_candidates(fresh_db)
    ids = {c["document_id"] for c in cand}
    assert "f-ok" in ids, "final_signed+pending 应可入队"
    assert "f-hold" not in ids, "holding_review 待核不得入队"
    assert "o-over" not in ids, "manual_override=1 不得自动列入候选（候选 SQL 排除覆盖）"


# —— 6) manual_override≠统一阻断：已确证的覆盖+可解析角色仍可 process_native ——
def test_manual_override_confirmed_response_still_processable(tmp_path, monkeypatch, fresh_db):
    """临时库中，our_response + manual_override=1 + pending 的行应能被共享入口处理。
    （人工角色纠正应被尊重，不被一刀切挡死；真正阻断是 holding_review/paused。）"""
    import app.config as app_config2  # noqa: F401
    from app import parser as parser_mod
    from app.db import deterministic_document_id, stable_path_key  # noqa: F401
    # 用与 test_parser_cache 一致的临时文件构造
    target = tmp_path / "2026年" / "20260101-标书-某人-某项目" / "响应文件.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    # 微 DOCX
    import zipfile as _z
    body = '<w:p><w:r><w:t xml:space="preserve">已确证的我方响应</w:t></w:r></w:p>'
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{body}<w:sectPr/></w:body></w:document>')
    ts = (2000, 1, 1, 0, 0, 0)
    with _z.ZipFile(target, "w", _z.ZIP_DEFLATED) as z:
        z.writestr(_z.ZipInfo("[Content_Types].xml", ts),
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr(_z.ZipInfo("_rels/.rels", ts),
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
                   ' Target="word/document.xml"/></Relationships>')
        z.writestr(_z.ZipInfo("word/document.xml", ts), xml)
    # 登记：override=1（人工确证）、可解析角色、pending
    _register(fresh_db, doc_id="o-confirmed", rel="20260101-标书-某人-某项目/响应文件.docx",
              role="our_response", status="pending", override=1, root="2026年",
              project="20260101-标书-某人-某项目")
    # 同时登记**不可解析角色**+override → 仍被角色守卫拒绝
    # ⚠️ 2026-09-13 口径变更：`process_material` 已**移出**禁止集（需求要定位的
    # 付款凭证/发票/完税证明住在该角色里，见 `app/parser.py::PARSE_SET_ROLES` 注释）。
    # 守卫本身**未削弱** —— 改用语义上确实不该解析的 `competitor_response`（竞品）来验证。
    _register(fresh_db, doc_id="bg-confirmed", rel="p2/竞品响应.png",
              role="competitor_response", status="pending", override=1, root="2026年",
              project="p2")
    # process_native 直接调用（绕过候选 SQL）→ override=1 可解析角色应被处理
    roots = {"2026年": tmp_path / "2026年"}
    rows = {r["document_id"]: dict(r) for r in fresh_db.execute(
        "SELECT * FROM documents WHERE document_id IN ('o-confirmed','bg-confirmed')")}
    r_ok = parser_mod.process_native(fresh_db, rows["o-confirmed"], roots)
    assert r_ok.error is None and r_ok.new_artifact and r_ok.chars and r_ok.chars > 0, \
        "已确证人工覆盖 + 可解析角色应能处理（覆盖不是统一阻断）"
    # 角色不在解析集 → 拒绝（override 不豁免角色边界）
    r_bg = parser_mod.process_native(fresh_db, rows["bg-confirmed"], roots)
    assert r_bg.error is not None and "角色不在解析集" in r_bg.error


def test_material_roles_are_now_parseable_but_competitor_is_not():
    """材料角色已放开、竞品/空模板仍禁止（2026-09-13 需求一三模块定位的前提）。

    需求要定位的三类材料的正文**住在这些角色里**，不放开则整块读不出来：
      财务社保 → qualification_evidence / tender_requirement
      仪器设备 → process_material（发票）
      收款凭证 → process_material
    而竞品响应/空模板/系统文件**仍不得解析** —— 放开的是材料来源，不是对齐口径。
    """
    from app.parser import PARSE_SET_ROLES

    for role in ("process_material", "qualification_evidence", "tender_requirement", "unknown"):
        assert role in PARSE_SET_ROLES, f"{role} 应可作为材料来源被解析"
    for role in ("competitor_response", "blank_template", "system_or_temp"):
        assert role not in PARSE_SET_ROLES, f"{role} 不得进入解析集"
