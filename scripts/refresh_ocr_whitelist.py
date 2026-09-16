"""白名单 OCR：只处理**本次指定**的 my 方响应扫描 PDF（精确驱动，不外扩范围）。

为什么存在（2026-09-15）：`scripts/ocr_batch.py` 是全量候选 —— 会误把 45 份
`native_pdf_text`（有原生文字层，OCR 是白外发）、12 份 `holding_review`（待核红线）
一起列进候选，还会把历史约 186 份存量欠账带进来。用户裁定选 X：**只补本次新增的
16 份扫描 PDF**，其余留待单独商议。

授权依据：`docs/authorizations/ocr-authorization-response-docs.md`（2026-09-11）—— our_response /
final_signed 扫描件 OCR 外发已获书面授权。**不含**竞品/招标/未知角色（代码层过滤）。

白名单口径（与刷新登记新增集一致）：
  document_role IN ('our_response','final_signed')
  AND lower(relative_path) LIKE '%.pdf'
  AND parse_status IS NULL            -- 本次新增、未解析
  AND content_format != 'native_pdf_text'  -- 有原生文字层不 OCR
  AND parse_status != 'holding_review'

幂等/续跑：状态与 `ocr_batch.py` 共用 `data/ocr_batch_state_our_response_final_signed.json`
（同粒度、同语义）—— 被杀重跑自动跳过已完成；同 SHA 已有 scanned_ocr 产物则不重复外发。

用法（外发，需 `!` 前缀由你执行）：
  ! python scripts/refresh_ocr_whitelist.py --dry-run     # 先看将 OCR 哪 16 份（不发送）
  ! python scripts/refresh_ocr_whitelist.py                # 真实 OCR（约 1,700 页，断点续跑）
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))

from dotenv import dotenv_values  # noqa: E402

_env = dotenv_values(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai\.env")
os_environ_update = {
    "OCR_ENABLED": "true",                          # 授权守卫（外发需通过 _guard）
    "OCR_BASE_URL": _env.get("LLM_BASE_URL", ""),
    "OCR_API_KEY": _env.get("LLM_API_KEY", ""),
    "OCR_MODEL": _env.get("OCR_MODEL") or _env.get("LLM_MODEL", ""),
    "OCR_FALLBACK_MINERU": "false",                 # 不用公网兜底
    "BID_AI_CLEAN_DB": str(CLEAN / "bid_ai_clean_reg.db"),
}
import os  # noqa: E402

for _k, _v in os_environ_update.items():
    os.environ[_k] = _v

import app.config as config  # noqa: E402
import app.db as db_mod  # noqa: E402
import app.ocr as ocr  # noqa: E402
from app.parser import PARSER_NAME, PARSER_VERSION, file_sha_from_bytes  # noqa: E402

ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}
STATE_FILE = CLEAN / "data" / "ocr_batch_state_our_response_final_signed.json"
LOG_FILE = CLEAN / "tmp" / "ocr_whitelist.log"
DB_PATH = Path(os.environ["BID_AI_CLEAN_DB"])

SELECT_SQL = """
SELECT document_id, source_root_id, project_folder, relative_path, file_size
FROM documents
WHERE document_role IN ('our_response','final_signed')
  AND lower(relative_path) LIKE '%.pdf'
  AND parse_status IS NULL
  AND (content_format IS NULL OR content_format != 'native_pdf_text')
ORDER BY file_size
"""


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with io.open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="白名单 OCR：只 OCR 本次新增的 my 方响应扫描 PDF")
    ap.add_argument("--dry-run", action="store_true", help="只列出将 OCR 的文件（不发送网关）")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    docs = [dict(r) for r in con.execute(SELECT_SQL)]
    log(f"白名单候选（our_response/final_signed · PDF · 未解析无文字层）: {len(docs)} 份")

    if args.dry_run:
        print("── 将 OCR 的白名单（dry-run，未发送）──")
        for d in docs:
            print(f"   {d['file_size'] / 1e6:6.1f}MB  {d['relative_path'][-58:]}")
        print(f"  共 {len(docs)} 份")
        con.close()
        return 0

    # 状态文件：与 ocr_batch 同一份（幂等续跑共享）
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    todo = [d for d in docs if state.get(d["document_id"], {}).get("status") != "done"]
    log(f"本轮待 OCR {len(todo)} 份（{len(docs) - len(todo)} 份已 done）")

    t0 = time.time()
    ok = empty = fail = skipped = 0
    for i, d in enumerate(todo, 1):
        p = ROOTS.get(d["source_root_id"])
        if p is None:
            state[d["document_id"]] = {"status": "error", "note": "未知 source_root_id",
                                       "file": d["relative_path"].rsplit("/", 1)[-1][:90]}
            fail += 1
            continue
        path = p.joinpath(*d["relative_path"].split("/"))
        name = d["relative_path"].rsplit("/", 1)[-1]
        try:
            if not path.exists():
                state[d["document_id"]] = {"status": "done", "note": "源文件不可达",
                                           "file": name[:90]}
                _save_state(state); skipped += 1
                continue
            raw = path.read_bytes()
            sha = file_sha_from_bytes(raw)
            # 幂等：同 SHA 已有 scanned_ocr 产物 → 跳过（不重复外发）
            hit = con.execute(
                "SELECT 1 FROM parse_artifacts WHERE sha256=? AND content_format='scanned_ocr'",
                (sha,)).fetchone()
            if hit:
                state[d["document_id"]] = {"status": "done", "note": "已有同SHA OCR产物",
                                           "file": name[:90], "sha": sha[:16]}
                _save_state(state); skipped += 1
                continue
            res = ocr.ocr_pdf(path)
            if not res.text.strip():
                state[d["document_id"]] = {"status": "done", "note": "未识别出文字",
                                           "file": name[:90], "sha": sha[:16],
                                           "pages_total": res.page_count}
                _save_state(state); empty += 1
                continue
            meta = {"pages": res.page_count, "order": "body",
                    "ocr": {"pages": res.pages, "methods": sorted(res.methods),
                            "gateway": config.OCR_BASE_URL}}
            with con:
                con.execute(
                    "INSERT OR REPLACE INTO parse_artifacts "
                    "(canonical_document_id, sha256, text, page_metadata, content_format,"
                    " parser_name, parser_version) VALUES (?,?,?,?,?,?,?)",
                    (sha, sha, res.text, json.dumps(meta, ensure_ascii=False),
                     "scanned_ocr", PARSER_NAME, PARSER_VERSION))
                con.execute(
                    "UPDATE documents SET canonical_document_id=?, sha256=?, content_format=?,"
                    " error_message=NULL WHERE document_id=?",
                    (sha, sha, "scanned_ocr", d["document_id"]))
            state[d["document_id"]] = {"status": "done", "chars": len(res.text),
                                       "pages_ok": res.ok_pages, "pages_total": res.page_count,
                                       "file": name[:90], "sha": sha[:16]}
            _save_state(state); ok += 1
            log(f"  [{i}/{len(todo)}] ok {res.ok_pages}/{res.page_count}页 {len(res.text):,}字 {name[:56]}")
        except Exception as exc:  # noqa: BLE001 —— 单文件失败不阻塞整批
            state[d["document_id"]] = {"status": "error", "note": f"{type(exc).__name__}: {str(exc)[:110]}",
                                       "file": name[:90]}
            _save_state(state); fail += 1
            log(f"  [{i}/{len(todo)}] ✗ {name[:56]}  {type(exc).__name__}: {str(exc)[:70]}")
    con.close()
    dt = time.time() - t0
    log(f"本轮结束：成功 {ok} / 空 {empty} / 失败 {fail} / 跳过 {skipped}，用时 {dt / 60:.1f} 分钟")
    log(f"总进度：{sum(1 for v in state.values() if v.get('status') == 'done')}/{len(docs)}")
    return 0


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())