"""从 `银行回单.zip` 的**中央目录**抽取「合同号 → 银行回单」映射（零解压、零 OCR、零外发）。

背景（2026-09-13）：用户要的「是否有收款凭证」原先只有一张**他方主体（鹿明）**的
`合同对应付款凭证.xlsx` 台账可用，98 份 CTL 合同里只覆盖 2 份。
实测另有一条**一直存在但从没被读**的证据源：项目文件夹 `合同/银行回单.zip`，
内部按**合同号**建目录存放银行回单图片：

    银行回单/BOE2023081556.png
    银行回单/YOE2024101421/<uuid>.png ×2
    银行回单/ZOE2025073201/<uuid>.png ×4
    …

**关键**：这层信息在 zip 的**中央目录**里，读取它**不需要解压、不需要 OCR、不产生任何写入**
（`Z:\\` 只读红线不受影响）—— 故这是纯本地可得的强证据。

⚠️ 诚实边界：本脚本**只读文件名**，**不核内容**。故产出的状态是
`receipt_file`（"存在以该合同号命名的银行回单图片"），**不等于**"款项已到账"。
要断言到账仍需人工看图或 OCR（属外发，未授权）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["BID_AI_CLEAN_DB"] = str(BASE / "bid_ai_clean_reg.db")

from app import db as db_mod  # noqa: E402

OUT = BASE / "data" / "payment_receipts.json"
ROOTS = {
    "2025年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2025年"),
    "2026年": Path(r"\\192.168.10.188\大客户部\01 投标项目文件\2026年"),
}
NUM_RE = re.compile(r"([A-Z]{2,4}\d{8,})")


def fix_name(name: str) -> str:
    """zip 未置 UTF-8 标志时，中文名按 CP437 解出乱码；原始字节其实是 GBK。"""
    try:
        return name.encode("cp437").decode("gbk")
    except Exception:  # noqa: BLE001
        return name


def norm(s: str) -> str:
    """合同号归一：去非字母数字，统一 O/I/L 与 0/1 的 OCR 混淆。"""
    s = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    return s.replace("O", "0").replace("I", "1").replace("L", "1")


con = db_mod.connect()
zips = [dict(r) for r in con.execute(
    """SELECT document_id, source_root_id, relative_path FROM documents
       WHERE relative_path LIKE '%银行回单%' AND lower(relative_path) LIKE '%.zip'""")]
print(f"库内登记的「银行回单.zip」: {len(zips)} 个")

records: list[dict] = []
for z in zips:
    root = ROOTS.get(z["source_root_id"])
    if root is None:
        continue
    path = root.joinpath(*z["relative_path"].split("/"))
    if not path.exists():
        print(f"  [跳过·路径不存在] {z['relative_path'][:70]}")
        continue
    try:
        with zipfile.ZipFile(path) as zf:            # 只读中央目录，不解压
            for info in zf.infolist():
                if info.is_dir():
                    continue
                inner = fix_name(info.filename)
                m = NUM_RE.search(inner)
                if not m:
                    continue
                records.append({
                    "contract_number": m.group(1),
                    "norm": norm(m.group(1)),
                    "inner_path": inner,
                    "size": info.file_size,
                    "source_zip": z["relative_path"],
                    "source_document_id": z["document_id"],
                })
    except Exception as e:  # noqa: BLE001
        print(f"  [读取失败] {z['relative_path'][:60]}: {str(e)[:80]}")

print(f"抽到银行回单条目: {len(records)} 条")
by_num: dict[str, list[dict]] = {}
for r in records:
    by_num.setdefault(r["norm"], []).append(r)
print(f"覆盖合同号: {len(by_num)} 个")

# —— 与 contracts 匹配 ——
ct = {norm(r[0]): r[0] for r in con.execute(
    "SELECT contract_number FROM contracts WHERE contract_number IS NOT NULL AND contract_number != ''")}
matched = 0
for _n, recs in by_num.items():
    hit = ct.get(_n)
    for r in recs:
        r["matched_contract_number"] = hit
    if hit:
        matched += 1
con.close()

OUT.write_text(json.dumps({
    "note": "从「银行回单.zip」**中央目录**读取的 [合同号→银行回单图片] 映射。"
            "**只读文件名，未核内容**：receipt_file 表示「存在以该合同号命名的回单图片」，"
            "**不等于款项已到账**（断言到账需人工看图或 OCR，属外发，未授权）。"
            "读取过程不解压、不写 NAS、不外发。",
    "count": len(records),
    "distinct_contracts": len(by_num),
    "matched_in_contracts": matched,
    "records": records,
}, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"匹配上 contracts 的合同号: {matched} 个")
print()
print("=== 覆盖的合同号 ===")
for _n, recs in sorted(by_num.items()):
    hit = recs[0].get("matched_contract_number")
    print(f"  {recs[0]['contract_number']:<16} {len(recs):>2} 张回单  "
          f"{'✓ 在 contracts 里' if hit else '✗ 不在 contracts 里'}")
print(f"\n报告 → {OUT}")
