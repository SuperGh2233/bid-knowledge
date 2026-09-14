"""全量对账：解析出的**明细金额合计** vs 文件名的**成交价**。

为什么这是有效的缺陷探测器：人工命名约定是「文件名金额＝成交价」（见项目 CLAUDE.md），
且实测 77/79 一致。**对不上的每一份**都是候选解析缺口 —— 要么明细行没抽全、要么金额读错。
比逐个金标准漏检去猜要系统得多。

**常驻健康检查**（可反复重跑）：抽取管线一有改动就跑一遍，
对不上的条数**变多**即说明引入了新的解析缺口。

不修改任何数据；只读复算 + 打印。
"""
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean\bid_ai_clean_reg.db"
sys.path.insert(0, r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
from app.extract import parse_contract_service_table  # noqa: E402

FILE_AMT = re.compile(r"([\d,，]{3,}(?:\.\d{1,2})?)\s*(?:\(\d\))?\.(?:pdf|jpg|jpeg|png)$", re.I)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute(
    "SELECT c.contract_id, c.contract_number, c.total_amount, d.relative_path,"
    "       d.canonical_document_id "
    "FROM contracts c JOIN documents d ON d.document_id=c.document_id "
    "WHERE c.contract_id LIKE 'CTL-%' ORDER BY d.relative_path")]

match = mismatch = uncomparable = nodetail = 0
cases = []
for r in rows:
    fn = r["relative_path"].rsplit("/", 1)[-1]
    m = FILE_AMT.search(fn)
    if not m:
        uncomparable += 1
        continue
    try:
        want = round(float(m.group(1).replace(",", "").replace("，", "")), 2)
    except ValueError:
        uncomparable += 1
        continue
    art = con.execute("SELECT text FROM parse_artifacts WHERE canonical_document_id=?",
                      (r["canonical_document_id"],)).fetchone()
    recs = parse_contract_service_table(art["text"] or "") if art else []
    details = [x for x in recs if x["row_type"] == "detail"]
    if not details:
        nodetail += 1
        continue
    has_unknown = any(x.get("line_amount") is None for x in details)
    got = round(sum(x["line_amount"] or 0 for x in details), 2)
    if has_unknown:
        uncomparable += 1
        cases.append(("含未知金额", fn, want, got, len(details)))
    elif abs(got - want) < 1:
        match += 1
    else:
        mismatch += 1
        cases.append(("❌对不上", fn, want, got, len(details)))

print(f"CTL 合同 {len(rows)} 份")
print(f"  明细合计 == 文件名成交价 : {match}")
print(f"  **对不上**               : {mismatch}")
print(f"  含未知金额（无法比对）    : {uncomparable}")
print(f"  无服务明细               : {nodetail}")
print()
print("=== 对不上的（候选解析缺口）===")
for tag, fn, want, got, n in cases:
    if tag.startswith("❌"):
        d = got - want
        print(f"  {fn[:66]}")
        print(f"      文件名={want:>14,.2f}  明细合计={got:>14,.2f}  差={d:>+14,.2f}  行数={n}")
print()
# —— 附加检查：**明细合计 > 文件名成交价** ——
# 明细行金额之和**不可能**超过合同总额；一旦超过，说明该表**列错位**
# （如多行单元格表：一条逻辑行的「服务要求」占多行，数字落在续行末尾，而续行单元格数与表头不符），
# 抽出来的数会污染产品金额、并按错误金额参与命中。
print()
print("=== 明细合计 > 文件名成交价（逻辑上不可能）===")
imp = [(fn, want, got, n) for tag, fn, want, got, n in cases if got > want + 1]
if imp:
    for fn, want, got, n in imp:
        print(f"  ⚠ 成交价={want:>14,.2f}  明细合计={got:>14,.2f}  {fn[:58]}")
    print("  注：若为「文件名＝成交价 < 正文标价」的已记载约定，差额属正常，非错位 —— 需人工看正文。")
else:
    print("  无 ✓ —— 解析结果中不存在「明细合计超合同总额」的错位污染")

print(f"=== 含未知金额的（前 10，行数多/差额大者优先看）===")
unk = [c for c in cases if not c[0].startswith("❌")]
for tag, fn, want, got, n in unk[:10]:
    print(f"  {fn[:66]}")
    print(f"      文件名={want:>14,.2f}  已知明细={got:>14,.2f}  行数={n}")
