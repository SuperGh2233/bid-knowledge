"""批量 OCR：扫描 PDF 与图片。**可断点续跑**。

授权依据（**两份**）：
  · `docs/authorizations/ocr-authorization.md`（2026-09-10）—— `contract_evidence` 扫描件。
  · `docs/authorizations/ocr-authorization-response-docs.md`（2026-09-11）—— **`our_response` / `final_signed`** 扫描件
    （用户就「640 份必须重做 OCR（外发）」回复「允许」）。**不含**竞品/招标/未知角色。

关键性质：
  - **可续跑**：状态文件按文件粒度落盘，进程被杀后重跑自动跳过已完成项。
  - **幂等**：同一内容 SHA 已有 scanned_ocr 产物则跳过，不重复外发。
  - **单文件失败不阻塞**：逐文件 try，失败如实记录，继续下一个。
  - **角色可选**（`--roles`）：默认仍只处理 `contract_evidence`，扩大范围须显式传参；
    状态文件**按角色分开**，避免换角色跑时误读另一批的完成记录。

用法：
  python scripts/ocr_batch.py [--limit N] [--pdf-only] [--roles our_response,final_signed] [--max-mb 5]

**分阶段推进**：候选 598 份约 72,500 页，全量约 60–121 小时。
但一半以上文件不到 2 MB、只占 1% 字节 —— 用 `--max-mb` 先做便宜的大多数。
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
os.environ.update({
    "OCR_ENABLED": "true",
    "OCR_BASE_URL": _env.get("LLM_BASE_URL", ""),
    "OCR_API_KEY": _env.get("LLM_API_KEY", ""),
    "OCR_MODEL": _env.get("OCR_MODEL") or _env.get("LLM_MODEL", ""),
    "OCR_FALLBACK_MINERU": "false",
    "BID_AI_CLEAN_DB": str(CLEAN / "bid_ai_clean_reg.db"),
})

import app.config as config  # noqa: E402
import app.db as db_mod  # noqa: E402
import app.ocr as ocr  # noqa: E402
from app.parser import PARSER_NAME, PARSER_VERSION, file_sha_from_bytes  # noqa: E402

ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}
STATE_PATH = CLEAN / "data" / "ocr_batch_state.json"
LOG_PATH = CLEAN / "tmp" / "ocr_batch.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with io.open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def contract_base(name: str) -> str:
    """合同基名：去掉扩展名与逐页后缀 `_NN` / `-NN`。

    注意只剥 `[_\\-]\\d{1,3}$`，不能用 `\\d{1,3}$`——后者会把金额末尾的 `00`
    （如 `-35,000.00`）也剥掉，导致同名合同失配。
    """
    import re as _re
    s = _re.sub(r"\.(pdf|jpg|jpeg|png)$", "", name, flags=_re.I)
    return _re.sub(r"[_\-]\d{1,3}$", "", s).strip().lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--pdf-only", action="store_true")
    ap.add_argument("--roles", default="contract_evidence",
                    help="要处理的文件角色，逗号分隔。扩大范围见 docs/authorizations/ocr-authorization-response-docs.md")
    ap.add_argument("--max-mb", type=float, default=None,
                    help="只处理小于该体积(MB)的文件 —— 用于**分阶段推进**（大文件少而贵）")
    ap.add_argument("--db", default=None, help="覆盖测试库路径（默认取 BID_AI_CLEAN_DB）")
    ap.add_argument("--local", action="store_true",
                    help="用**本机 rapidocr**（零外发、不需授权）；不加则走外发网关（需 OCR_ENABLED + 授权）")
    args = ap.parse_args()

    if args.local:
        os.environ["OCR_BACKEND"] = "local"
        import importlib
        importlib.reload(config)          # config 在 import 时读环境变量，需重载
        importlib.reload(ocr)
    local = ocr.use_local_backend()
    # 外发网关需要 OCR_ENABLED（授权守卫）；**本地后端不外发，不需要**
    assert local or config.OCR_ENABLED, "OCR 未启用（外发网关需 OCR_ENABLED=true；或加 --local 走本机）"
    con = db_mod.connect()
    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    # 状态文件**按角色分开**：否则换角色跑会读到另一批的 done 记录，误判为已完成
    state_path = STATE_PATH if roles == ("contract_evidence",) else \
        STATE_PATH.with_name(f"ocr_batch_state_{'_'.join(roles)}.json")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    def _save() -> None:
        """状态落盘到**本角色专属**文件（断点续跑的依据）。"""
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    exts = ("%.pdf",) if args.pdf_only else ("%.pdf", "%.jpg", "%.jpeg", "%.png")
    where = " OR ".join(f"lower(relative_path) LIKE '{e}'" for e in exts)
    role_sql = ",".join("?" * len(roles))
    sql = (f"SELECT document_id, source_root_id, project_folder, relative_path, file_size "
           f"FROM documents WHERE document_role IN ({role_sql}) AND ({where})")
    params: list = list(roles)
    if args.max_mb is not None:
        sql += " AND file_size IS NOT NULL AND file_size < ?"
        params.append(int(args.max_mb * 1_000_000))
    docs = [dict(r) for r in con.execute(sql + " ORDER BY document_id", params)]

    # —— 内容级去重（D3A 同理）：图片若只是**已 OCR 合同**的逐页导出，跳过 ——
    # 实测：1174 张图片只对应 90 个合同，其中 1098 张（93.5%）的合同 PDF 已 OCR 过。
    # 不做这层去重会重复外发上千次、白跑约 80 分钟。
    # 基名直接**查库**而非读 state（state 里文件名被截断，长名会失配）。
    done_bases = {contract_base(r[0].rsplit("/", 1)[-1]) for r in con.execute(
        "SELECT d.relative_path FROM documents d "
        "JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id "
        "WHERE d.document_role='contract_evidence' AND a.content_format='scanned_ocr' "
        "AND lower(d.relative_path) LIKE '%.pdf'")}
    deduped, dup = [], 0
    for d in docs:
        rel = d["relative_path"]
        if rel.lower().endswith((".jpg", ".jpeg", ".png")):
            if contract_base(rel.rsplit("/", 1)[-1]) in done_bases:
                dup += 1
                state.setdefault(d["document_id"], {"status": "done",
                                                    "note": "合同 PDF 已 OCR，逐页图为重复内容",
                                                    "file": rel.rsplit("/", 1)[-1][:90]})
                continue
        deduped.append(d)
    if dup:
        _save()
        log(f"内容去重：跳过 {dup} 张（其合同 PDF 已 OCR）")

    todo = [d for d in deduped if state.get(d["document_id"], {}).get("status") != "done"]
    if args.limit:
        todo = todo[:args.limit]
    log(f"候选 {len(docs)} 份（去重后 {len(deduped)}）；本轮处理 {len(todo)} 份")

    t0 = time.time()
    ok = empty = fail = skipped = 0
    for i, d in enumerate(todo, 1):
        p = ROOTS[d["source_root_id"]].joinpath(*d["relative_path"].split("/"))
        name = d["relative_path"].rsplit("/", 1)[-1]
        try:
            if not p.exists():
                state[d["document_id"]] = {"status": "done", "note": "源文件不可达",
                                           "file": name[:90]}
                _save(); skipped += 1
                continue
            raw = p.read_bytes()
            sha = file_sha_from_bytes(raw)
            # 幂等：同 SHA 已有 scanned_ocr 产物 → 跳过（不重复外发）
            hit = con.execute("SELECT 1 FROM parse_artifacts WHERE sha256=? AND content_format='scanned_ocr'",
                              (sha,)).fetchone()
            if hit:
                state[d["document_id"]] = {"status": "done", "note": "已有同SHA OCR产物",
                                           "file": name[:90], "sha": sha[:16]}
                _save(); skipped += 1
                continue

            res = (ocr.ocr_pdf(p) if p.suffix.lower() == ".pdf" else ocr.ocr_image_file(p))
            if not res.text.strip():
                state[d["document_id"]] = {"status": "done", "note": "未识别出文字",
                                           "file": name[:90], "sha": sha[:16],
                                           "pages_total": res.page_count}
                _save(); empty += 1
                log(f"  [{i}/{len(todo)}] 空 {name[:60]}")
                continue
            meta = {"pages": res.page_count, "order": "body",
                    "ocr": {"pages": res.pages, "methods": sorted(res.methods),
                            "gateway": config.OCR_BASE_URL}}
            with con:
                con.execute("INSERT OR REPLACE INTO parse_artifacts "
                            "(canonical_document_id, sha256, text, page_metadata, content_format, parser_name, parser_version) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (sha, sha, res.text, json.dumps(meta, ensure_ascii=False),
                             "scanned_ocr", PARSER_NAME, PARSER_VERSION))
                con.execute("UPDATE documents SET canonical_document_id=?, sha256=?, content_format=?, "
                            "error_message=NULL WHERE document_id=?", (sha, sha, "scanned_ocr", d["document_id"]))
            state[d["document_id"]] = {"status": "done", "chars": len(res.text),
                                       "pages_ok": res.ok_pages, "pages_total": res.page_count,
                                       "file": name[:90], "sha": sha[:16]}
            _save(); ok += 1
            log(f"  [{i}/{len(todo)}] ✔ {res.ok_pages}/{res.page_count}页 {len(res.text):,}字  {name[:56]}")
        except Exception as exc:  # noqa: BLE001
            state[d["document_id"]] = {"status": "error", "note": f"{type(exc).__name__}: {str(exc)[:110]}",
                                       "file": name[:90]}
            _save(); fail += 1
            log(f"  [{i}/{len(todo)}] ✗ {name[:56]}  {type(exc).__name__}: {str(exc)[:70]}")

    dt = time.time() - t0
    log(f"本轮结束：成功 {ok} / 空 {empty} / 失败 {fail} / 跳过 {skipped}，用时 {dt/60:.1f} 分钟")
    log(f"总进度：{sum(1 for v in state.values() if v.get('status')=='done')}/{len(docs)}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
