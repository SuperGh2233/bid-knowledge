"""补齐语料内**未提取**的合同（R1-1.b′，方案 C —— 用户 2026-09-15 批准）。

背景与结论（见 `docs/plans/active/PLAN-20260915-demo-feedback-issues.md` R1-1.b′）：
文件名里出现 135 个合同号，`contracts` 只登记 93 个，缺 43 个。根因**不是数据源缺失**，
而是这些合同是**简式/技术开发（委托）合同**，**正文本就没有「服务明细表」** →
`parse_contract_service_table` 抽不出明细 → 建不了 `CTL-` 合同 → **进不了已核白名单** → 检索不到。

**三条链其实是一个约束**：
    抽出服务明细 → 建 CTL 合同 → 进白名单 → 才能被检索

**方案 C（本次实现）**：对**无明细表**且**文件名只含一个产品**的合同，
用**合同总额**作为该产品金额，建一条合成明细行，来源标注为
`product_amount_source='filename_product_total'`（**与真实明细的 `declared` 严格区分、可审计**）。
- ✅ 单产品合同时「合同总额 == 该产品金额」，不是编造；
- ⛔ **多产品合同绝不走此路**（文件名给不出唯一产品时直接跳过）。

同时把新建的 CTL 合同**追加进 `data/approved_documents.json`**（`basis` 明确标注来源），
否则检索的第一道闸（必须在白名单内）仍会挡住它们。

安全：只写测试库 `bid_ai_clean_reg.db`；改前自动备份；NAS 只读；**不调用任何外部网关**；幂等可重跑。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))
os.environ["BID_AI_CLEAN_DB"] = str(CLEAN / "bid_ai_clean_reg.db")

import app.db as db_mod  # noqa: E402
import app.extract as E  # noqa: E402

DB = Path(os.environ["BID_AI_CLEAN_DB"])
APPROVED = CLEAN / "data" / "approved_documents.json"
assert DB.name.endswith("_reg.db") and DB.name != "bid_ai_clean.db", f"拒绝写非测试库：{DB}"

ORDER_NO = re.compile(r"\b((?:BOE|YOE|ZOE|DZOE|YLM)\d{6,})\b", re.I)
PAGE_IMAGE = re.compile(r"_\d{1,3}\.(?:jpg|jpeg|png)$", re.I)
TEXT_FORMATS = ("native_text", "native_pdf_text", "mixed", "scanned_ocr")
# 该来源值 = 「无明细表、按文件名单产品归因」—— 与真实明细 `declared` 严格区分
FILENAME_ATTR = "filename_product_total"


def gap_numbers(con) -> set[str]:
    in_db = {str(r[0]).upper() for r in con.execute(
        "SELECT contract_number FROM contracts WHERE contract_number IS NOT NULL")}
    seen = set()
    for r in con.execute("SELECT relative_path FROM documents"):
        for m in ORDER_NO.finditer(r[0]):
            seen.add(m.group(1).upper())
    return seen - in_db


def gap_docs(con, gaps: set[str]) -> list[dict]:
    out, seen = [], set()
    for r in con.execute("""SELECT d.document_id, d.source_root_id, d.relative_path, d.content_format,
                                   d.parse_status, a.text, a.sha256, a.parser_version
                            FROM documents d
                            LEFT JOIN parse_artifacts a
                              ON a.canonical_document_id = d.canonical_document_id"""):
        rel = r["relative_path"]
        if PAGE_IMAGE.search(rel) or r["document_id"] in seen:
            continue
        if not any(m.group(1).upper() in gaps for m in ORDER_NO.finditer(rel)):
            continue
        seen.add(r["document_id"])
        out.append(dict(r))
    return out


def synth_single_product_record(text: str, filename: str) -> tuple[dict | None, str]:
    """方案 C：**无明细表**时，用「文件名唯一产品 + 合同总额」合成一条明细行。

    返回 (record | None, 跳过原因)。
    只在**文件名给出唯一产品**且**合同头取到金额**时才合成 —— 缺一即跳过（宁缺毋滥）。
    """
    fp = E.product_from_filename(filename)
    if not fp:
        return None, "文件名未识别出唯一产品（可能是多产品合同）"
    hdr = E.contract_header_facts(text, filename)
    total = hdr.get("total_amount")
    if not total or total <= 0:
        return None, "合同头未取到金额"
    if not hdr.get("contract_number"):
        return None, "合同头未取到合同编号"
    return {
        "row_type": "detail",
        "category": fp,                 # product_raw = "{category}/"，产品匹配走类别
        "service_name": "",
        "line_amount": float(total),
        # ⚠️ 来源**必须显式标注**：这是「按文件名归因」，不是正文声明的明细金额。
        "product_amount_source": FILENAME_ATTR,
        "row_text": f"[文件名归因] {filename}（单产品合同、正文无服务明细表；金额=合同总额 {total}）",
    }, ""


def main() -> int:
    con = db_mod.connect()
    gaps = gap_numbers(con)
    docs = gap_docs(con, gaps)
    with_text = [d for d in docs if d["content_format"] in TEXT_FORMATS and (d["text"] or "").strip()]
    no_text = [d for d in docs if d not in with_text]

    print(f"缺口合同号 {len(gaps)} 个；主文档 {len(docs)} 份"
          f"（有正文 {len(with_text)} / 需先 OCR {len(no_text)}）")

    before_c = con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    bak = DB.with_name(f"{DB.stem}.bak-{time.strftime('%Y%m%d-%H%M%S')}-before-planC.db")
    shutil.copy2(DB, bak)
    print(f"备份 → {bak.name}")

    real, synth, skipped, failed = [], [], [], []
    for d in with_text:
        text, rel = d["text"] or "", d["relative_path"]
        name = rel.rsplit("/", 1)[-1]
        try:
            E.extract_and_sync(con, d["document_id"], text)      # 业绩清单（LEDGER-*）
            records = E.parse_contract_service_table(text)
            details = [r for r in records if r.get("row_type") in ("detail", "product_subtotal")]
            if details:
                out = E.sync_contract_service_items(
                    con, d["document_id"], records, source_sha256=d["sha256"],
                    parser_version=d["parser_version"], native_text=text, filename=name)
                real.append((name, len(details), out.get("contract_total")))
                continue
            # —— 方案 C：无明细表 → 单产品按文件名归因 ——
            rec, why = synth_single_product_record(text, name)
            if rec is None:
                skipped.append((name, why))
                continue
            out = E.sync_contract_service_items(
                con, d["document_id"], [rec], source_sha256=d["sha256"],
                parser_version=d["parser_version"], native_text=text, filename=name)
            synth.append((name, rec["category"], rec["line_amount"]))
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {str(exc)[:70]}"))

    after_c = con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    print(f"\n=== 真实明细建 CTL：{len(real)} 份 ===")
    for name, n, tot in real[:10]:
        print(f"   明细 {n:>3} 行 总额={tot}  {name[:56]}")
    print(f"\n=== 方案 C 归因建 CTL：{len(synth)} 份（来源={FILENAME_ATTR}）===")
    for name, prod, amt in synth[:30]:
        print(f"   {amt:>12,.0f}  {prod[:34]:<36} {name[:40]}")
    if skipped:
        print(f"\n=== 跳过 {len(skipped)} 份 ===")
        for name, why in skipped[:12]:
            print(f"   {why}  {name[:52]}")
    if failed:
        print(f"\n=== 失败 {len(failed)} 份 ===")
        for name, why in failed[:8]:
            print(f"   {why}  {name[:50]}")
    print(f"\ncontracts {before_c} → {after_c}（+{after_c - before_c}）")

    # —— 白名单扩充：新建的 CTL 合同必须进白名单，否则检索第一道闸就挡住 ——
    rows = con.execute("""SELECT d.document_id, d.relative_path, d.content_format, d.document_role
        FROM documents d JOIN contracts c ON c.document_id = d.document_id
        WHERE c.contract_id LIKE 'CTL-%'""").fetchall()
    items = json.loads(APPROVED.read_text(encoding="utf-8")) if APPROVED.exists() else []
    have = {str(x.get("document_id")) for x in items}
    added = []
    for r in rows:
        if r["document_id"] in have:
            continue
        src = con.execute("SELECT product_amount_source FROM contract_items WHERE contract_id=?",
                          (f"CTL-{r['document_id']}",)).fetchone()
        basis = ("无明细表单产品归因合同（文件名归因，金额=合同总额）"
                 if src and src[0] == FILENAME_ATTR else "已建 CTL 服务明细的合同（脚本补登）")
        items.append({"document_id": r["document_id"], "content_format": r["content_format"],
                      "role": r["document_role"],
                      "file": r["relative_path"].rsplit("/", 1)[-1], "basis": basis})
        added.append(r["relative_path"].rsplit("/", 1)[-1])
    if added:
        shutil.copy2(APPROVED, APPROVED.with_name(f"approved_documents.bak-{time.strftime('%H%M%S')}.json"))
        APPROVED.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"白名单 {len(items) - len(added)} → {len(items)}（+{len(added)}）")
    for f in added[:12]:
        print(f"   + {f[:62]}")

    remain = gap_numbers(con)
    print(f"\n缺口合同号 {len(gaps)} → 剩 {len(remain)} 个")
    if no_text:
        print(f"仍需先 OCR：{len(no_text)} 份（跑 `python scripts/ocr_batch.py --roles contract_evidence` 后再执行本脚本）")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())