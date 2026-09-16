"""白名单 OCR：补齐**未提取合同的缺口文档**（R1-1.b′ 收尾，2026-09-15）。

只处理 `scripts/backfill_missing_contracts.py` 判定为「缺口合同、且**无正文**」的文档 ——
不碰 `ocr_batch.py` 的全量候选（那有 1331 份 `contract_evidence`，远超本次范围）。

授权依据：`docs/authorizations/ocr-authorization.md` —— `contract_evidence` 扫描件 OCR 外发**已有书面授权**。
安全：NAS 只读；只写测试库；与 `ocr_batch.py` 共用 state 文件语义（幂等、可续跑）。

用法（**外发**，需 `!` 前缀由用户执行）：
  ! python scripts/ocr_backfill_contracts.py --dry-run   # 先看将 OCR 哪几份
  ! python scripts/ocr_backfill_contracts.py              # 真实 OCR
之后再跑 `python scripts/backfill_missing_contracts.py` 完成提取。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))

from dotenv import dotenv_values  # noqa: E402

_env = dotenv_values(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai\.env")
for _k, _v in {
    "OCR_ENABLED": "true",
    "OCR_BASE_URL": _env.get("LLM_BASE_URL", ""),
    "OCR_API_KEY": _env.get("LLM_API_KEY", ""),
    "OCR_MODEL": _env.get("OCR_MODEL") or _env.get("LLM_MODEL", ""),
    "OCR_FALLBACK_MINERU": "false",
    "BID_AI_CLEAN_DB": str(CLEAN / "bid_ai_clean_reg.db"),
}.items():
    os.environ[_k] = _v

import app.config as config  # noqa: E402
import app.db as db_mod  # noqa: E402
import app.ocr as ocr  # noqa: E402
import scripts.backfill_missing_contracts as B  # noqa: E402
from app.parser import PARSER_NAME, PARSER_VERSION, file_sha_from_bytes  # noqa: E402

ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}
STATE = CLEAN / "data" / "ocr_batch_state_contract_backfill.json"
LOG = CLEAN / "tmp" / "ocr_backfill_contracts.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with io.open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def pending_docs() -> list[dict]:
    """缺口合同、且无正文的文档（本脚本的处理范围）。"""
    con = db_mod.connect()
    try:
        gaps = B.gap_numbers(con)
        return [d for d in B.gap_docs(con, gaps)
                if not (d["content_format"] in B.TEXT_FORMATS and (d["text"] or "").strip())]
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="白名单 OCR：缺口合同的无正文文档")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    docs = pending_docs()
    log(f"缺口合同待 OCR 文档: {len(docs)} 份")
    if args.dry_run:
        for d in docs:
            print(f"   {d['parse_status'] or '未解析':<10} {d['relative_path'][-66:]}")
        return 0

    con = db_mod.connect()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    todo = [d for d in docs if state.get(d["document_id"], {}).get("status") != "done"]
    log(f"本轮处理 {len(todo)} 份")
    ok = fail = 0
    for i, d in enumerate(todo, 1):
        root = ROOTS.get(d["source_root_id"] if "source_root_id" in d.keys() else "", None)
        p = (root / d["relative_path"]) if root else None
        name = d["relative_path"].rsplit("/", 1)[-1]
        try:
            if p is None or not p.exists():
                state[d["document_id"]] = {"status": "done", "note": "源文件不可达", "file": name[:90]}
                STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                continue
            raw = p.read_bytes()
            sha = file_sha_from_bytes(raw)
            hit = con.execute("SELECT 1 FROM parse_artifacts WHERE sha256=? AND content_format='scanned_ocr'",
                              (sha,)).fetchone()
            if hit:
                state[d["document_id"]] = {"status": "done", "note": "已有同SHA OCR产物", "file": name[:90]}
                continue
            res = ocr.ocr_pdf(p) if p.suffix.lower() == ".pdf" else ocr.ocr_image_file(p)
            if not res.text.strip():
                state[d["document_id"]] = {"status": "done", "note": "未识别出文字", "file": name[:90]}
                log(f"  [{i}/{len(todo)}] 空 {name[:52]}")
                continue
            meta = {"pages": res.page_count, "order": "body",
                    "ocr": {"pages": res.pages, "methods": sorted(res.methods),
                            "gateway": config.OCR_BASE_URL}}
            with con:
                con.execute("INSERT OR REPLACE INTO parse_artifacts (canonical_document_id, sha256, text,"
                            " page_metadata, content_format, parser_name, parser_version) VALUES (?,?,?,?,?,?,?)",
                            (sha, sha, res.text, json.dumps(meta, ensure_ascii=False),
                             "scanned_ocr", PARSER_NAME, PARSER_VERSION))
                con.execute("UPDATE documents SET canonical_document_id=?, sha256=?, content_format=?,"
                            " error_message=NULL WHERE document_id=?",
                            (sha, sha, "scanned_ocr", d["document_id"]))
            state[d["document_id"]] = {"status": "done", "chars": len(res.text),
                                       "pages_ok": res.ok_pages, "pages_total": res.page_count, "file": name[:90]}
            ok += 1
            log(f"  [{i}/{len(todo)}] ok {res.ok_pages}/{res.page_count}页 {len(res.text):,}字 {name[:50]}")
        except Exception as exc:  # noqa: BLE001
            state[d["document_id"]] = {"status": "error", "note": f"{type(exc).__name__}: {str(exc)[:100]}",
                                       "file": name[:90]}
            fail += 1
            log(f"  [{i}/{len(todo)}] ✗ {name[:50]}  {type(exc).__name__}: {str(exc)[:60]}")
        finally:
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    con.close()
    log(f"本轮结束：成功 {ok} / 失败 {fail}")
    log("→ 接着跑 `python scripts/backfill_missing_contracts.py` 完成合同提取")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())