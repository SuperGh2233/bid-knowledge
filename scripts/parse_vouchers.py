"""解析凭证类文件（有文字层的 PDF + Office 文件）——**零外发**。

动机：收款凭证/完税证明/发票整块没解析过（175 份），其中 105 份 PDF 有文字层、
65 份是 Office 文件 —— 都不需要 OCR，属纯本地工作。
这批正是用户要的「项目业绩→是否有收款凭证」与「财务社保→包含什么月份」的来源。
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["BID_AI_CLEAN_DB"] = str(BASE / "bid_ai_clean_reg.db")

import app.config as config  # noqa: E402
from app import db as db_mod  # noqa: E402
from app import parser as parser_mod  # noqa: E402

ROOTS = {
    "2025年": Path("//192.168.10.188/大客户部/01 投标项目文件/2025年"),
    "2026年": Path("//192.168.10.188/大客户部/01 投标项目文件/2026年"),
}

db = Path(config.DB_PATH)
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", f"拒绝写非测试库：{db}"

con = db_mod.connect()
rows = [dict(r) for r in con.execute(
    """
 SELECT d.document_id, d.source_root_id, d.project_folder, d.relative_path, d.document_role,
        d.parse_status, d.manual_override, d.canonical_document_id, d.file_ext, d.sha256
 FROM documents d LEFT JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id
 WHERE a.canonical_document_id IS NULL
   AND lower(d.file_ext) IN ('.pdf', '.docx', '.docm', '.xlsx', '.doc')
   AND (d.relative_path LIKE '%回单%' OR d.relative_path LIKE '%收款%'
        OR d.relative_path LIKE '%付款%' OR d.relative_path LIKE '%凭证%'
        OR d.relative_path LIKE '%发票%' OR d.relative_path LIKE '%银行%'
        OR d.relative_path LIKE '%完税%' OR d.relative_path LIKE '%社保%'
        OR d.relative_path LIKE '%纳税%' OR d.relative_path LIKE '%资信%')
 ORDER BY file_size DESC""")]
print(f"候选（凭证类 · 可本地格式 · 未解析）: {len(rows)} 份")
print("-" * 78)

ok = err = skip = 0
for i, doc in enumerate(rows, 1):
    res = parser_mod.process_native(con, doc, ROOTS)
    name = doc["relative_path"].rsplit("/", 1)[-1]
    if res.error:
        # 无文字层的 PDF 会落到 unsupported —— 如实计数，不当成功
        if "unsupported" in str(res.error).lower() or "no text" in str(res.error).lower():
            skip += 1
        else:
            err += 1
            print(f"{i:3}. [ERR] {name[:56]}  {str(res.error)[:60]}")
    elif res.chars:
        ok += 1
        print(f"{i:3}. [OK ] chars={res.chars:>6,}  {name[:56]}")
    else:
        skip += 1
con.close()
print("-" * 78)
print(f"成功 {ok} 份 / 跳过（无文字层等）{skip} 份 / 失败 {err} 份")
print("（NAS 只读；未调用任何网关）")
