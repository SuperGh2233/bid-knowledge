"""从已解析的材料文件提取**三类定位所需的源信息**（确定性，零外发）。

三类（用户 2026-09-13 明确）：
  ① 项目业绩   —— 产品 / 合同金额 / 签订日期 / **是否有收款凭证**（走 contracts，不在本脚本）
  ② 财务社保   —— **包含什么月份**（年度/月度）
  ③ 仪器设备   —— 哪些仪器 / 是否有采购合同 / 发票 / 仪器照片

产出写入 `material_facts`（现有三列稀疏表，不新建表）。
抽取原则：**只认文件里明写的信息，提不到就不写**（宁缺毋滥，绝不猜）。

---
2026-09-13 修订（对抗复核后，两处实测缺口）

**缺口 1 · 候选被「文件名」门控**：原候选 SQL 只用 `relative_path LIKE`，
于是「社保内容写在正文里、但文件名不含社保二字」的**主响应文件完全不是候选** ——
实测 293 份正文含「社保」的 our_response 里只有 16 份（5.5%）产出了事实。
→ 候选改为 **路径命中 OR 正文命中**。

**缺口 2 · 没有任何字段承载仪器名**：原 `fact_value` 一律填「期间」，
实测 instrument 的 19 个去重值**全是 YYYY-MM 或 None，一条仪器名都没有**，
`INSTRUMENT_NAMES` 是死代码 —— 用户要的「包含哪些仪器」产出为 0。
→ 新增 `instrument_name` 事实：`fact_value` = **仪器名**，`evidence_text` = 出处句。
   同时把 `instrument_name` 类目**排在 `instrument` 之前**，避免被单标签分类器吞掉。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["BID_AI_CLEAN_DB"] = str(BASE / "bid_ai_clean_reg.db")

import app.config as config  # noqa: E402
from app import db as db_mod  # noqa: E402
import app.extract as E  # noqa: E402
from app.extract import scan_periods  # noqa: E402

db = Path(config.DB_PATH)
assert db.name.endswith("_reg.db") and db.name != "bid_ai_clean.db", f"拒绝写非测试库：{db}"

# —— ② 财务社保：期间识别 ——
# 期间抽取已抽到 `app.extract.scan_periods`（可单测、口径一处维护）——
# 2026-09-15 修 bug：原实现就地扫全文月份，把「授权书有效期到期日」当成社保月份
# （实测 `social_security_month=2026-03` 伪造值）；现在那一层过滤在 `scan_periods` 里。

# —— ③ 仪器设备：仪器名句式 ——
# 实测形态：`共有11台10X单细胞Genomics Chromium仪器，设备序列号为：`、
#           `拥有3台96通道自动化建库设备，1台96通道自动化纯化仪`
NUM_UNIT = re.compile(r"(?:共有|拥有|配备|现有|计)?\s*(\d+)\s*台\s*([^，,。；;、\n|]{2,40})")
# 名字不能以这些字开头（多为上一个匹配的越界残渣，如「合**同签订**补充协议后」）
NAME_STOP_PREFIX = ("的", "和", "同", "与", "及", "则", "后", "上")
NAME_NOISE = re.compile(r"^\s*(?:仪器|设备|如下|序列号|台|套)$")
# 名字里**不该出现**的词：出现即说明抓越界了（把下一个表头/字段名/页眉也吞进来了）
NAME_BAD_WORDS = ("序号", "清单", "合同", "备注", "合计", "名称", "型号", "目录", "页码",
                  "一览表", "配置", "承诺", "声明", "要求", "正文", "附件", "以上", "如下",
                  "平台", "本公司", "我方")
# 纯数字堆（如 `203204205合同第9 台209210` 里的编号串）→ 不是仪器名
NAME_MANY_DIGITS = re.compile(r"\d{6,}")

# —— ③ 仪器设备：真「仪器采购合同」句式 ——
# 复核发现原 `purchase_contract` 候选源（文件名含「采购合同」）产出的 10 条**全是假阳性**
# （多为「我方销售合同」被买方写成「采购合同」、或政府采购承诺样板句）。
# 真信号形态是「<仪器/品牌>采购合同」且同句带型号，如
# `的waters I class液相采购合同和9台的Thermo QE-HF质谱采购合同`。
PURCHASE_SENT = re.compile(r"([A-Za-z一-鿿][^，,。；;\n|]{1,30}?)采购合同")

INSTRUMENT_NAMES = [
    "10X单细胞", "10x Genomics", "Chromium", "测序仪", "荧光细胞分析仪", "生物分析仪",
    "液相色谱", "质谱", "PCR仪", "离心机", "超低温冰箱", "生物安全柜", "流式细胞仪",
    "Agilent", "安捷伦", "Bruker", "布鲁克", "Illumina", "墨卓",
]
KIND_RULES = [
    # ⚠️ 顺序即优先级：「仪器照片」必须先于「仪器」、「仪器名」必须先于「仪器」，
    # 否则永远产不出 instrument_photo / instrument_name（kind_of 是单标签分类器）。
    (("完税证明", "社保", "社会保险", "养老", "医疗", "失业", "工伤"), "social_security_month"),
    (("财务社保数据统计表", "审计报告", "财务报告", "资产负债表", "利润表",
      "资信证明", "纳税"), "finance_period"),
    (("仪器照片", "设备照片", "实拍"), "instrument_photo"),
    (("购置发票", "发票"), "invoice"),
    (("采购合同", "购销合同"), "purchase_contract"),
    (("仪器", "设备", "测序仪"), "instrument"),
]


def kind_of(name: str, text: str) -> str | None:
    """按**文件名优先**判定材料类别（文件名是人工命名，比正文噪声低）。

    2026-09-13 修订：正文回退从 `text[:400]` 放宽到**全文**。
    原实现只看正文前 400 字，而材料清单常出现在响应文件靠后的小节里。
    """
    for kws, kind in KIND_RULES:
        if any(k in name for k in kws):
            return kind
    for kws, kind in KIND_RULES:
        if any(k in text for k in kws):
            return kind
    return None


def _unwrap(text: str) -> str:
    """把**软换行**接回一行，用于句式匹配。

    实测（OCR 与 PDF 抽取都有）：正文会把一个名字从中间折断 ——
    `1 台192 通道\\nHBH192）` → 不接行就只抓到 `192 通道`，再断成 `192 通`；
    于是同一个仪器在库里出现 3 个「名字」，全是残渣。
    规则：换行前**不是**句读符号（。；！？：）的一律视为同行内断行，接起来。
    只用于匹配；evidence 也取自接行后的文本（接行后读起来更完整）。
    """
    return re.sub(r"(?<![。；！？：\n])\n(?=\S)", "", text)


def instrument_names_in(text: str) -> list[tuple[str, str]]:
    """抽「有哪些仪器」：返回 (仪器名, 出处句) 列表。

    只认 `…N台<名字>` 句式。名字需含字母或汉字、长度≥2、不以残渣字开头。
    """
    text = _unwrap(text)
    found: list[tuple[str, str]] = []
    for m in NUM_UNIT.finditer(text):
        name = m.group(2).strip(" ：:（(）)")
        if len(name) < 2 or len(name) > 30 or NAME_NOISE.match(name):
            continue
        if name.startswith(NAME_STOP_PREFIX):
            continue
        if any(w in name for w in NAME_BAD_WORDS):
            continue
        if NAME_MANY_DIGITS.search(name):
            continue
        if not re.search(r"[A-Za-z一-鿿]", name):
            continue
        # 出处句：向左扩到句读，向右到句读，供人工核对（不猜）
        s = max(0, text.rfind("\n", 0, m.start()) + 1)
        e = min(len(text), m.end() + 60)
        found.append((name, text[s:e].strip()[:220]))
    # 去重保序
    seen, out = set(), []
    for name, ev in found:
        if name in seen:
            continue
        seen.add(name)
        out.append((name, ev))
    # ⚠️ 剔除「前缀残渣」：同一份文件里 `192 通道HBH192` 与 `192 通道`、`192 通` 会各成一条
    # （正文里换行/空格把同一个名字切断）。规则：若某名字是**另一个更长名字**的严格前缀，丢短的。
    # 比较时先去掉空白，避免 `192 通道` vs `192通道HBH192` 因空格差异逃过判断。
    def squash(s: str) -> str:
        return re.sub(r"\s+", "", s)
    kept = [(n, e) for n, e in out
            if not any(squash(n) != squash(m) and squash(m).startswith(squash(n)) for m, _ in out)]
    return kept


def purchase_contracts_in(text: str) -> list[tuple[str, str]]:
    """抽真「仪器采购合同」：`<型号/品牌>采购合同`，且同句不带「我方/投标」等销售语境。"""
    text = _unwrap(text)
    out, seen = [], set()
    for m in PURCHASE_SENT.finditer(text):
        head = m.group(1).strip()
        # 去掉越界的前缀：取最后一个分隔符之后的部分（`附LIMS…` → `LIMS…`；
        # `术方案（七）设备配置拥有9台的waters I class液相` → `waters I class液相`）
        head = re.split(r"[（(《【:：、）)》】]", head)[-1].strip()
        # ⚠️ 要**交替跑到稳定**：`设备配置拥有9 台的waters…` 里连接词在前、`N台` 在后，
        # 单跑一轮只能剥掉一层，会剩下 `9 台的waters…`。
        for _ in range(3):
            head = re.sub(r"^\s*\d+\s*台\s*", "", head)
            head = re.sub(r"^(?:拥有|共有|现有|配备|附|见|和|及|的|方案|设备配置)*\s*", "", head).strip()
        if len(head) < 3 or len(head) > 34:
            continue
        # head 必须像个设备/品牌名：含字母（型号/品牌）或含仪器词；否则多半是
        # 「不签订采购合同」「以解除采购合同」这类**否定句/法律句**，不是设备采购
        if not (re.search(r"[A-Za-z]", head) or any(w in head for w in INSTRUMENT_NAMES)
                or any(w in head for w in ("设备", "仪器", "质谱", "色谱", "测序", "分析仪"))):
            continue
        if head.startswith(("不", "未", "无", "以", "第", "页", "见")) or "页" in head or "序号" in head:
            continue
        s = max(0, text.rfind("\n", 0, m.start()) + 1)
        e = min(len(text), m.end() + 60)
        sent = text[s:e].strip()[:220]
        key = head[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append((f"{head}采购合同", sent))
    return out


con = db_mod.connect()
# —— 候选：**路径命中 OR 正文命中**（2026-09-13 修订，见文件头「缺口 1」）——
PATH_KWS = ["完税", "社保", "纳税", "财务", "审计", "资信", "仪器", "设备", "发票",
            "采购", "照片", "实拍", "凭证", "应收账款", "付款", "收款"]
TEXT_KWS = ["社会保障", "社会保险", "社保", "完税", "纳税", "财务报告", "审计报告",
            "资产负债表", "利润表", "资信证明", "仪器", "设备清单", "发票",
            "采购合同", "仪器照片", "设备照片", "实拍", "付款凭证", "收款凭证"]
path_clause = " OR ".join(["d.relative_path LIKE ?"] * len(PATH_KWS))
text_clause = " OR ".join(["a.text LIKE ?"] * len(TEXT_KWS))
rows = [dict(r) for r in con.execute(
    f"""SELECT d.document_id, d.relative_path, d.document_role, a.text
 FROM documents d JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id
 WHERE length(a.text) > 0 AND (({path_clause}) OR ({text_clause}))""",
    [f"%{k}%" for k in PATH_KWS] + [f"%{k}%" for k in TEXT_KWS])]
print(f"候选材料文档: {len(rows)} 份（路径命中 OR 正文命中）")

stats: dict[str, int] = {}
written = 0
instr_rows = 0
# ⚠️ 幂等清理**只能删本脚本自己产出的类别**。
# 原实现是 `DELETE ... WHERE document_id=?`（无类别条件），会把**不由本脚本产生**的
# `qualification`（87 条，来自 r4_sync_classified_facts.py / LLM 分类补写）一并抹掉 ——
# 实测已发生一次（485 → 2136 条的同时 qualification 整类消失）。只删自己的，别动别人的。
OWN_TYPES = ("social_security_month", "finance_period", "instrument", "instrument_name",
             "purchase_contract", "instrument_purchase_contract", "invoice", "instrument_photo")
_OWN_PH = ",".join("?" * len(OWN_TYPES))
with con:
    # 幂等：先清掉本次范围内的旧记录（**仅本脚本产出的类别**）
    for r in rows:
        con.execute(f"DELETE FROM material_facts WHERE document_id=? AND fact_type IN ({_OWN_PH})",
                    (r["document_id"], *OWN_TYPES))
    for r in rows:
        name = r["relative_path"].rsplit("/", 1)[-1]
        text = r["text"] or ""
        kind = kind_of(name, text)
        role = r["document_role"]

        # —— 仪器名：无论文档级类别判成什么，只要有 `N台<名字>` 句式就抽 ——
        if kind in ("instrument", "instrument_photo"):
            for iname, iev in instrument_names_in(text):
                con.execute(
                    "INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
                    "VALUES (?,?,?,?)",
                    (r["document_id"], "instrument_name", iname, f"[{role}] {iev}"))
                written += 1
                instr_rows += 1
                stats["instrument_name"] = stats.get("instrument_name", 0) + 1
            for pname, pev in purchase_contracts_in(text):
                con.execute(
                    "INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
                    "VALUES (?,?,?,?)",
                    (r["document_id"], "instrument_purchase_contract", pname, f"[{role}] {pev}"))
                written += 1
                stats["instrument_purchase_contract"] = \
                    stats.get("instrument_purchase_contract", 0) + 1

        if not kind:
            continue
        # 期间抽取：**只在材料标记附近取**（响应件里夹着营业执照/合同/证书/招标要求/身份证，
        # 全文扫描会把「营业执照登记日期」「签署日期」甚至**招标文件自己的投标截止时间**
        # 当成社保月份 —— 2026-09-15/16 需求方两轮实测）。
        # 两级标记：强标记（材料段落标题）优先，取不到再用宽标记。
        # ⚠️ **不再回退到全文扫描**：实测那一路正是伪造值来源（851 份含社保事实的文档里
        #    501 份靠它给值，其中就有把「2026 年 6 月」（招标截止）当社保月份的）。
        #    找不到材料记录段 → **如实「期间未知」，不猜**（与"提不到就不写"同一原则）。
        # ⚠️ **分档**（2026-09-16 需求方第三轮反馈「不能只改个例」）：
        #   强标记（`社会保险费缴费记录`/`社会保障记录`）→ **窗口**：那是**记录表**，
        #     OCR 把表格列打散，期间与关键词常不同行；
        #   宽标记（`社会保险`/`社保` 泛词）→ **同一行**：那多是**声明句**
        #     （`现附上自2025年2月1日至…我方缴纳的社会保险凭据`）。统一用 ±150 窗口会把
        #     同段的「签署时间/日期」也收进来 —— 实测窗口内约 650 条是签署日期。
        if kind == "social_security_month":
            per = (E.scan_periods_near(text, E.SS_MARKERS_STRONG, month_only=True)
                   or E.scan_periods_near(text, E.SS_MARKERS, same_line=True))
        elif kind == "finance_period":
            per = (E.scan_periods_near(text, E.FIN_MARKERS_STRONG)
                   or E.scan_periods_near(text, E.FIN_MARKERS, same_line=True))
        else:
            per = []
        # 独立信号校验：**材料期间不得晚于项目日期**（目录名自带 `YYYYMMDD`）——
        # 证书/身份证**有效期**（`2027-12`/`2028-04`）紧邻社保段落时会被收进来（实测 2026-09-16）。
        per = E.filter_periods_by_project_date(per, r["relative_path"].split("/")[0])
        if not per:
            # 无期间：仍记一条（事实是"有这类材料"，期间未知）
            per = [None]
        for p in per:
            con.execute(
                "INSERT INTO material_facts (document_id, fact_type, fact_value, evidence_text) "
                "VALUES (?,?,?,?)",
                (r["document_id"], kind, p, f"[{role}] {name[:120]}"))
            written += 1
            stats[kind] = stats.get(kind, 0) + 1
con.close()

print(f"\n写入 material_facts: {written} 条（其中仪器名 {instr_rows} 条）")
print("按类别：")
for k, v in sorted(stats.items(), key=lambda x: -x[1]):
    print(f"  {k:<30} {v}")
print()
print("=== 索引后的总覆盖（全部来源）===")
con = db_mod.connect()
for r in con.execute("SELECT fact_type, COUNT(*) n, COUNT(DISTINCT document_id) docs "
                     "FROM material_facts GROUP BY 1 ORDER BY n DESC"):
    print(f"  {r['fact_type']:<30} {r['n']:>5} 条 / {r['docs']:>3} 份文档")
con.close()
