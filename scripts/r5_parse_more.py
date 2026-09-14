"""扩大解析批次：把更多**原生 .docx** 我方响应文件解析入库（纯本地，零外发）。

动机（2026-09-11 实测）：库里我方响应/最终版文档 **1,320 份，已解析仅 31 份（2.3%）**；
未解析的 1,289 份里有 **586 份 .docx**（原生，解析不需 OCR）。解析器 `process_native`
**完全不调用 OCR**（全文件仅一处提及 OCR 的注释），故这是纯本地工作。

用途：解析产物 → 方案章节索引 → 提升 R7 门槛「必要小节覆盖率」（实测当前 6/9 = 67%）。
D1 把索引文档从 13 扩到 20 时，4 个模块从 sparse 跃到 ok —— 文档数与覆盖率直接相关。

安全：NAS 只读；只写测试库；不 import OCR、不调用任何网关。
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
REG_DB = BASE / "bid_ai_clean_reg.db"
os.environ["BID_AI_CLEAN_DB"] = str(REG_DB)

import app.config as config  # noqa: E402
from app import db as db_mod  # noqa: E402
from app import parser as parser_mod  # noqa: E402

SOURCE_ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}
MIN_XML = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000   # 压缩前 XML 大小门槛
TARGET = int(sys.argv[2]) if len(sys.argv) > 2 else 40

db = Path(config.DB_PATH)
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", f"拒绝写非测试库：{db}"

con = db_mod.connect()
default_root = str(BASE.parent / "bid-ai-r0-snapshot")
# ⚠️ 角色集**必须**取自 app/parser.py::PARSE_SET_ROLES，不得在此写死。
# 2026-09-13 实测：白名单已扩为 7 类，但本脚本原先仍写死 ('our_response','final_signed')，
# 导致「扩容」只停在声明层、驱动层零执行 —— 需求一三类材料（社保/财务在 qualification_evidence
# 与 tender_requirement、发票在 process_material）的**正文根本读不出来**。
# 实测：按旧写死口径标书范围内有 3,731 份未解析，其中约 1,313 份**零外发本地可解析**。
PARSE_ROLES = tuple(parser_mod.PARSE_SET_ROLES)
_ROLE_PH = ",".join("?" * len(PARSE_ROLES))
cands = []
for d in con.execute(
        "SELECT document_id, source_root_id, project_folder, relative_path, document_role, "
        "       parse_status, manual_override, canonical_document_id, file_ext, sha256 "
        f"FROM documents WHERE document_role IN ({_ROLE_PH}) "
        # 2026-09-13 扩展：原只收 .docx/.docm，**漏了 .xlsx/.xlsm** ——
        # `app/parser.py::NATIVE_DISPATCH` 本来就支持 xlsx（openpyxl，零外发），
        # 但没有驱动脚本把它们当候选，于是 34 份表格（社保/财务/仪器清单常在这里）从没被读。
        "  AND (lower(relative_path) LIKE '%.docx' OR lower(relative_path) LIKE '%.docm' "
        "       OR lower(relative_path) LIKE '%.xlsx' OR lower(relative_path) LIKE '%.xlsm') "
        # ⚠️ 必须同时收 `parse_status IS NULL`：扩容角色的文档是「已登记但从未入队」，
        # 状态为 NULL 而非 'pending'。只认 'pending' 会把它们全部漏掉（实测 1,146 份 .docx）。
        # NULL 不在 parser.BLOCK_PARSE_STATUSES 里，语义就是「可解析」。
        "  AND (parse_status IS NULL OR parse_status='pending') "
        "  AND canonical_document_id IS NULL", PARSE_ROLES):
    root = SOURCE_ROOTS.get(d["source_root_id"])
    if root is None:
        continue
    path = root.joinpath(*d["relative_path"].split("/"))
    # ⚠️ 探测**体积**用的成员名必须按格式选：原实现写死 `word/document.xml`，
    # 而 `.xlsx` 里没有这个成员 → 抛异常 → **被静默 continue 掉**，
    # 于是 34 份表格（社保/财务/仪器清单常放在这里）从来不是候选。已按扩展名分派。
    # ⚠️ `documents.file_ext` 存的是**带前导点**的值（`.xlsx` 而不是 `xlsx`，hex 2E78787378）——
    # 不 strip 掉点号，下面的格式分派永远不成立，xlsx 会被当成 docx 探测而抛异常静默跳过。
    ext = (dict(d).get("file_ext") or "").lower().lstrip(".")
    member = "xl/workbook.xml" if ext in ("xlsx", "xlsm") else "word/document.xml"
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.getinfo(member).file_size
    except Exception:  # noqa: BLE001
        continue
    if xml < MIN_XML:
        continue
    cands.append({**dict(d), "_xml": xml})

cands.sort(key=lambda x: -x["_xml"])
print(f"候选（未解析 · docx/docm/xlsx/xlsm · 主体XML≥{MIN_XML:,}）: {len(cands)} 份；"
      f"本次取 {min(TARGET, len(cands))} 份")
print("-" * 78)

stats = parser_mod.ParseStats()
done = err = 0
for i, doc in enumerate(cands[:TARGET], 1):
    res = parser_mod.process_native(con, doc, SOURCE_ROOTS)
    stats.processed += 1
    stats.files_read += res.files_read
    stats.new_artifacts += int(res.new_artifact)
    stats.errors += int(bool(res.error))
    if res.error:
        err += 1
        print(f"{i:3}. [ERR] {doc['relative_path'].rsplit('/', 1)[-1][:56]}")
        print(f"        ! {res.error[:110]}")
    else:
        done += 1
        n = res.chars if res.chars is not None else -1
        print(f"{i:3}. [OK ] chars={n:>7,}  {doc['relative_path'].rsplit('/', 1)[-1][:52]}")
con.close()
print("-" * 78)
print(f"成功 {done} 份，失败 {err} 份（NAS 只读；未调用任何网关）")
