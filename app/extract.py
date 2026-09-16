"""R4 确定性提取：业绩清单 → 历史合同声明记录。

设计（用户拍板口径）：
- 只识别「业绩清单」这类历史合同声明（表：序号/采购人/项目名称/合同金额(万元)/年份等）。
- 完整保留标题、列头、整行原文与所在位置（正文行号）。
- 金额：列表头为"合同金额（万元）"时 ×10000 转元写入 total_amount；单位不明保持未知。
- 缺失字段一律未知：合同编号、乙方、合同供应商归属 未出现 → None，禁止从响应文件所属公司反推。
- 6 行独立保留；无合同编号不能作为合并依据；项目名称含"LC-MS/MS/代谢"不能单独证明代谢组服务金额；
  清单合同总额不得写成产品明细金额。
- 报价文件/预算/限价/报价表 不生成历史合同正例（无"合同金额/业绩"语境）。

数据更新边界（增量更新修正，用户拍板）：
- 只有【成功取得本清单完整结构（表头锚点存在）】才能谈"空清单"。
- 无原生正文 / 空串 / 纯空白 / 读取不完整 → status=no_native_text，**不得**触发旧记录删除，
  也不能计为"成功提取0条"。
- 未匹配到表头 → status=no_ledger_confirmed，**不得**清空（extract 返回 [] 不单独构成清空依据）。
- 表头命中且数据行为空 → 明确确认清单为空，才清空该文档旧记录（先删子记录再删父行）。
- 同 ordinal 内容变化（采购人/金额不同）→ 旧子记录失效先删，防止子记录挂在已变成另一份合同的父下。
- 同输入重放（采购人/金额一致）→ 保留仍有效的子记录。

返回/写入：
- extract_contract_ledger(text)  -> list[dict]（兼容旧调用，空=无记录）
- extract_contract_ledger_state(text) -> {"header_found": bool, "records": [...]}
- sync_contract_ledger(con, doc_id, header_found, records) -> stats dict（含 status）
- extract_and_sync(con, doc_id, native_text) -> stats dict（入口）：无正文/空→no_native_text；
  表头未命中→no_ledger_confirmed；清空/同步/更新。写库异常→回滚并抛。
"""
from __future__ import annotations

import re

import app.config as config

_ORD = re.compile(r"^\d{1,3}(?:\s*\||\s)")
# 章节标题：`十三、《…》` / `十四、类似项目业绩一览表` / `一、…`（业绩数据行不会这样开头）
_SECTION_HEAD = re.compile(r"^[一二三四五六七八九十百]{1,3}\s*[、.．]")
# 纯日期/年份段（`2023年-2025年` / `2025年2月` / `2023.9`）—— 不能当项目名
# 纯日期/年份段：`2023年-2025年` / `2025年2月` / **`2025年10月17日`**（含"日"——第一版漏了，
# 实测该格被当成项目名）/ `2023.9`
_DATEISH = re.compile(r"^(?:20\d{2}\s*年?)(?:\s*[-~—至到]\s*20\d{2}\s*年?)?"
                      r"(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*日)?\s*$|^20\d{2}\.\d{1,2}$")
# 联系方式格（`潘杰028-85502628` / `韩煦，18616122427` / 邮箱）—— 不能当项目名
_CONTACTISH = re.compile(r"\d{7,}|@|\d{3,4}[-－]\d{7,}")
_PURE_NUM = re.compile(r"^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$")
_YEAR_FULL = re.compile(r"^(?:20\d{2})(?:\s*年)?$")


def _year_of(cell: str) -> str | None:
    """年份只能整格识别（2023 / 2023年），禁止在电话号码等串内取到 20xx 子串。"""
    cell = cell.strip()
    m = _YEAR_FULL.match(cell)
    if m:
        return m.group(0)[:4]
    return None


def _to_yuan(amount_raw, unit: str | None) -> float | None:
    if not amount_raw:
        return None
    try:
        val = float(str(amount_raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    # 「万」与「万元」等价（2026-09-16：实测表格里金额格常写 `3.5万`，没有「元」字，
    # 旧实现只认「万元」→ 19 条明明写了金额却落成未知）。
    # 合理上限：本语料是检测服务投标，业绩金额量级在 1 万 ~ 300 万；实测最大值 253.7 万。
    # 超过 1 亿的一律判为**误读**（实锤：一条 4.46119200 亿 = 电话号码被当成金额）。
    # 宁缺毋滥：宁可漏一个真实的亿级合同，也不把电话号当金额上屏。
    _MAX_YUAN = 100_000_000.0
    if unit in ("万元", "万"):
        val = val * 10000
        return round(val, 2) if val <= _MAX_YUAN * 10 else None
    if unit == "元":
        return round(val, 2) if val <= _MAX_YUAN else None
    return None


_AMOUNT_COL = re.compile(r"金额|总价|价格|合同额")
_PROJECT_COL = re.compile(r"项目名称|项目内容|服务名称|业绩名称|工作主要内容|合同主要内容|采购内容")
# 业绩内容列（用于判定"这是业绩表"）：与 _PROJECT_COL 同族，另收「服务内容/标的名称」等实测写法
_LEDGER_CONTENT_COL = re.compile(r"项目|服务内容|服务名称|业绩|标的|采购内容|工作内容|主要内容")
_DATE_COL = re.compile(r"签订|签约|年份|竣工验收|时间")


# 机构名标记：业绩行的采购人是**单位**（医院/大学/公司…）。用于剔除"表头认出来了、
# 但表体其实是别的表"的误抽行（见 `_looks_like_ledger_row`）。
_ORG_MARKERS = ("公司", "大学", "医院", "研究院", "研究所", "学院", "中心", "学校",
                "科学院", "实验室", "集团", "疾控", "检测", "检验", "设计院", "事务所",
                "委员会", "管理局", "部队", "保健院", "防治", "总院", "分院", "附属")


def _looks_like_ledger_row(r: dict) -> bool:
    """一行是否**像**业绩行：有金额，或采购人像机构名。两者皆无 → 不是业绩行。"""
    if r.get("total_amount") is not None:
        return True
    party = r.get("party_a_raw") or ""
    return any(m in party for m in _ORG_MARKERS)


def _column_index(header_line: str) -> dict:
    """从**表头行**推出各字段所在列（按列名，不按"第一个中文格"）。

    由来（2026-09-16）：业绩表的列序差异极大 —— 有的写 `序号|采购人|项目名称|…`（当事人列在前），
    有的写 `序号|项目名称|项目内容|…|单位名称`（当事人列在后）。旧实现取"第一个中文格当采购人"，
    后者会把**项目名当采购人**。按列名定位两类都能正确。
    """
    cells = [c.strip() for c in header_line.split("|")]
    idx = {}
    for i, c in enumerate(cells):
        if not c:
            continue
        if "party" not in idx and any(k in c for k in _PARTY_KEYS):
            idx["party"] = i
        if "amount" not in idx and _AMOUNT_COL.search(c) and "单价" not in c:
            idx["amount"] = i
        if "project" not in idx and _PROJECT_COL.search(c):
            idx["project"] = i
        if "date" not in idx and _DATE_COL.search(c):
            idx["date"] = i
    return idx if "party" in idx or "amount" in idx else {}


def _parse_row(row_text: str, unit: str | None, header_line: str | None = None) -> dict:
    """解析一行业绩行 → 采购人/项目名称/金额/年份（缺失=None，不臆测）。

    `header_line` 给了就**按列名定位**；给不出可用的列映射时退回原位置启发式（行为不变）。
    """
    cells = [c.strip() for c in row_text.split("|") if c and c.strip()]
    ord_ = None
    rest = cells
    if cells and cells[0].isdigit() and len(cells[0]) <= 3:
        ord_ = int(cells[0])
        rest = cells[1:]
    party = None
    amount_raw = None
    unit_eff = unit
    project_by_col = None
    colmap = _column_index(header_line) if header_line else {}
    raw_cells = [c.strip() for c in row_text.split("|")]

    def _at(kind):
        i = colmap.get(kind)
        if i is None or i >= len(raw_cells):
            return None
        v = raw_cells[i].strip()
        return v or None

    if colmap:
        p = _at("party")
        # 列映射给的采购人只接受"像机构名"的值（避免把表头残留/数字当采购人）
        if p and re.fullmatch(r"[一-龥·（）()A-Za-z0-9]{2,40}", p) and not _PURE_NUM.match(p):
            party = p
        a = _at("amount")
        if a:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(万元|万|元)?", a)
            if m and not re.match(r"^(?:19|20)\d{2}$", m.group(1)):
                amount_raw = m.group(1)
                if m.group(2) in ("万元", "万"):
                    unit_eff = "万元"
                elif m.group(2) == "元":
                    unit_eff = "元"
            else:
                # **合并列**（2026-09-16 实测 6 例）：表头写 `项目名称及合同金额（万元）`，
                # 一格内容形如 `LC-MS/MS 全谱代谢组检测、35.5` / `LC-MS非靶向代谢31` ——
                # 金额就在格子里，只是与项目名合并了。取**结尾的数**，单位按表头列名的「万元」。
                # 安全边界：仅当该列名本身含 金额/总价/价格 时才走这条路（`_column_index` 已保证），
                # 且数值须在本语料合理量级内（后续 `_to_yuan` 的上限再兜一层）。
                tail = re.search(r"(\d+(?:\.\d+)?)\s*万?元?\s*$", a)
                if tail and "万" in (header_line or ""):
                    amount_raw = tail.group(1)
                    unit_eff = "万元"
        project_by_col = _at("project")
    for c in rest:
        if party:
            break
        if re.fullmatch(r"[一-龥·（）()]{2,40}", c):
            party = c
            break
    if amount_raw is None:
        for c in rest[1:] if party else rest:
            if not c:
                continue
            if _PURE_NUM.match(c) and not re.match(r"^(?:19|20)\d{2}$", c):
                amount_raw = c
                break
            # 金额格自带单位：`3.5万` / `3.5万元`（旧实现只认纯数字格 → 落成未知）
            m_inline = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(万元|万)", c)
            if m_inline:
                amount_raw = m_inline.group(1)
                unit_eff = "万元"
                break
    year = None
    for c in rest:
        y = _year_of(c)
        if y:
            year = y
            break
    project = project_by_col
    if not project:
        # **结构判据**（2026-09-16，替代"取第一个中文格"）：项目列 = 排除掉
        #   ① 当事人格 ② 金额格 ③ 纯日期/年份段 ④ 联系方式格（姓名+电话/邮箱）之后，
        #   **最长的中文格**。
        # 为什么不用词表：实测列名写法无穷（项目名称 / 服务内容 / 主要采购内容 / 标的名称 /
        # 工作主要内容 / 合同主要内容 …），逐个补必然漏 —— 与「意图识别」那次的教训同源：
        # 多份手写词表互不知情。位置无关、词表无关，而"最长的中文格就是项目描述"在真实业绩表里稳定。
        # 注释/说明格（`（2023年1月至本采购活动比选公告日期，以合同或协议签字日期为准）`）也排除——
        # 实测它被当成项目名上过屏。判据：整格被括号包住，或含"为准/公告/签字日期/备注"等说明语。
        note = re.compile(r"^[（(].*[）)]$|为准|公告日期|签字日期|详见|略$|^备注")
        # 另：项目名**必含中文**（实测英文人名 `Lingge Tu` 被当成项目名上过屏）
        cands = [c for c in rest
                 if c != party and c != amount_raw and len(c) >= 4
                 and re.search(r"[一-龥]", c)
                 and not _year_of(c) and not _DATEISH.match(c)
                 and not _CONTACTISH.search(c) and not note.search(c)]
        if cands:
            project = max(cands, key=len)
    if amount_raw is None:
        # 金额被写在项目名里（实测 `10x Genomics 单细胞空转￥39.9万元`、
        # `代谢学检测技术服务合同（30万元）`、`LC-MS/MS脂质组检测 9万元`）——
        # 表格里没有独立金额列，但金额是**明确写着**的，属"能取就该取"。
        joined = " ".join(rest)
        m_emb = re.search(r"(\d+(?:\.\d+)?)\s*万元", joined)
        if m_emb:
            amount_raw = m_emb.group(1)
            unit_eff = "万元"
        else:
            # 合并列形态（`…检测、35.5`）：表头含「万元」且格尾是数字 → 取格尾数
            m_tail = re.search(r"、\s*(\d+(?:\.\d+)?)\s*$", joined)
            if m_tail and "万元" in (header_line or ""):
                amount_raw = m_tail.group(1)
                unit_eff = "万元"
    total = _to_yuan(amount_raw, unit_eff)
    return {"row_ord": ord_, "party_a_raw": party, "project_raw": project,
            "total_amount": total,
            "unit": unit_eff or ("" if amount_raw is None else "单位不明"),
            "year_raw": year, "row_text": row_text.strip()}


# 2026-09-14 清理：删掉 `_scan_ledger`（71 行）。它是**业绩清单解析修复前的旧实现**
# （只认横排「序号|采购人」，不支持竖排键值对），已被 live 的
# `extract_contract_ledger_state → _scan_ledger_full → _scan_ledger_vertical` 完整取代。
# 全工作区 grep 只有定义行本身、零调用、无测试引用；`bid-ai/tmp/patch_ledger.py:3` 也早已把它
# 记为「死代码（只定义、从未调用）」。删它不影响 `_parse_row`/`_year_of`（仍被 live 路径调用）。


# 明确"空清单"的证据性后导：表头行之后 若见到这些表述 → 允许把空 records 视为明确确认的空清单。
_EMPTY_LEDGER_EVIDENCE = ("无相关业绩", "无业绩", "本次无业绩", "未附业绩清单", "不适用业绩", "无", "无类似业绩", "无符合条件的业绩", "空")


def _has_empty_ledger_evidence(lines, header: int) -> bool:
    """表头之后若干行的文本中是否存在明确无业绩表述。"""
    tail = "\n".join(lines[max(header + 1, 0): min(header + 40, len(lines))])
    return any(k in tail for k in _EMPTY_LEDGER_EVIDENCE)


# 解析失败线索：序号行出现但无法解析出 采购人或金额
_ORD_LINE_FAIL = ("parse_unparsable_row",)


# —— 业绩清单的两种真实格式（2026-09-11 实测）——
# A 竖排键值对（江中药业等）：
#     十二、近五年主要项目业绩清单
#     1、业绩1
#     项目名称 | 队列高通量基因分型芯片检测服务合同
#     采购人名称 | 四川大学华西第四医院
#     合同价格 | 399万
# B 横排表格（济宁医学院等）：
#     3、类似业绩一览表
#     序号 | 项目名称 | 使用单位 | 合同金额（万元） | 合同日期 | 使用单位联系人及电话
#     1 | 中药入血/入靶成分分析-PLUS 版技术服务合同 | 南京市食品药品监督检验院 | 3.5万 | 2025.4.1 | 周蓉馨；15951640117
# 原实现只认「序号 + 采购人」的横排表，两种都漏——实测 13 份响应文件中 10 份含业绩清单，
# 却只提出 1 份。**清单文字里已含项目名称/采购人/金额/日期，无需 OCR 下方合同扫描件。**
_PARTY_KEYS = ("采购人", "买方", "使用单位", "客户", "甲方", "委托方", "项目单位", "采购单位",
               # 2026-09-16 补齐：从 42 条真实业绩表头统计出的当事人列写法（旧词表只有上面 8 个，
               # 「单位名称/用户情况/客户名称/业主单位」全部认不出 → 83 份文档整片跳过）。
               "单位名称", "用户情况", "客户名称", "业主单位", "业主情况", "用户单位", "委托单位")
# 业绩清单标题（实测有多种写法：近五年主要项目业绩清单 / 类似业绩一览表 / 业绩情况表 …）
_LEDGER_TITLE = re.compile(r"(业绩|类似项目|合同).{0,10}(清单|一览|汇总|情况表|列表)")
_VERT_BLOCK = re.compile(r"^\s*\d{1,3}\s*[、.．]\s*业绩\s*\d*\s*$")
_VERT_KEY = re.compile(
    r"^(项目名称|采购人名称|采购人|买方名称|买方|使用单位|客户名称|甲方|委托方|项目单位|采购单位|"
    r"合同价格|合同金额|合同总价|合同总额|合同签订日期|合同签订时间|签订日期|签订时间|"
    r"交付日期|项目所在地|项目描述|服务期|备注)\s*\|\s*(.*)$")


def _scan_ledger_vertical(lines: list[str], start: int, source_doc_id: str,
                          header_region: str, unit: str | None) -> list[dict]:
    """竖排键值对业绩块 → records（与 `_parse_row` 同形）。"""
    records, cur = [], None
    last_key = None

    def flush():
        nonlocal cur
        if cur and (cur.get("party_a_raw") or cur.get("total_amount") is not None):
            cur["source_doc_id"] = source_doc_id
            cur["source_context"] = header_region.split("\n")[0] if header_region else ""
            cur["header_line"] = header_region
            records.append(cur)
        cur = None

    for i in range(start, len(lines)):
        ln = lines[i].strip()
        if not ln:
            continue
        if _VERT_BLOCK.match(ln):
            flush()
            # **必须设 row_ord**：`sync_contract_ledger` 会丢弃 row_ord 为 None 的记录，
            # 不设则整批解析结果落不了库（实测 13 条解析、0 条入库）。
            m_ord = re.match(r"\s*(\d{1,3})", ln)
            cur = {"row_ord": int(m_ord.group(1)) if m_ord else None,
                   "party_a_raw": None, "project_raw": None,
                   "total_amount": None, "unit": unit or "单位不明", "year_raw": None,
                   "row_text": ln, "evidence_idx": i + 1}
            last_key = None
            continue
        m = _VERT_KEY.match(ln)
        if m:
            if cur is None:                       # 没有「N、业绩N」引导也允许（有的文档省了）
                cur = {"row_ord": None, "party_a_raw": None, "project_raw": None,
                       "total_amount": None, "unit": unit or "单位不明", "year_raw": None,
                       "row_text": ln, "evidence_idx": i + 1}
            key, val = m.group(1), m.group(2).strip()
            if key == "项目名称":
                cur["project_raw"] = val or None
            elif key in _PARTY_KEYS or key.endswith("名称") and "采购" in key or key in ("使用单位", "买方"):
                if key in _PARTY_KEYS or key in ("采购人名称", "买方名称", "客户名称"):
                    cur["party_a_raw"] = val or None
            elif key in ("合同价格", "合同金额", "合同总价", "合同总额"):
                m2 = re.search(r"\d[\d,，]*(?:\.\d+)?", val)
                if m2:
                    num = float(m2.group(0).replace(",", "").replace("，", ""))
                    mult = 10000 if ("万" in val) else 1
                    cur["total_amount"] = round(num * mult, 2)
                    cur["unit"] = "万元" if mult == 10000 else "元"
            elif key in ("合同签订日期", "合同签订时间", "签订日期", "签订时间", "交付日期"):
                y = _year_of(val) or (re.search(r"(20\d{2})", val).group(1) if re.search(r"20\d{2}", val) else None)
                if y and not cur["year_raw"]:
                    cur["year_raw"] = y
            last_key = key
            continue
        # 值的续行（长项目名换行）：追加到上一个键
        if cur is not None and last_key and not _VERT_KEY.match(ln) and len(ln) < 80:
            if last_key == "项目名称" and cur["project_raw"]:
                cur["project_raw"] = (cur["project_raw"] + ln)[:200]
            elif last_key in ("采购人名称", "买方名称", "客户名称") and cur["party_a_raw"]:
                cur["party_a_raw"] = (cur["party_a_raw"] + ln)[:100]
    flush()
    return records


def _scan_ledger_full(text: str, source_doc_id: str) -> dict:
    """完整扫描业绩清单 → 返回状态字典。

    - header_found       : 表头是否定位（仅证明"看到表头"）
    - records            : 切成功解析出的行
    - full_result        : 是否取得"完整清单结构 + 确定性的空/非空结果"
                            完整 = 表头存在 且 末行明确闭合（出现 注：/投标人可按上述/4.…章节锚/4.1… 等）
                             或文本末尾（无截断信号）
    - empty_confirmed    : full_result 且 records 为空 且 有明确空清单证据 → 真"清空"依据
    - truncated          : 表头后很快到文本末尾（疑似被截断）→ 不可信
    - unparsed_rows      : 序号行出现但无法解析出 采购人/金额 的行数（数据行解析失败线索）
    """
    lines = text.splitlines()
    anchor = -1
    for i, ln in enumerate(lines):
        s_ = ln.strip()
        # 锚点必须是**章节标题**，不能是表格单元格：实测简历表里有
        # 「| 详见近五年主要项目业绩清单 | …」这类单元格，会抢在真正的
        # 业绩清单章节之前命中，导致把简历表当业绩数据解析。
        if "|" in s_ or len(s_) > 40:
            continue
        if _LEDGER_TITLE.search(s_) or "合作单位证明" in s_:
            anchor = i
            break
    # 竖排键值对格式优先：其"表头"是 `键 | 值`，横排判据套不上，不先试就会整片漏掉。
    if anchor >= 0:
        vstart = anchor + 1
        for j in range(anchor + 1, min(anchor + 8, len(lines))):
            if _VERT_BLOCK.match(lines[j].strip()) or _VERT_KEY.match(lines[j].strip()):
                vstart = j
                break
        vrecs = _scan_ledger_vertical(lines, vstart, source_doc_id, lines[anchor], None)
        if vrecs:
            return {"header_found": True, "records": vrecs, "full_result": True,
                    "empty_confirmed": False, "truncated": False, "unparsed_rows": 0}
    header = -1
    search_from = anchor if anchor >= 0 else 0
    for i in range(search_from, min(search_from + 25, len(lines))):
        ln = lines[i]
        # 横排表头：序号 + 当事人列。实测列名多样（采购人/买方/使用单位/客户…），
        # 原判据只认「采购人」，把「使用单位」等写法整片漏掉。
        if "序号" in ln and any(k in ln for k in _PARTY_KEYS):
            # ️ **必须还有金额列**（2026-09-16 实测踩到）：扩展当事人词表后，
            # 「序号 | 单位名称 | 相互关系」这类**关联方表**也被当成了业绩表 → 把电话号码
            # 当成了金额（实测一条 4.46 亿）。业绩清单按定义就列合同金额，没有金额列的
            # 不可能是业绩清单。表头常跨行（金额列可能写在下一行，如 合同 / 金额 / （万元）），故在 ±4 行窗口内找。
            window = [x for x in lines[i: i + 5]]
            window += [x for x in lines[max(0, i - 2): i]]
            # 表头必须能证明「这是一张业绩表」——**有金额列 或 有业绩内容列**。
            #   ① 金额列：业绩清单按定义列合同金额（挡掉「序号|单位名称|相互关系」的**关联方表**，
            #      它曾让电话号码被当成金额，实测一条 4.46 亿）；
            #   ② 业绩内容列：实测有的业绩表**不含金额**（如「…承担相关业绩一览表」只列
            #      履约时间/服务内容/采购单位/履约情况）—— 若一律拒掉，那张表的行
            #      **再也不会被重新解析**，早期写错的旧值就永远留在库里（用户实测 6 条如此）。
            if not (any(_AMOUNT_COL.search(x) for x in window)
                    or any(_LEDGER_CONTENT_COL.search(x) for x in window)):
                continue
            header = i
            break
    if header < 0:
        return {"header_found": False, "records": [], "full_result": False,
                "empty_confirmed": False, "truncated": False, "unparsed_rows": 0}
    unit = None
    for i in range(max(anchor, 0), min(header + 4, len(lines))):
        if "万元" in lines[i]:
            unit = "万元"
            break
        # 表头只写「（万）」也要认（同一类漏认，2026-09-16）
        if re.search(r"[（(]\s*万\s*[）)]", lines[i]):
            unit = "万元"
            break
        if re.search(r"（元）|\(元\)|\b元$", lines[i]):
            unit = "元"
            break
    start_idx = anchor if anchor >= 0 else header
    header_region = "\n".join(lines[max(anchor, 0): header + 4]) if anchor >= 0 else lines[header]

    records = []
    unparsed = 0
    cur = header + 1
    buf = []
    buf_start = -1
    closed = False  # 遇明确闭合锚点

    def flush():
        nonlocal buf, buf_start, unparsed
        if buf:
            r = _parse_row(" | ".join(buf), unit, lines[header] if header < len(lines) else None)
            if r.get("party_a_raw") or r.get("total_amount") is not None:
                # —— 行级可信性（2026-09-16）——
                # 扩展当事人列词表后实测出现**误抽**：某文档的表标题是「投标人业绩情况表」，
                # 但表体其实是**技术响应偏离表**（"我司完全响应/无偏离"），24 行全假
                # （采购人=项目名、无金额）。判据：**既无金额、采购人又不像机构名** → 丢弃。
                # ⚠️ 丢弃**不计入 `unparsed`** —— 它压根不是业绩行（是别的表），
                # 计进去会把整份文档判成"不完整"，反而伤及正常文档。
                if not _looks_like_ledger_row(r):
                    # ⚠️ 这里是 `flush()` **内部函数**，不是循环 —— 只能用 return 提前退出
                    # （第一版写成 `continue`，直接语法错误）。
                    buf = []
                    buf_start = -1
                    return
                r["source_doc_id"] = source_doc_id
                r["source_context"] = lines[start_idx] if start_idx >= 0 else ""
                r["evidence_idx"] = buf_start + 1
                r["header_line"] = header_region
                records.append(r)
            else:
                unparsed += 1  # 序号行解析失败
            buf = []
            buf_start = -1

    while cur < len(lines):
        ln = lines[cur].strip()
        if not ln:
            flush()
            cur += 1
            continue
        if ln.startswith("注：") or "投标人可按上述" in ln or ln.startswith(("4.", "4)", "6-2", "附表", "项目经理", "项目负责人")):
            flush()
            closed = True
            break
        if "序号" in ln and any(k in ln for k in _PARTY_KEYS):
            # ⚠️ **必须先判这一条**（认得的数据表头**重复出现** → 跳过该行，继续读下面的数据行）。
            # 第一版把下面「新表头即收尾」放在前面 → 表中间的重复表头把表**提前截断**，
            # 实测业绩行 402 → 338（丢的正是重复表头之后的那些行）。
            cur += 1
            continue
        if "序号" in ln and "|" in ln:
            # **认不得的表头 = 这张表结束了** → 收尾并停止。
            # 2026-09-16 实测踩到：不认得的表头（如《技术和服务要求响应表》）会被当成数据行，
            # 把**后面整张表的几千字**都吞进上一条业绩行的 `row_text` 里，
            # 卡片「原文」被撑满屏，金额也因列被撑歪而丢失。
            flush()
            closed = True
            break
        if _SECTION_HEAD.match(ln) and len(ln) <= 40:
            # 章节标题（`十三、《技术和服务要求响应表》` / `十四、类似项目业绩一览表`）同样表示
            # 本表已结束 —— 业绩数据行不会以「中文序号、」开头（那是**章节**编号）。
            flush()
            closed = True
            break
        if _ORD.match(ln):
            flush()
            buf_start = cur
            buf.append(ln)
        elif buf:
            buf.append(ln)
        cur += 1
    flush()
    # —— 完整性判定（保守，不猜）——
    #   full_result 仅在「表头存在 + 无解析失败行 + 有明确闭合锚点」时成立；
    #   文本自然到末尾但没有闭合锚点（直接 EOF）不算完整 —— 可能是被截断；
    #   仅有表头（无数据行/无空清单证据/无闭合）→ 不完整，不清空。
    if records:
        full_result = unparsed == 0 and closed
    else:
        full_result = unparsed == 0 and closed and _has_empty_ledger_evidence(lines, header)
    empty_confirmed = bool(full_result and records == [] and _has_empty_ledger_evidence(lines, header))
    return {"header_found": True, "records": records, "full_result": full_result,
            "empty_confirmed": empty_confirmed, "truncated": not full_result,
            "closed": closed, "unparsed_rows": unparsed}


def extract_contract_ledger(text: str, source_doc_id: str = "") -> list[dict]:
    """兼容旧调用：返回清单记录列表（无表头/空 → []）。"""
    return _scan_ledger_full(text, source_doc_id)["records"]


def extract_contract_ledger_state(text: str, source_doc_id: str = "") -> dict:
    """返回增强状态，供增量更新判断完整性/空清单证据。"""
    return _scan_ledger_full(text, source_doc_id)


LEDGER_PREFIX = "LEDGER-"


def _ledger_prefix(doc_id: str) -> str:
    return f"{LEDGER_PREFIX}{doc_id}-"


def _evidence(r: dict, ord_: int) -> str:
    """业绩行的证据文本：标头 + 本行**自己的**内容 + 位置。

    竖排格式的 row_text 只有 `N、业绩N` 这一行，项目名/采购人在别的键行上，
    只留 row_text 会丢掉本行的实际内容——故把 project_raw / party_a_raw 一并写入。
    """
    header = (r.get("header_line") or "").split("\n")[0][:60]
    # ⚠️ 限长 300 字：业绩行是**一行表格数据**，不该有几千字（实测曾出现 6836 字的证据，
    # 表后整张《技术和服务要求响应表》被吞进行缓冲 → 卡片被撑满屏）。
    row = (r.get("row_text") or "")[:300]
    parts = []
    if r.get("project_raw"):
        parts.append(f"项目:{r['project_raw'][:60]}")
    if r.get("party_a_raw"):
        parts.append(f"采购人:{r['party_a_raw'][:40]}")
    if r.get("total_amount") is not None:
        amt = r["total_amount"]
        parts.append(f"金额:{amt:,.0f}元" if float(amt).is_integer() else f"金额:{amt:,.2f}元")
    detail = " ".join(parts)
    pos = r.get("evidence_idx")
    return f"[业绩清单] {header} | 行{ord_}: {row} | {detail} | 位置:line{pos}"


def sync_contract_ledger(con, source_doc_id: str, header_found: bool, records: list[dict],
                         *, full_result: bool = True, empty_confirmed: bool = False,
                         truncated: bool = False, closed: bool = False) -> dict:
    """同一事务内按目标状态替换 [source_doc_id] 的业绩清单快照。

    完整性门槛（本次收紧）：
    - header_found=False → no_ledger_confirmed，不动旧记录。
    - header_found=True 但 无完整结果（full_result=False / truncated=True / 表后截断）→
      返回 'incomplete'，清空或缺行删除一律不执行（待复核）。
    - full_result 且 records 为空 且 empty_confirmed=True → 明确空清单才清空。
    - full_result 且 records 非空 → 按 ordinal 对齐同步。

    父记录变化检测（不只看 采购人+金额）：
    - 本提取器负责的行，其证据文本（含项目名称等项目内容）与旧值不同 → 旧子记录失效先删；
      "完全相同输入"（记录的全部业务内容与证据一致）才保留有效子记录。
    - 处理范围仍限定 contract_id LIKE 'LEDGER-{doc_id}-%'。
    """
    if not source_doc_id:
        raise ValueError("source_doc_id 必填，不能按空文档更新")
    prefix = _ledger_prefix(source_doc_id)
    if not header_found:
        total = con.execute("SELECT COUNT(*) FROM contracts WHERE contract_id LIKE ?",
                            (prefix + "%",)).fetchone()[0]
        return {"status": "no_ledger_confirmed", "upserted": 0, "deleted": 0,
                "kept_updated": 0, "cleared": 0, "total": total,
                "reason": "未取得业绩清单表头锚点，不清空、不更新"}
    if truncated or (header_found and not full_result):
        existing = con.execute("SELECT COUNT(*) FROM contracts WHERE contract_id LIKE ?",
                               (prefix + "%",)).fetchone()[0]
        # —— 受限放行（2026-09-16，PLAN-20260916-track-record-search §5.2）——
        # 旧规则：只要有一行解析失败 → 整份不写（宁缺毋滥）。实测这拦下了 **14 份 / 312 条**，
        # 而它们**全部**满足：① 表格有明确闭合锚点（不是被截断）；② 该文档**当前没有任何业绩行**
        # → 属于**纯新增**，既不会删旧行、也不会覆盖既有快照。
        # 故：仅当「有行 + 已收尾 + 无既有行」三者同时成立才放行，状态另记为 `synced_partial`
        # （与完整解析的 `synced` 严格区分，可审计）；**删除保护路径一字未动**。
        # 2026-09-16 口径再放宽**一格**（根因：全有或全无 → 部分解析时**旧值永不被纠正**；
        # 用户列出的 6 条「项目名仍是旧的 2023年-2025年」就是这么留下来的）：
        # **可疑解析禁止删除，但允许 upsert**（改写/新增）。删除仍只在 full_result 时发生。
        # ️ **不再要求 `closed`**（2026-09-16 二次修正）：`closed` 只是"表已收尾"的**代理**，
        # 它要求表后紧邻 `注：`/章节标题/另一张表 —— 实测大量表后面跟的是普通段落，
        # 于是永远 closed=False → 旧值永不被纠正（用户列出的 6 条正是此因）。
        # **安全关键从来不是 `closed`，而是"可疑解析下不删行"**（下面 `partial` 分支保证）。
        if not records:
            return {"status": "incomplete", "upserted": 0, "deleted": 0,
                    "kept_updated": 0, "cleared": 0, "total": existing,
                    "reason": "表头命中但未取得完整清单（截断/尾段缺失/已有快照），"
                              "不清空、不做缺行删除"}
        partial = True
    else:
        partial = False

    desired: dict[int, dict] = {}
    for r in records:
        ord_ = r.get("row_ord")
        if ord_ is None:
            continue
        desired[int(ord_)] = r

    stats = {"upserted": 0, "deleted": 0, "kept_updated": 0, "cleared": 0}
    with con:
        cur_rows = {int(r["ordinal"]): r["contract_id"]
                    for r in con.execute(
                        "SELECT ordinal, contract_id FROM contracts WHERE contract_id LIKE ?",
                        (prefix + "%",))}
        # —— 明确空清单（empty_confirmed）才允许清空 ——
        if not desired:
            if not empty_confirmed:
                stats["status"] = "empty_not_confirmed"
                stats["total"] = len(cur_rows)
                return stats
            for cid in cur_rows.values():
                con.execute("DELETE FROM contract_items WHERE contract_id=?", (cid,))
                con.execute("DELETE FROM contracts WHERE contract_id=?", (cid,))
            stats["cleared"] = len(cur_rows)
            stats["deleted"] = len(cur_rows)
            stats["total"] = 0
            stats["status"] = "cleared"
            return stats
        for ord_, cid in cur_rows.items():
            if ord_ not in desired:
                if partial:
                    continue      # 可疑解析：**不删**（只做 upsert）
                con.execute("DELETE FROM contract_items WHERE contract_id=?", (cid,))
                con.execute("DELETE FROM contracts WHERE contract_id=?", (cid,))
                stats["deleted"] += 1
        for ord_, r in desired.items():
            cid = f"{prefix}{ord_}"
            evid = _evidence(r, ord_)
            if ord_ in cur_rows:
                old = con.execute(
                    "SELECT party_a, total_amount, evidence_text FROM contracts WHERE contract_id=?",
                    (cid,)).fetchone()
                content_same = (old is not None and old["party_a"] == r.get("party_a_raw")
                                and str(old["total_amount"]) == str(r.get("total_amount"))
                                and str(old["evidence_text"] or "") == evid)
                con.execute(
                    "UPDATE contracts SET party_a=?, total_amount=?, evidence_text=? WHERE contract_id=?",
                    (r.get("party_a_raw"), r.get("total_amount"), evid, cid))
                if not content_same:
                    # 父行业务内容或证据变化 → 旧子记录失效先删
                    con.execute("DELETE FROM contract_items WHERE contract_id=?", (cid,))
                stats["kept_updated"] += 1
            else:
                con.execute(
                    "INSERT INTO contracts (contract_id, document_id, ordinal, contract_number,"
                    " party_a, party_b, contract_vendor, vendor_scope, vendor_evidence,"
                    " contract_date, total_amount, evidence_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, source_doc_id, ord_, None,
                     r.get("party_a_raw"), None, None, None, None,
                     None, r.get("total_amount"), evid))
                stats["upserted"] += 1
        stats["status"] = "synced_partial" if partial else "synced"
    stats["total"] = con.execute(
        "SELECT COUNT(*) FROM contracts WHERE contract_id LIKE ?", (prefix + "%",)).fetchone()[0]
    return stats


def extract_and_sync(con, source_doc_id: str, native_text: str | None) -> dict:
    """真实入口：从该文档原生正文提取业绩清单并同步替换其清单快照。

    - None / 空串 / 纯空白 → no_native_text，不动旧记录、不计成功0条。
    - 表头未命中 → no_ledger_confirmed；表头命中但非完整 → incomplete；明确空 → cleared；有行 → synced。
    """
    if native_text is None or not native_text.strip():
        return {"status": "no_native_text", "upserted": 0, "deleted": 0,
                "kept_updated": 0, "cleared": 0,
                "reason": "无可用原生正文(None/空/空白)，不清空旧记录，不计成功提取零条"}
    state = extract_contract_ledger_state(native_text, source_doc_id=source_doc_id)
    return sync_contract_ledger(con, source_doc_id, state["header_found"], state["records"],
                                full_result=state["full_result"],
                                empty_confirmed=state["empty_confirmed"],
                                truncated=state["truncated"],
                                closed=state.get("closed", False))


# ============================================================================
# R4 合同服务明细（contract_items）：D9 语义
# ============================================================================

# 服务明细表头锚点 & 行分类
_SVC_HEADER_TOKENS = ("服务名称", "数量", "单价", "金额", "总价", "服务类别")
_SUBTOTAL_TOKENS = ("合计", "总计", "小计", "总计：")
_CONTRACT_TOTAL_TOKENS = ("合同总金额", "合同金额", "本合同总价", "总计（元）")


def _parse_amount(s: str) -> float | None:
    """解析金额/数量/单价字符串 → float；解析失败(含空)返回 None（**不补值**）。

    **取前导数字**，忽略其后紧跟的单位/说明 —— 实测坑：合同表的金额列常写成
    `44,000.00 元`，原实现只去掉逗号与空格，`float("44000.00元")` 抛错 → 金额被判为空。
    该单元格**确实写着金额**，属解析遗漏而非「文档未记载」，故必须读出来。

    保守边界（宁缺毋滥，保持原有行为）：
      · 字符串**不以数字开头**（如 `小写：270,000.00`、`（须单独密封一式三份）`）→ None，不猜；
      · 含「万」→ None —— 万元口径的换算在业绩清单那边单独处理，这里不擅自换算。
    """
    if s is None:
        return None
    t = str(s).replace(",", "").replace("，", "").replace(" ", "").replace("　", "")
    if not t or "万" in t:
        return None
    m = re.match(r"^-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


# 服务明细表头识别（放宽，2026-09-10 实测修订）
# 实测：合同服务明细表的列名写法多样，原规则要求「服务名称＋数量＋单价」三者齐全，
# 导致两类**真实明细表被整体漏掉**：
#   `序号|服务名称|计量单位|数量|合计金额（元/年）|…`   ← 无"单价"
#   `测试项目|单价（元）|数量|总价（元）`                ← 无"服务名称"
# 改为：具名列（任一）＋ 数量列 ＋ 价格列（任一）。
_HDR_NAME_COLS = ("服务名称", "服务类别", "服务内容", "服务项目", "测试项目", "检测项目", "项目名称")
_HDR_PRICE_COLS = ("单价", "金额", "总价", "价格", "费用")


def is_service_detail_header(line: str) -> bool:
    """该行是否像「服务明细」表头。"""
    s = line or ""
    return ("数量" in s) and any(k in s for k in _HDR_NAME_COLS) and any(k in s for k in _HDR_PRICE_COLS)


def map_header_columns(header_line: str) -> dict:
    """按**表头名**定位列索引 → {field: idx}。

    为什么必须按名而不是按位置：实测合同表至少三种列形：
      `服务类别|序号|服务名称|单位|数量|单价|金额`   （原始两份合同，有类别列）
      `序号|服务名称|单位|数量|单价|总价`             （无类别列）
      `序号|服务名称|计量单位|数量|合计金额（元/年）` （无单价列）
    按位置映射会让后两种整列错位（category 取到序号），产品匹配随之失效。
    """
    cells = [c.strip() for c in (header_line or "").split("|")]
    idx: dict[str, int] = {}
    for i, c in enumerate(cells):
        if not c:
            continue
        if "类别" in c and "category" not in idx:
            idx["category"] = i
        elif "序号" in c and "seq" not in idx:
            idx["seq"] = i
        elif any(k in c for k in _HDR_NAME_COLS) and "service" not in idx:
            idx["service"] = i
        elif "数量" in c and "quantity" not in idx:
            idx["quantity"] = i
        elif "单位" in c and "unit" not in idx:
            idx["unit"] = i
        elif "单价" in c and "unit_price" not in idx:
            idx["unit_price"] = i
        elif any(k in c for k in ("金额", "总价", "价格", "费用")) and "amount" not in idx:
            idx["amount"] = i
    return idx


def parse_contract_service_table(text: str) -> list[dict]:
    """从合同原生正文解析「服务明细」表格 → 行级 record。

    text 由 docx_full 保序输出（每个表格行用 ' | ' 连接）。返回行：
      {category, seq, service_name, unit, quantity, unit_price, line_amount,
       row_type(detail|product_subtotal|contract_total|header|note),
       row_text, evidence_idx}
    金额列取值优先级：金额/总价 列；缺失 → None（明细金额未知）。
    """
    lines = text.splitlines()
    header = -1
    for i, ln in enumerate(lines):
        if is_service_detail_header(ln):
            header = i
            break
    if header < 0:
        # 兼容"服务内容/服务明细"标题在表格标题行的情况
        for i, ln in enumerate(lines):
            if "服务明细" in ln:
                # 向后找符合服务明细表头特征的行
                for j in range(i + 1, min(i + 12, len(lines))):
                    if is_service_detail_header(lines[j]):
                        header = j
                        break
                if header >= 0:
                    break
    if header < 0:
        return []

    recs: list[dict] = []
    cur = header + 1
    while cur < len(lines):
        ln = lines[cur].strip()
        if not ln:
            cur += 1
            continue
        # 表头重复（同文档多张表）跳过——用与入口一致的表头判据，否则放宽后仍会漏跳
        if is_service_detail_header(ln):
            cur += 1
            continue
        # 遇到**另一张表的表头**即停止：其后是别的表（如技术参数表/物资清单），
        # 不属于本合同服务明细。判据：非数字开头 + 含 序号/编号 + 含 名称/项目/参数/要求/规格，
        # 且不是服务明细表头。
        if (ln[:1] and not ln[:1].isdigit()
                and re.search(r"(序号|编号)", ln)
                and re.search(r"(名称|项目|参数|要求|规格)", ln)):
            break
        # ⚠️ **必须保留空单元格**：原写法 `if c and c.strip()` 会丢掉空列，
        # 导致「序号列为空」的行**整体左移一位** —— 实测 OCR 合同里大量明细行序号为空
        # （`| LC-MS/MS 全谱代谢组（RP-Plus）-实验 | 元/样 | 1000 | 150.00 | 150,000.00`），
        # 左移后 `service_name` 变成 `元/样`、金额变成 None，整行金额丢失。
        # 表头解析（`map_header_columns`）本就不丢空列，这里与它保持一致，位置才对得上。
        # 仅去掉**末尾**由序列化/OCR 产生的多余空单元格（避免影响位置映射分支的列数判断）。
        cells = [c.strip() for c in ln.split("|")]
        while cells and cells[-1] == "":
            cells.pop()
        if len(cells) < 3:
            cur += 1
            continue
        # 合同总金额行
        # ⚠️ 除既有词表外，还要认「同时含**大写**与**小写**」的行 —— 那是合同总额的固定书写惯例
        # （`合计（元）： | 大写：贰拾柒万元 | 小写：270,000.00`），而 `_CONTRACT_TOTAL_TOKENS`
        # 里没有 `合计（元）`。不认它会导致：该行落成 `detail`、金额为 None →
        # ① 污染 `has_unknown`（把整类产品判成"金额未知"而不参与命中）；
        # ② 一旦它解析出金额，会被**重复计入产品明细合计**（等于把合同总额混进产品金额，D9 禁止）。
        # 不用「合计」是因为它同时是产品小计词（`_SUBTOTAL_TOKENS`），加了会抢分类。
        _is_total_row = (any(k in ln for k in _CONTRACT_TOTAL_TOKENS)
                         or ("大写" in ln and "小写" in ln))
        if _is_total_row:
            amt = None
            for c in cells:
                v = _parse_amount(c)
                if v is not None and v > 0:
                    amt = v
                    break
            recs.append({"category": "合同总金额", "seq": None, "service_name": "合同总金额",
                         "unit": None, "quantity": None, "unit_price": None,
                         "line_amount": amt, "row_type": "contract_total",
                         "row_text": ln, "evidence_idx": cur + 1})
            cur += 1
            continue
        # 优先按**表头名**取列；表头信息不足时回退到位置映射（兼容既有 7 列样式）
        cols = map_header_columns(lines[header])
        by_name = "service" in cols and ("amount" in cols or "unit_price" in cols)

        def _cell(cells_: list[str], key: str):
            i = cols.get(key)
            return cells_[i].strip() if (i is not None and i < len(cells_)) else None

        try:
            if by_name:
                category = _cell(cells, "category")
                seq_s = _cell(cells, "seq")
                service = _cell(cells, "service")
                unit = _cell(cells, "unit")
                qty = _parse_amount(_cell(cells, "quantity") or "")
                up = _parse_amount(_cell(cells, "unit_price") or "")
                amt = _parse_amount(_cell(cells, "amount") or "")
            elif len(cells) >= 7:
                category = cells[0]; seq_s = cells[1]; service = cells[2]
                unit = cells[3] if len(cells) > 3 else None
                qty = _parse_amount(cells[4]) if len(cells) > 4 else None
                up = _parse_amount(cells[5]) if len(cells) > 5 else None
                amt = _parse_amount(cells[6]) if len(cells) > 6 else None
            elif len(cells) == 6:
                # 7 列样式但金额列空（尾分隔被过滤）
                category = cells[0]; seq_s = cells[1]; service = cells[2]
                unit = cells[3]; qty = _parse_amount(cells[4]); up = _parse_amount(cells[5])
                amt = None  # 金额列缺失 → 未知
            else:
                # 5 列：类别 | 序号 | 服务名称 | 单价 | 金额
                category = cells[0]; seq_s = cells[1]; service = cells[2]
                unit = None; qty = None
                up = _parse_amount(cells[3]) if len(cells) > 3 else None
                amt = _parse_amount(cells[4]) if len(cells) > 4 else None
        except Exception:  # noqa: BLE001
            cur += 1
            continue
        seq = None
        if seq_s and seq_s.isdigit():
            seq = int(seq_s)
        row_type = "detail"
        if any(k in (service or "") for k in _SUBTOTAL_TOKENS) or any(k in (category or "") for k in _SUBTOTAL_TOKENS):
            row_type = "product_subtotal"
        elif not service:
            # 只要求服务名；**不再要求 category**——实测有无「服务类别」列的合同表，
            # 原先 `not category → note` 会让整张无类别列的表全部降级为 note（明细全丢）。
            row_type = "note"
        recs.append({"category": category, "seq": seq, "service_name": service,
                     "unit": unit, "quantity": qty, "unit_price": up,
                     "line_amount": amt, "row_type": row_type,
                     "row_text": ln, "evidence_idx": cur + 1})
        cur += 1
    if config.CONTRACT_DERIVE_AMOUNT:
        _fill_amounts_from_qty_price(recs)
    return recs


def _fill_amounts_from_qty_price(recs: list[dict]) -> None:
    """**开关开启时**：整表结构完整则用 数量×单价 补齐空金额（就地修改）。

    前提（必须整张表都满足，这是安全性的来源）：该合同**全部** detail 行都有 `数量 > 0` 且 `单价 > 0`，
    且至少一行缺金额。只有这种「表头与列都在、无部分 OCR」的表才敢乘 —— 实测 98 份里仅 3 份满足；
    而「部分行有数量单价」的里有一半是**串列**（`数量=500 单价=80000` → 4000 万，合同总额才 14.4 万）。

    ⚠️ **刻意不拿 `contracts.total_amount` 做上界** —— D9 明写它不得参与产品金额判断。

    **默认关闭**（`config.CONTRACT_DERIVE_AMOUNT=false`），因为它与计划 §2
    「无法确认时返回「金额未确认」，**不能猜测**」的口径冲突，属**需求所有者**的决定，
    而同项目既有模式（OCR / 方案生成）都是「默认关闭 + 有书面理由」。
    实测（开启后）：与文件名成交价完全一致的合同 80 → 82；新增的唯一「对不上」是 0.00005% 舍入残差；
    「明细合计>总额」错位检查 0；金标准 Recall 88.5% → **92.3%（跨过 ≥90% 门槛）**。
    """
    details = [r for r in recs if r.get("row_type") == "detail"]
    if not details or not any(r.get("line_amount") is None for r in details):
        return
    if not all((r.get("quantity") or 0) > 0 and (r.get("unit_price") or 0) > 0 for r in details):
        return
    for r in details:
        if r.get("line_amount") is None:
            r["line_amount"] = round(r["quantity"] * r["unit_price"], 2)
            r["amount_source"] = "derived_qty_x_price"


# —— 关于「用 数量×单价 补齐空金额」：**评估过，决定不做** ——
# 已实测（2026-09-11，见 tmp/eval_qty_price_rule.py）：
#   · 判据「该合同**全部** detail 行都有 数量>0 且 单价>0」在 98 份合同里只命中 **3 份**；
#   · 它对金标准召回只有 **+1 条**收益（80.8% → 84.6%，**仍低于 90% 门槛**）；
#   · 推导值本身**是对的**（`YOE2024114080`：44,000+117,000+128,000+80,000 = 369,000，
#     恰好等于该合同总额，可作独立验证）。
# **但仍不做** —— 它推翻的是项目**刻意且已测试**的保守决定：
#   `tests/test_contract_items.py::test_amount_unknown_stays_unknown_not_zero`
#   断言「金额列为空即未知，`数量=5 单价=100` 也不得推导成 500」，
#   对应计划 §2「**无法确认时返回「金额未确认」，不能猜测**」。
# 为 +1 条记录推翻一条写进计划的保守原则，不值得，也不应由实现者单方面决定。
# 若日后要开：需先改计划 §2 的措辞与上述测试，并明确「算术推导」不算「猜测」的理由。


# ============================================================================
# 材料事实提取（确定性切片，2026-09-11）
# ============================================================================
# 背景：响应文件里普遍是「先用文字说明、下方跟扫描件佐证」。用户据此指出
# **结构化信息在文字里，扫描件只需标记"有佐证"**，不必 OCR（内嵌图 7,948 张）。
#
# **但全清单解析被对抗复核判定不可行**（见 docs/business/material-facts-feasibility.md）：
# 把"材料清单条目"与"承诺函正文/评分要求/章节标题"分开的判据本质是语义的——
# 实测规则跑 13 份正文只命中 14 行（真实条目 60+），且
# `（五）拟派项目实施团队（含社保证明）`（章节标题）与
# `（三）财务状况表或银行资信证明`（材料条目）语法完全同构，词表划不开。
#
# 故本模块**只做特征鲜明的一个切片**：「现附上…」声明句。
#   - 复核认定这是"唯一能拿到精确期间的位置"；
#   - 句式特征强，误抓面小；
#   - **不判 source**（是"我方附件"还是"采购人要求"）——那需要语义判断，
#     未获 LLM 授权前不做，也不假装做了。
#
# 关键防误抓：**模板占位符不得当真实期间**。实测同一文档里既有
#   `现附上我方（2023年度）财务报告复印件…`          ← 真值
#   `现附上我方（填写"具体的年度、或半年度、或季度"）…` ← 占位符
#   `现附上自  年  月  日至  年  月  日期间我方缴纳…`   ← 空白占位
# 含「填写」「＿」「年  月」空格的整行一律跳过。
_MF_DECL = re.compile(r"现附上.{0,140}")
_MF_PERIOD = re.compile(
    r"自\s*(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?\s*"
    r"(?:至|-|—)\s*(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?")
_MF_OPEN = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(?:至今|起)")
# 无月份的开放区间：`2021年至今`（实测 `社保证明（2021年至今为上海欧易缴纳）` 8 处）
_MF_OPEN_YEAR = re.compile(r"(20\d{2})\s*年\s*(?:至今|起)")
_MF_YEAR = re.compile(r"[（(]\s*(20\d{2})\s*年度?\s*[）)]")
_MF_PLACEHOLDER = re.compile(r"填写|＿|_{2,}|年\s{2,}月|月\s{2,}日|具体的年度")
# 材料类型 → fact_type（只用 schema 既有枚举值）
_MF_KINDS = (("社会保险", "social_security_month"), ("社会保障", "social_security_month"),
             ("社保", "social_security_month"),
             ("税收", "finance_period"), ("纳税", "finance_period"),
             ("财务报告", "finance_period"), ("财务审计", "finance_period"),
             ("资信证明", "finance_period"))


def extract_period(text: str) -> str | None:
    """从一行材料条目/声明句里提取**期间或年度**；取不到返回 None（不猜）。

    形态（均由真实正文实测）：
      `自2025年2月1日至2025年2月28日` → `2025-02-01~2025-02-28`
      `2023年5月至今`                 → `2023-05~`
      `2024年度`                      → `2024`
      `2025年连续三个月`               → `2025`（只到年，不臆造月份）

    **占位符一律返回 None**：实测同文档里真值与占位并存
      `（2023年度）财务报告`（真值） vs `（填写“具体的年度…”）`（占位）
      `自2025年2月1日至…`（真值）   vs `自  年  月  日至  年  月  日`（空白占位）

    泛用函数：`extract_material_facts`（声明句）与 fact_value 抽取共用，
    避免两处各写一套、口径漂移。
    """
    s = (text or "").strip()
    if not s or _MF_PLACEHOLDER.search(s):
        return None
    m = _MF_PERIOD.search(s)
    if m:
        a = f"{m.group(1)}-{int(m.group(2)):02d}" + (f"-{int(m.group(3)):02d}" if m.group(3) else "")
        b = f"{m.group(4)}-{int(m.group(5)):02d}" + (f"-{int(m.group(6)):02d}" if m.group(6) else "")
        return f"{a}~{b}"
    m = _MF_OPEN.search(s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}~"
    m = _MF_OPEN_YEAR.search(s)
    if m:
        return f"{m.group(1)}~"
    m = _MF_YEAR.search(s) or re.search(r"(20\d{2})\s*年度?", s)
    if m:
        return m.group(1)
    m = re.search(r"(20\d{2})\s*年", s)
    return m.group(1) if m else None


# —— 期间扫描（「三类材料定位」的财务/社保期间用）——
# 与 `extract_period` 的分工：`extract_period` 从**单条声明句**里抽精确期间（带区间）；
# 本组函数从**整段文本**扫出期间候选（宽口径），供三模块定位用。
_SCAN_YEAR = re.compile(r"(20\d{2})\s*年度?")
_SCAN_YM = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")
_SCAN_RANGE = re.compile(r"(20\d{2})\s*[-~至]\s*(20\d{2})")
# ⚠️ **非材料期间**的上下文 —— 这些句子里的年月不是「材料所属期间」，必须先剔除。
# 实测（2026-09-15 需求方反馈）：正文里的
#   `本授权书有效期限为：2025年3月14日至2026年3月14日，特此声明。`
# 到期日被抽成 `social_security_month=2026-03`（**伪造值**）—— 授权书写的是委托期限，
# 与社保缴纳月份无关。同类还有证书有效期、投标有效期。
_SCAN_NOISE_CTX = re.compile(
    r"(?:有效期限|有效期|授权期限|委托期限|证书有效|投标有效|认证有效)"
    r"[^\n。；]{0,80}")
# 合理年份区间（与 `app/api.py::parse_demo_date` 同口径）：材料期间不会落在范围外。
# 实测脏值 `2046-06` / `2029-03` / `2028-07` / `2009-03` 全部来自无关上下文。
_SCAN_YEAR_MIN, _SCAN_YEAR_MAX = 2015, 2030


def scan_periods(name: str, text: str, *, head: int = 2000) -> list[str]:
    """从「文件名 + 正文前 `head` 字」扫描期间候选：月份优先，否则年度，再否则年度区间。

    返回按值排序的去重列表；**提不到返回空**（宁缺毋滥）。

    两处收紧（2026-09-15 修 bug）：
      1. 剔除「有效期／授权期限」等**非材料期间**上下文（`_SCAN_NOISE_CTX`）——
         否则授权书到期日会被当成社保月份（实测：`2026-03` 伪造值）；
      2. 只认 `[_SCAN_YEAR_MIN, _SCAN_YEAR_MAX]` 内的年份 —— 剔除 `2046-06` 这类脏值。
    """
    src = _SCAN_NOISE_CTX.sub(" ", name + "\n" + (text or "")[:head])
    mons = sorted({f"{y}-{int(m):02d}" for y, m in _SCAN_YM.findall(src)
                   if _SCAN_YEAR_MIN <= int(y) <= _SCAN_YEAR_MAX})
    if mons:
        return mons
    yrs = sorted({y for y in _SCAN_YEAR.findall(src)
                  if _SCAN_YEAR_MIN <= int(y) <= _SCAN_YEAR_MAX})
    if yrs:
        return yrs
    out: list[str] = []
    for a, b in _SCAN_RANGE.findall(src):
        out += [y for y in (a, b) if _SCAN_YEAR_MIN <= int(y) <= _SCAN_YEAR_MAX]
    return sorted(set(out))


# —— 按「材料标记」限定范围的期间抽取（2026-09-15 修）——
# 由来（需求方反馈）：一份 48k 字的**扫描响应件**里夹着营业执照、合同、证书、完税凭证……
# 全量扫描会把「营业执照**登记机关** 2024年09月26日」「签署**日期** 2026年8月27日」
# 当成社保月份（实测正是这两个假值）。真正的社保期间在「社会保险费缴费记录」表里。
# 故：**只在材料标记附近取期间**，且接受连字符形态 `YYYY-MM`（社保/完税表多用此形）。
_SCAN_YM_DASH = re.compile(r"(20\d{2})[-/.](\d{1,2})(?![\d])")
# **只认「月」**：`YYYY-MM` 后面**不接**「-日」/「.日」——用于区分
# 「费款所属期」（是**月**）与「入库日期」（是**具体日**）。
_SCAN_YM_DASH_MONTH = re.compile(r"(20\d{2})[-/.](\d{1,2})(?![-/.\d])")
_SCAN_YM_CN_MONTH = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?!\s*\d)")
# 材料标记（按 kind 分）：**强**标记 = 该材料段落的标题；宽标记 = 相关词（兜底用）
SS_MARKERS_STRONG = ("社会保险费缴费记录", "社会保障记录")
SS_MARKERS = SS_MARKERS_STRONG + ("社会保险事业管理中心", "社保经办机构", "社会保险", "社保")
FIN_MARKERS_STRONG = ("财务报告", "审计报告", "资产负债表", "利润表", "资信证明")
FIN_MARKERS = FIN_MARKERS_STRONG + ("纳税", "完税", "税收凭据", "税收业务专用章")


def filter_periods_by_project_date(periods: list[str], project_folder: str, *,
                                   margin_months: int = 3) -> list[str]:
    """剔除**晚于「项目日期 + margin」**的期间 —— 社保/财务材料不可能来自未来。

    项目目录名自带登记日期（`20260814-标书-…`）。实测（2026-09-16 需求方反馈）：
    证书/身份证/认证的**有效期**（`2027-12`/`2028-04`/`2027-05`）会紧邻社保段落被收进来，
    产出「未来社保月份」—— 一个 2026-08 的项目报出 2028-04 的社保，一眼即假。
    `margin_months=3` 是留给「登记日 → 实际投标」的正常间隔（实测 23 例只差 1 个月）。

    取不到项目日期（目录名无 `YYYYMMDD` 前缀）时**不筛**（宁可不筛，不可误删）。
    """
    import re as _re

    m = _re.match(r"^\s*(\d{4})(\d{2})", project_folder or "")
    if not m:
        return periods
    cutoff = int(m.group(1)) * 12 + int(m.group(2)) + margin_months
    out: list[str] = []
    for p in periods:
        mm = _re.match(r"^(\d{4})-(\d{1,2})$", p or "")
        if mm and int(mm.group(1)) * 12 + int(mm.group(2)) > cutoff:
            continue                      # 未来期间 → 剔除
        out.append(p)
    return out


def scan_periods_near(text: str, markers: tuple[str, ...], *,
                      window: int = 150, head: int = 120_000,
                      same_line: bool = False, month_only: bool = False) -> list[str]:
    """只在 `markers` 的**上下文**里取期间；找不到返回空（宁缺毋滥）。

    `same_line=False`（默认）：标记 ±`window` 字窗口 —— 用于**记录表**
      （`社会保险费缴费记录`：OCR 把表格列打散，期间与关键词常不在同一行）。
    `same_line=True`：**只取含标记的那一行**内的期间 —— 用于**声明句**
      （`现附上自2025年2月1日至…我方缴纳的社会保险凭据`：期间与关键词同句同行）。
    `month_only=True`：**只认「月」形态**，不认完整日期 —— 用于社保**记录表**：
      表里有两类日期列，`费款所属期` 是**月**（`2026-04`），`入库日期` 是**具体日**
      （`2026-05-14`）。不区分会把入库日当社保月份（2026-09-16 需求方实测：
      一份文件报出 4 个月，全是入库日期与隔壁税收凭据段的日期）。

    ⚠️ 为什么必须分档（2026-09-16 实测）：统一的 ±150 窗口会把**同段里出现的任意日期**
    都收进来 —— 实测窗口内**约 650 条是「签署时间/日期」**。**宽标记（`社保` 泛词）必须走同行**。

    仍受 `[_SCAN_YEAR_MIN, _SCAN_YEAR_MAX]` 年份区间与月份合法性（1–12）约束，
    并**先做「有效期/授权期限」等噪声上下文掩码**。
    """
    t = _SCAN_NOISE_CTX.sub(" ", (text or "")[:head])
    if same_line:
        segs = [ln for ln in t.splitlines() if any(mk in ln for mk in markers)]
    else:
        segs = []
        for mk in markers:
            start = 0
            while True:
                i = t.find(mk, start)
                if i < 0:
                    break
                segs.append(t[max(0, i - window): min(len(t), i + window)])
                start = i + len(mk)
    got: set[str] = set()
    for seg in segs:
        pairs = (_SCAN_YM_CN_MONTH.findall(seg) + _SCAN_YM_DASH_MONTH.findall(seg)
                 if month_only
                 else _SCAN_YM.findall(seg) + _SCAN_YM_DASH.findall(seg))
        for y, m in pairs:
            if _SCAN_YEAR_MIN <= int(y) <= _SCAN_YEAR_MAX and 1 <= int(m) <= 12:
                got.add(f"{y}-{int(m):02d}")
    return sorted(got)


def extract_material_facts(text: str, document_id: str) -> list[dict]:
    """从「现附上…」声明句提取材料事实。

    返回 [{document_id, fact_type, fact_value, evidence_text}]。
    **调用方注意**：本函数**不判 source**——不知道这条是"我方已附材料"还是
    "采购人要求/模板"。在获得语义分类能力前，产出应标注该局限，不得当作
    "已核实附件"使用。

    占位符（含"填写"/空白年月）一律不产出，如实跳过。
    """
    out: list[dict] = []
    for i, raw in enumerate((text or "").splitlines()):
        line = raw.strip()
        if "现附上" not in line:
            continue
        decl = _MF_DECL.search(line)
        if not decl:
            continue
        s = decl.group(0)
        if _MF_PLACEHOLDER.search(s):          # 模板占位，不是真值
            continue
        kind = next((k for kw, k in _MF_KINDS if kw in s), None)
        if not kind:
            continue
        value = extract_period(s)                # 统一的期间抽取（与 fact_value 共用口径）
        if not value:
            continue                            # 无真实期间 → 不产出（宁缺毋滥）
        out.append({"document_id": document_id, "fact_type": kind, "fact_value": value,
                    "evidence_text": line[:200], "line": i + 1})
    return out


# —— 合同头信息：编号 / 甲方 / 乙方 / 合同总额 ——
# 实测覆盖率（98 份 CTL）：编号(文件名) 96% / 甲方 98% / 乙方 97% / 总额(文件名) 94%。
# **误抓教训**：`甲\s*方[:：]?\s*(...)` 这种宽松写法会抓到正文句子——
# 实测抓到 `委托乙方进行`、`接受委托并进行此项外协服务工作`。
# 故**要求 甲方/乙方 后必须有分隔符 `：` 或 `|`**，收紧后全部为真实单位名。
_PARTY = {
    # `（使用部门）`/`（供应商）` 这类**中文括注**必须吃掉，否则 `甲方（使用部门）：昆明理工大学…`
    # 会整条漏抓（实测 1 例）。括注可选、非贪婪，不影响无括注的常规写法。
    # ⚠️ 取值字符类**必须允许空格/全角空格**（但**不含换行**）：机构名内部常有断行
    # （`军事预 防医学系`）——排除空白会让名字被**静默截断**成半截且看不出错
    # （对抗性复核实测：库里 2 行的 party_a 就是被截断的「…军事预」）。
    # 捕获到的空白由 `_clean_org` 统一去掉。
    "party_a": re.compile(r"甲\s*方(?:[（(]\s*[^）)]{0,12}?\s*[）)])?"
                        r"[^一-龥]{0,4}[:：|]\s*\|?\s*([一-龥A-Za-z0-9（）()· 　]{4,40})"),
    "party_b": re.compile(r"乙\s*方(?:[（(]\s*[^）)]{0,12}?\s*[）)])?"
                        r"[^一-龥]{0,4}[:：|]\s*\|?\s*([一-龥A-Za-z0-9（）()· 　]{4,40})"),
}
_FILE_AMOUNT = re.compile(r"([\d,，]{3,}(?:\.\d{1,2})?)\s*(?:\(\d\))?\.(?:pdf|jpg|jpeg|png)$", re.I)
# 正文兜底**必须带「元」**。反例（实测确认）：付款条款「向乙方支付合同总金额的 100 %，
# 即人民币 180000 元」在不要求单位时会把百分比 `100` 抓成合同总额。表头式（「元」在数字前）
# 另列一条。当前 98 份 CTL 全量回放：仅 6 份走正文兜底，收紧前后取值**完全一致**（零影响）。
_TEXT_TOTAL_DECL = re.compile(r"(?:合同总金额|合同总额|合同金额|总金额|价税合计)[^\d\n]{0,14}"
                              r"([\d,，]{3,}(?:\.\d{1,2})?)\s*元")
_TEXT_TOTAL_ROW = re.compile(r"合同总(?:金额|额)\s*[（(]\s*元\s*[）)]\s*\|\s*([\d,，]+(?:\.\d+)?)")


def contract_header_facts(text: str, filename: str) -> dict:
    """合同头信息：编号 / 甲方 / 乙方 / 合同总额。取不到的字段为 None（不猜）。

    **总额口径**：文件名金额优先——项目 CLAUDE.md 已记载「人工命名＝成交价，正文表价常是优惠前标价」，
    实测 2 处文件名 < 正文（48,380 vs 50,000）正合此约定。正文合计兜底。
    """
    out = {"contract_number": None, "party_a": None, "party_b": None, "total_amount": None}
    base = re.sub(r"\.(pdf|jpg|jpeg|png)$", "", (filename or "").strip(), flags=re.I)
    m = re.match(r"^[^0-9A-Za-z]{0,6}([A-Z]{2,4}\d{6,})", base.upper())
    if m:
        out["contract_number"] = m.group(1)
    for key, rx in _PARTY.items():
        mm = rx.search(text or "")
        if mm:
            # 必须像机构名。**实测坑**：两栏表头 `需方（甲方） | 供方（乙方）` 会让甲方正则
            # 抓到**隔壁列的标签**「供方（乙方）」。取错比不取更糟 → 复用 _clean_org 的机构词校验
            # （对当前 98 份 CTL 全量回放，仅此 1 例受影响，其余 93 例取值不变）。
            out[key] = _clean_org(mm.group(1))
    # ⚠️ 匹配文件名金额前**先把合同号去掉**：文件名形如 `YOE2024051711.pdf`（不含金额）时，
    # `_FILE_AMOUNT` 会把**合同号本身**当成金额（实测 `total_amount=2024051711.0`，
    # 而明细合计仅 272,000 —— 2026-09-15 补提取时发现）。去掉编号后无数字 → 正确落到正文兜底。
    _amt_src = filename or ""
    if out.get("contract_number"):
        _amt_src = _amt_src.replace(out["contract_number"], "", 1)
    m = _FILE_AMOUNT.search(_amt_src)
    if m:
        try:
            out["total_amount"] = round(float(m.group(1).replace(",", "").replace("，", "")), 2)
        except ValueError:
            pass
    if out["total_amount"] is None:
        mt = _TEXT_TOTAL_DECL.search(text or "") or _TEXT_TOTAL_ROW.search(text or "")
        if mt:
            try:
                out["total_amount"] = round(float(mt.group(1).replace(",", "").replace("，", "")), 2)
            except ValueError:
                pass
    return out


def product_key_of(record: dict) -> str:
    """记录的**产品键**：有「服务类别」列时用类别；无类别列时退回服务名。

    必须与 `sync_contract_service_items` 写入 `contract_items.product_raw` 的口径一致：
    检索层用 `_cat_of(product_raw)` 得到 product_key，再拿它来这里过滤明细。
    两边不一致（例：product_raw 取到服务名、这里却看 category=None）会导致
    **明细被全部过滤掉、命中恒为空**——实测合同无「服务类别」列时正是如此。
    """
    return (record.get("category") or record.get("service_name") or "")


# ============================================================================
# 合同日期提取（两档精度，**精度由字符串长度自明**，不新增字段）
# ============================================================================
# 实测（184 份 OCR 合同）：
#   精确签署日（签署页）   37/184 = 20%   →  `YYYY-MM-DD`
#   合同编号内嵌年月       178/184 = 97%  →  `YYYY-MM`
# 为什么不能只做精确签署日：覆盖率太低，撑不起日期检索。
# 为什么可以用编号年月：编号形如 `YOE` + YYYYMM + 序号；与 34 份可比对样本对照，
#   同月 15 份、签署晚 1–3 月 12 份（编号先分配、签约稍后，符合业务），反常 7 份
#   （其中 4 份是 OCR 把 2025 误读成 2015，已被年份合理性校验挡下）。
# **性质差异必须让调用方看得见**：编号年月是「订单/报价月」，不是签署日。
# 故以字符串长度区分精确度：`YYYY-MM-DD` = 签署日；`YYYY-MM` = 编号年月。

_SIGN_LABEL = re.compile(r"(?:签订|签署|签约|签字)\s*(?:日期|时间)\s*[:：]?\s*"
                         r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})?\s*日?")
_SIGN_BARE = re.compile(r"日\s*期\s*[:：]\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})?\s*日?")
_SIGN_BY = re.compile(r"于\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[（(]\s*[“\"]?\s*签署日")
_DATE_ONLY_LINE = re.compile(r"^\s*[（(]?\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})?\s*日?\s*[)）]?\s*$")

# —— 2026-09-13 新增：**点/横线分隔**的签署日 ——
# 实测根因（不是模式漏了，是**格式漏了**）：扫描件 OCR 把签署页日期渲染成 `2024.9.20`、
# `2023.5.18`，而上面三个模式只认「YYYY年M月D日」。于是 98 份 CTL 合同里 79 份退化成
# 「合同编号年月」代理值（与真实签署月一致率仅约 53%）。
# 原文实测（`YOE2024090883`）：`签订日期：2024.9.20` / `签订日期：2024.9.10`；
# （`YOE2023040198`）：`日 期：2023.5.18`。**日期一直就在正文里，只是没被读出来。**
_DOT = r"\s*[.\-/．]\s*"
_SIGN_LABEL_DOT = re.compile(r"(?:签订|签署|签约|签字)\s*(?:日期|时间)\s*[:：]?\s*"
                             r"(20\d{2})" + _DOT + r"(\d{1,2})" + _DOT + r"(\d{1,2})")
_SIGN_BARE_DOT = re.compile(r"日\s*期\s*[:：]\s*(20\d{2})" + _DOT + r"(\d{1,2})" + _DOT + r"(\d{1,2})")
_DATE_ONLY_LINE_DOT = re.compile(r"^\s*[（(]?\s*(20\d{2})" + _DOT + r"(\d{1,2})" + _DOT
                                 + r"(\d{1,2})\s*[)）]?\s*$")
_SIGN_NEAR = ("公章", "签名", "盖章", "甲方", "乙方", "委托方", "受托方", "采购人", "供应商")
_CONTRACT_ID = re.compile(r"^([A-Z]{2,4})(20\d{2})(\d{2})(\d{2,4})")
_YEAR_MIN, _YEAR_MAX = 2015, 2030


_CLIENT_LIKE = re.compile(r"(公司|大学|医院|研究院|研究所|学院|中心|实验室|疾控|部队|集团|学校|保健院|卫生院|科学院|测试中心)")
_AMOUNT_LIKE = re.compile(r"^[\d,，.\s()（）万元个样次例]+$")
# 产品名必须含领域词。**否则会把人名/客户名当产品**——实测两例：
#   `ZOE2024032225浙江省肿瘤医院…-袁莉-潘利斌-LC-MS-MS全谱代谢组检测-…` → 误取「袁莉」
#   `ZOE2023081140-暨南大学-罗钧洪-416,000.00.pdf`              → 误取「暨南大学」
# 取错产品键比不取更糟：用户会看到"产品＝袁莉"。宁可返回 None 退回服务名分组。
_PRODUCT_LIKE = re.compile(
    r"(测序|检测|分析|组学|代谢|蛋白|单细胞|基因组|转录|质谱|芯片|空间|微生物|细胞|"
    r"标记|鉴定|定量|[Pp]anel|测序服务|技术服务|Olink|DIA|SNP|ATAC|Xenium|Visium|Mobi)")


def _looks_like_product(seg: str) -> bool:
    """该段是否像**产品**（而非客户名/人名）。"""
    return bool(seg) and bool(_PRODUCT_LIKE.search(seg)) and not _CLIENT_LIKE.search(seg)


# ============================================================================
# 合同身份字段（编号/甲方/乙方/合同总额）—— 计划 §2 要求合同场景返回
# ============================================================================
# 实测覆盖率（184 份 OCR 合同）：
#   合同编号  文件名首段 **97%**  > 正文「合同号：」67%   → 优先用文件名
#   甲方      正文「甲方： | X」69%
#   乙方      正文「乙方： | X」65%
#   合同总额  「合同总金额为：人民币X元」50% + 表格行「合同总金额（元） | X」49% → 两式合并
# **必须防的坑**（实测）：`下方式支付给乙方： 1.4.1分首尾款2期支付` 是付款条款，
# 会被「乙方」正则误抓成公司名——故要求取值含机构词（公司/大学/医院/研究院…）。
_ORG_KW = ("公司", "大学", "医院", "研究院", "研究所", "学院", "中心", "集团",
           "部队", "学校", "局", "委员会", "保健院", "卫生院", "科学院", "实验室",
           # 2026-09-11 补：常见主体类型，缺了会把**取值确凿**的甲方判成误抓而丢弃
           # （对抗性复核实测：`北京陈菊梅公益基金会` 被判 None，语料里该行确实存在）
           "基金会", "协会", "学会", "红十字会", "事务所", "公益", "分院", "分校")
# 2026-09-14 清理：删掉 `_PARTY_A` / `_PARTY_B` / `_CN_NUM` / `_AMOUNT_DECL` / `_AMOUNT_ROW`
# / `_AMOUNT_BAD` —— 它们**唯一**的使用者是已删的 `extract_contract_identity`。
# 其中 `_CN_NUM` 早在本次清理前就已无任何使用者（定义后从未被引用）。
# `_ORG_KW` 与 `_clean_org` **保留**：它们被 live 路径用（`extract_contract_header` 等）。


def _clean_org(v: str | None) -> str | None:
    """清洗机构名：去空白/首尾标点；**必须含机构词**；**必须是单位名而不是栏目标签**。

    ⚠️ 两栏表头（`需方（甲方） | 招标代理公司（乙方）`）会让甲方正则抓到**隔壁列的标签**。
    只靠「必须含机构词」挡不住 —— `招标代理公司（乙方）` 是含「公司」的
    （对抗性复核实测；真实布局 `需方（甲方） | 供方（乙方）` 恰好因不含机构词而被挡住，属侥幸）。
    故显式拒绝把**甲乙方栏目标签**当成单位名。
    """
    if not v:
        return None
    s = re.sub(r"[\s　]+", "", v).strip("：:|-—·")
    s = re.sub(r"[（(](供货|服务方|盖章|签字)[^）)]*[）)]$", "", s).strip()
    if len(s) < 2 or len(s) > 40:
        return None
    if re.search(r"[（(](甲方|乙方|需方|供方|采购人|供应商|委托方|受托方)[）)]", s):
        return None                      # 是栏目标签，不是单位名
    return s if any(k in s for k in _ORG_KW) else None


def product_from_filename(filename: str) -> str | None:
    """从合同文件名取**产品名**：`{编号}-{产品}-{客户}-{金额}.pdf`。

    用途：合同表**没有「服务类别」列**时，用它当产品键。
    否则每个服务名会各自变成一个"产品"，同一份合同被打散成 N 条结果、
    每条只显示一个服务项的金额——实测 `ZOE2025073338-空间代谢组-…-352000.00.pdf`
    被拆成 9 条（4,000 / 8,000 / 261,200 …），而 9 条合计正是合同总额 352,000。

    解析要点（均由实测文件名倒推）：
      - 首段可能被污染（`·YOE…`、`大金额 ZOE…`）→ 先剥掉开头的非字母数字。
      - 产品名自身可能含 `-`（`LC-MS-MS脂质代谢组检测`），按 `-` 取第 2 段会得到 "LC"。
        故：第 2 段含中文就直接用；否则**累加后续段**，直到遇到客户名或金额段为止。
    返回 None 表示无法判定（不猜）。
    """
    base = re.sub(r"\.(pdf|jpg|jpeg|png)$", "", (filename or "").strip(), flags=re.I)
    base = re.sub(r"^[^0-9A-Za-z一-龥]+", "", base)       # 剥开头污染（· 等）
    base = re.sub(r"^[^0-9A-Za-z]{0,6}(?=[A-Z]{2,4}\d{6,})", "", base)  # 剥 "大金额 " 之类前缀
    parts = [p.strip() for p in re.split(r"[-—–]", base) if p.strip()]
    if len(parts) < 2 or not re.match(r"^[A-Z]{2,4}\d{6,}", parts[0].upper()):
        return None
    seg = parts[1]
    if _looks_like_product(seg):
        return seg[:60]
    acc = [seg]
    for p in parts[2:]:
        if _CLIENT_LIKE.search(p) or _AMOUNT_LIKE.match(p):
            break
        acc.append(p)
        if _looks_like_product(p):
            break
    joined = "-".join(acc).strip("-")
    return joined[:60] if _looks_like_product(joined) else None


def _id_year_month(filename: str) -> str | None:
    """从合同编号取 `YYYY-MM`（订单/报价年月）。取不到返回 None。"""
    m = _CONTRACT_ID.match(re.sub(r"\.(pdf|jpg|jpeg|png)$", "", (filename or "").strip().upper(),
                                  flags=re.I))
    if not m:
        return None
    y, mo = int(m.group(2)), int(m.group(3))
    return f"{y:04d}-{mo:02d}" if (_YEAR_MIN <= y <= _YEAR_MAX and 1 <= mo <= 12) else None


def _pick_best_date(old: str | None, new: str | None, id_ym: str | None) -> str | None:
    """日期合并规则（**先剔 OCR 误读，再比精度**）。

    1) 年份与合同编号年份相差 >1 的日期判为 OCR 误读（实测 2025 被读成 2015）→ 另一边优先；
    2) 都正常时「精度只升不降」：精确签署日（`YYYY-MM-DD`）不被编号年月（`YYYY-MM`）覆盖。

    ⚠️ 顺序很关键：原实现**只有第 2 条**，于是错的长日期一旦写入就再也无法被正确的
    `YYYY-MM` 取代 —— 库内实证 `ZOE2025103717` 的 contract_date 长期是 `2015-11-25`。
    """
    iy = int(id_ym[:4]) if id_ym and id_ym[:4].isdigit() else None

    def _bad(d: str | None) -> bool:
        if not d or iy is None or not d[:4].isdigit():
            return False
        return abs(int(d[:4]) - iy) > 1

    if old and new:
        if _bad(old) != _bad(new):
            return new if _bad(old) else old
        return old if len(old) > len(new) else new
    return old or new


def extract_contract_date(text: str, filename: str = "") -> tuple[str | None, str]:
    """返回 (日期字符串, 来源)。日期字符串长度即精度：`YYYY-MM-DD`=签署日 / `YYYY-MM`=编号年月。

    来源：`signature_page`（签署页精确日）/ `contract_id`（编号年月）/ `none`。
    规则优先级：签署页 > 编号年月——有精确的就不用月级近似。

    **年份合理性两道关**：
      ① 落在 `_YEAR_MIN.._YEAR_MAX`（2015–2030）；
      ② 与**合同编号内嵌年份**相差 ≤1 年。只靠 ① 挡不住实测的 OCR 误读（2025→2015 正好在区间内），
         必须用编号年份交叉校验 —— 这正是该函数 docstring 原先声称能挡、实际挡不住的那一类。
    """
    id_ym = _id_year_month(filename)
    id_year = int(id_ym[:4]) if id_ym else None

    def _ok(y: int) -> bool:
        return (_YEAR_MIN <= y <= _YEAR_MAX
                and (id_year is None or abs(y - id_year) <= 1))

    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    found: list[str] = []
    for i, ln in enumerate(lines):
        if len(ln) > 120:
            continue
        for rx in (_SIGN_BY, _SIGN_LABEL, _SIGN_BARE, _SIGN_LABEL_DOT, _SIGN_BARE_DOT):
            m = rx.search(ln)
            if m and _ok(int(m.group(1))):
                y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
                found.append(f"{y:04d}-{mo:02d}-{int(d):02d}" if d else f"{y:04d}-{mo:02d}")
        m = _DATE_ONLY_LINE.match(ln) or _DATE_ONLY_LINE_DOT.match(ln)  # 整行即日期，需邻近签署块
        if m and _ok(int(m.group(1))):
            near = " ".join(lines[max(0, i - 4): i + 4])
            if any(k in near for k in _SIGN_NEAR):
                y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
                found.append(f"{y:04d}-{mo:02d}-{int(d):02d}" if d else f"{y:04d}-{mo:02d}")
    if found:
        # ⚠️ 一份合同常有**两个签署日期**（甲方一个、乙方一个，实测 `ZOE2024040057`：
        # `日 期：2024年4月8日` 与 `日 期：2024.04.03`）。合同自**较晚**签署日生效，
        # 故取最晚者；原实现是「从文末往前找第一个命中」，取到的是**文档顺序靠后**的那个，
        # 与「生效日」不是一回事（同一份合同原本会报 04-03，改为报 04-08）。
        got = sorted(found)[-1]
        return got, "signature_page"
    return (id_ym, "contract_id") if id_ym else (None, "none")


def aggregate_product_amount(records: list[dict], product_key: str | None = None) -> float | None:
    """D9 产品金额聚合：同一产品下 detail 行 line_amount 求和（不含小计/总计/合同总额）。

    返回部分和（存在未知金额行时只计已知部分）。调用方在做精确阈值评估前，
    必须先查 has_unknown_amount(records, product_key) —— 未知金额存在时不得把部分和当完整产品金额。
    """
    total = 0.0
    any_val = False
    for r in records:
        if r["row_type"] != "detail":
            continue
        if product_key is not None and product_key not in product_key_of(r):
            continue
        amt = r.get("line_amount")
        if amt is None:
            continue
        total += amt
        any_val = True
    return round(total, 2) if any_val else None


def has_unknown_amount(records: list[dict], product_key: str | None = None) -> bool:
    """同产品是否含 line_amount 未知的 detail 行（部分和不可当作完整产品金额）。"""
    for r in records:
        if r["row_type"] != "detail":
            continue
        if product_key is not None and product_key not in product_key_of(r):
            continue
        if r.get("line_amount") is None:
            return True
    return False


def product_amount_status(records: list[dict], product_key: str | None = None) -> dict:
    """该产品金额状态：detail 求和 vs 小计声明 vs 未知/冲突。

    返回 {detail_sum, subtotal_declared, has_unknown, conflict}
    - detail_sum          = 该产品 detail 行 line_amount 求和（存在未知/无 detail 时为 None/仅已知部分）
    - subtotal_declared   = 该产品明确「合计/总计」行声明的金额（无则 None）
    - has_unknown         = 该产品是否有金额缺失的 detail 行
    - conflict            = detail_sum 与 subtotal_declared 均存在且不一致（>0.01）
    注意：conflict=True 仅表示「原文字段不一致、权威金额待核」，绝不裁定哪一侧错误；
          未知金额或缺行也可能造成差异，具体须回原文核验。
    调用方在精确阈值评估时必须先查 has_unknown / conflict —— 任一为真则该产品不参与精确筛选。
    """
    detail_sum = 0.0
    any_detail = False
    unknown = False
    subtotal_declared = None
    for r in records:
        if product_key is not None and product_key not in product_key_of(r):
            continue
        if r["row_type"] == "detail":
            if r.get("line_amount") is None:
                unknown = True
                continue
            detail_sum += r["line_amount"]
            any_detail = True
        elif r["row_type"] == "product_subtotal" and r.get("line_amount") is not None:
            subtotal_declared = r["line_amount"]
    detail_sum = round(detail_sum, 2) if any_detail else (None if not unknown else None)
    conflict = False
    if any_detail and subtotal_declared is not None and not unknown:
        conflict = abs(detail_sum - subtotal_declared) > 0.01
    return {"detail_sum": detail_sum, "subtotal_declared": subtotal_declared,
            "has_unknown": unknown, "conflict": conflict}


CONTRACT_PREFIX = "CTL-"


def sync_contract_service_items(con, source_doc_id: str, records: list[dict],
                                source_sha256: str | None = None,
                                parser_version: str | None = None,
                                native_text: str | None = None,
                                filename: str = "") -> dict:
    """同一事务内，把 [source_doc_id] 合同的服务明细快照替换为目标状态。

    - 父合同行 contract_id = CTL-{doc_id}（ordinal=1），total_amount = 合同总金额行金额（无→None），
      evidence_text = 明细表头+合同总金额行。
    - contract_items：同一事务内先删后插（确定性、幂等重放），每条保留 row_text/位置。
    - 明细必须属于同一份合同（同 contract_id）。
    - 来源指纹：仅当本次同步成功提交（事务结束）时，才把 source_sha256 / parser_version
      写入 contracts（该父行）；异常抛出则整个事务回滚，旧指纹保持不变。
      禁止用当前 canonical 给未重新提取的旧记录补标签。
    返回 {'contract': 'upserted'|'kept', 'items': 写入行数, 'contract_total': ...}
    """
    if not source_doc_id:
        raise ValueError("source_doc_id 必填")
    contract_id = f"{CONTRACT_PREFIX}{source_doc_id}"
    # 合同日期：函数内自己算，避免调用方漏掉。
    # 精度由字符串长度自明（YYYY-MM-DD 签署日 / YYYY-MM 编号年月），见 extract_contract_date。
    contract_date = extract_contract_date(native_text, filename)[0] if (native_text or filename) else None
    # 无「服务类别」列的合同：用**文件名里的产品**当产品键，否则每条服务明细
    # 会各自成为一个"产品"，同一份合同被打散成 N 条结果（见 product_from_filename）。
    file_product = product_from_filename(filename) if filename else None
    if file_product:
        for r in records:
            if not r.get("category"):
                r["category"] = file_product
    # 合同头：编号/甲乙方/总额（文件名优先），与明细同步写入
    hdr = contract_header_facts(native_text or "", filename)
    contract_total = hdr["total_amount"]
    if contract_total is None:
        for r in records:
            if r.get("row_type") == "contract_total" and r.get("line_amount") is not None:
                contract_total = r["line_amount"]
                break
    header = ""
    for r in records:
        if r.get("row_text") and "服务名称" in r["row_text"] and "数量" in r["row_text"]:
            header = r["row_text"]
            break
    evid = f"[合同服务明细] {header} | 合同总金额={contract_total}" if contract_total else f"[合同服务明细] {header} | 合同总金额=未知"
    with con:
        exist = con.execute("SELECT contract_number, party_a, party_b, contract_date, total_amount "
                            "FROM contracts WHERE contract_id=?", (contract_id,)).fetchone()
        if exist:
            old_num, old_a, old_b, old_date, old_total = \
                exist[0], exist[1], exist[2], exist[3], exist[4]
            # 日期合并：先剔 OCR 误读（年份与编号不符），再按精度「只升不降」。见 _pick_best_date。
            best_date = _pick_best_date(old_date, contract_date, _id_year_month(filename))
            # 快照 vs 保留（合同头字段）——**按字段的真实来源分别判**：
            #   调用方**给了**该字段的来源 → 快照语义，取不到即 None（宁缺毋滥）——
            #     否则像「供方（乙方）」这种误抓**永远无法被修正**（旧写法 COALESCE 的缺陷）。
            #   调用方**没给**来源 → 保留原值，不得静默清空。
            # ⚠️ 曾经把「有上下文」笼统定义为 `not (native_text or filename)`，两处都错：
            #   ① 甲乙方**只可能来自正文** —— 只给 filename 也会把已提取的甲乙方写成 NULL
            #      （对抗性复核实测：party_a/party_b 由「浙江大学/上海欧易…」→ None，
            #       而同一次调用里 contract_number 却按文件名正常写入）；
            #   ② `total_amount` **没进快照**，无上下文调用时被静默清成 NULL，与 docstring
            #      声称的「与原 COALESCE 等价、无行为回退」不符（对抗性复核实测）。
            keep_num = not (native_text or filename)
            keep_party = not native_text          # 甲乙方只来自正文
            # 合同总额的来源：文件名金额 / 正文声明（需上下文）或明细表「合同总金额」行（只需 records）。
            # 有值就写；为 None 且无上下文时才保留旧值。
            keep_total = contract_total is None and not (native_text or filename)

            def _snap(new, cur, keep):
                return cur if keep else new

            con.execute("UPDATE contracts SET total_amount=?, evidence_text=?, source_sha256=?, "
                        "parser_version=?, contract_date=?, contract_number=?, party_a=?, party_b=? "
                        "WHERE contract_id=?",
                        (_snap(contract_total, old_total, keep_total), evid, source_sha256,
                         parser_version, best_date,
                         _snap(hdr["contract_number"], old_num, keep_num),
                         _snap(hdr["party_a"], old_a, keep_party),
                         _snap(hdr["party_b"], old_b, keep_party), contract_id))
            cstate = "kept"
        else:
            con.execute(
                "INSERT INTO contracts (contract_id, document_id, ordinal, contract_number,"
                " party_a, party_b, contract_vendor, vendor_scope, vendor_evidence,"
                " contract_date, total_amount, evidence_text, source_sha256, parser_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (contract_id, source_doc_id, 1, hdr["contract_number"],
                 hdr["party_a"], hdr["party_b"], None, None, None,
                 contract_date, contract_total, evid, source_sha256, parser_version))
            cstate = "upserted"
        # 明细：全量替换（同一事务，先删后插 → 幂等）
        con.execute("DELETE FROM contract_items WHERE contract_id=?", (contract_id,))
        n = 0
        seen = set()
        for r in records:
            if r["row_type"] not in ("detail", "product_subtotal"):
                continue
            seq = r.get("seq")
            # item_id 确定性：seq 唯一；无 seq 时用递增 index；同 seq 重复行通过 index 后缀去重
            base = f"{contract_id}-{seq}" if seq is not None else f"{contract_id}-i{n}"
            item_id = base
            k = 2
            while item_id in seen:
                item_id = f"{base}-r{k}"
                k += 1
            seen.add(item_id)
            con.execute(
                "INSERT INTO contract_items (item_id, contract_id, product_raw, product_canonical,"
                " quantity, unit_price, line_amount, row_type, product_amount_source, evidence_text)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (item_id, contract_id,
                 # 有类别列时用「类别/服务名」（_cat_of 取类别）；无类别列时退回服务名本身，
                 # 否则 product_raw 会以 "/" 开头，_cat_of 取到空串、产品匹配全部失效。
                 ((r.get("category") + "/") if r.get("category") else "") + (r.get("service_name") or ""),
                 None, r.get("quantity"), r.get("unit_price"), r.get("line_amount"),
                 r["row_type"],
                 # 来源**可审计**：调用方可显式指定（如 `filename_product_total` ——
                 # 单产品、无明细表合同的**文件名归因**，见 `PLAN-20260915` R1-1.b′ / §8-3），
                 # 缺省仍按 row_type 推导 `declared` / `explicit_product_subtotal`。
                 r.get("product_amount_source")
                 or ("declared" if r["row_type"] == "detail" else "explicit_product_subtotal"),
                 r.get("row_text") or ""))
            n += 1
    total = contract_total
    return {"contract": cstate, "contract_id": contract_id, "items": n, "contract_total": total}

# ============================================================================
# R5 方案章节提取（D11：结构边界与业务语义正交双层）
# ============================================================================
# 边界来源优先级（写进 ES 的 boundary_source 字段，如实标注，不混同）：
#   word_outline —— Word 段落大纲级别（最可靠，R5-02 要求"优先使用 Word 标题"）
#   text_pattern —— 无大纲时的文本模式回退（实测假阳性高，故从严）
#   none         —— 两种都取不到，该文档不产出章节
#
# structural_role 判定（目录/页眉/封面强排除，不进方案索引）：
#   toc_entry / header_footer / cover  → 排除
#   content_section / appendix         → 保留
#   table_caption                      → 保留（表题也算方案内容的一部分）
#
# section_type：计划要求"九类业务语义"，但九类在全部权威文档中**从未列举**。
#   按 §5.5"unclassified 仍进通用 ES"，此处一律置 unclassified，
#   classification_source='none'；待九类定义确定后接词典即可，不需改结构。
# ============================================================================

_SCHEME_KW = (
    "售后", "服务", "培训", "技术方案", "实施方案", "方案", "承诺", "质保",
    "保障", "进度", "组织", "团队", "人员", "应急", "验收", "质量", "保密",
)

# 文本模式回退：只取高置信形态（实测 1.1.1 与 6-1 假阳性最低）
_TEXT_HEADING = (
    re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){1,3})\s*[、.．]?\s*(\S.{0,60})$"),   # 6.1.2 xxx
    re.compile(r"^\s*(\d{1,2}-\d{1,2}(?:-\d{1,2})?)\s+(\S.{0,60})$"),          # 6-1 xxx
    re.compile(r"^\s*第\s*([一二三四五六七八九十百\d]{1,3})\s*[章节]\s*[、.．]?\s*(\S.{0,60})$"),
)
_TOC_LINE = re.compile(r"[.·…]{4,}\s*\d{1,3}\s*$")
# 目录条目的另一种形态：标题后跟页码/页范围（"（五）培训方案 | 182-194页"）
_TOC_PAGEREF = re.compile(r"[|\s]\s*\d{1,4}(?:\s*[-–~]\s*\d{1,4})?\s*页\s*$")
_CAPTION = re.compile(r"^\s*(表|图)\s*\d")
_APPENDIX = re.compile(r"^\s*(附件|附录|附表|附\s*\d)")
_COVER_KW = ("项目名称", "采购编号", "项目编号", "供应商名称", "投标人名称",
             "（正本）", "(正本)", "（副本）", "法定代表人", "投标日期", "响应文件",
             "投标文件", "采购单位", "供应商：", "投标人：")
_TOC_TITLE = re.compile(r"^\s*(目\s*录|contents)\s*$", re.IGNORECASE)
# 目录页的标题不一定恰好是"目录"——常见"投标文件目录""…页码索引表"
_TOC_TITLE_LOOSE = re.compile(r"(目\s*录|页码索引|页码对照|索引表)\s*$")

# —— 标题合理性过滤 ——
# 实测：文档作者常把正文段落误设为 Heading 样式（人员证书表格行、长句、承诺函正文），
# 这些"伪标题"会成为检索噪声的主要来源（证书表格行曾排到售后查询 Top3）。
# 判据取保守形态，宁可漏掉个别真标题，也不让长句和表格行进索引。
_MAX_HEADING_CHARS = 40
_SENTENCE_TAIL = re.compile(r"[。；！？!?；]\s*$")
_SENTENCE_MID = re.compile(r"[。；]")          # 句中出现句号/分号 → 多半是正文
_CERT_ROW = re.compile(r"(学位证书|身份证|职称|执业资格|毕业证|联系方式\s*\|)")

# —— 结构层噪声（用户决定：按 structural_role 一律排除出方案检索，复用既有枚举故记为 unknown）——
_PURE_DATE = re.compile(r"^\s*[（(]?\s*(?:20)?\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?\s*[)）]?\s*$")
_FORM_RESIDUE = re.compile(r"^\s*(备注|说明|序号|注|填写说明|注意事项|无|有|是|否)\s*[：:]?\s*$")
_SIGN_LINE = re.compile(r"(签字|签名|盖章|（盖|\(盖|法定代表人（单位负责人）或其委托代理人|日\s*期\s*[:：])")
# 人员凭证行："姓名 | 职务 | 学位证书 | 年份 | 电话 |" 或 "岗位-姓名"
# 注意排除形近的**章节标题**："项目负责人简历""人员配置方案"是方案内容，不是人员行
_NAME_TITLE_ROW = re.compile(
    r"^[一-龥]{2,4}\s*[|｜]"
    r"|[一-龥]{2,6}(?:主管|经理|负责人|专员|签字人)[-—\s]*[一-龥]{2,4}\s*$")
_SECTION_WORD_GUARD = re.compile(r"(简历|职责|配置|方案|一览|情况|团队|人员|制度|流程|说明)")
# 业绩表金额列（"… | 43.875万元 | 2023年7月14日 |"）与买方列
_PERF_AMOUNT = re.compile(r"\d+(?:\.\d+)?\s*万元")
_PERF_BUYER = re.compile(r"(大学|学院|研究院|研究所|医院|中心|公司|部队|疾控|预防控制)")
# 图题（"图 2.1 …"，与表题 _CAPTION 分开以便分别统计）
_FIG_CAPTION = re.compile(r"^\s*图\s*\d")


def is_plausible_heading(text: str) -> bool:
    """过滤伪标题：表格行、长句、正文段落。"""
    s = (text or "").strip()
    if not s:
        return False
    if "|" in s or "\t" in s:                     # 表格行（"姓名 | 职务 | …"）
        return False
    if len(s) > _MAX_HEADING_CHARS:               # 标题不会这么长
        return False
    if _SENTENCE_TAIL.search(s):                  # 以句号/分号收尾 = 句子
        return False
    if _SENTENCE_MID.search(s) and len(s) > 20:   # 20 字以上还带句读 = 正文
        return False
    if _CERT_ROW.search(s):                       # 证书/人员信息行
        return False
    return True


def _headings_from_outline(outline: list[dict]) -> list[dict]:
    return [{"line": int(o["line"]), "level": int(o["level"])} for o in outline
            if o.get("line") and o.get("level")]


def _headings_from_text(lines: list[str]) -> list[dict]:
    """文本模式回退。从严：排除表格行（含 |）、目录点线、纯数字行。"""
    found = []
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s or len(s) > 70 or "|" in s or _TOC_LINE.search(s):
            continue
        for pat in _TEXT_HEADING:
            m = pat.match(s)
            if m:
                num = m.group(1)
                level = num.count(".") + num.count("-") + 1
                found.append({"line": i, "level": min(level, 4)})
                break
    return found


# 标题编号前缀（用于在模式匹配前剥离；实测带编号的标题会让 ^ 锚定的规则全部失效，
# 例如 "10.4 产品经理 刘进"、"3、大客户部项目经理-刘进"、"6.1.13一种基于…方法"）
_HEADING_NUMBER = re.compile(
    r"^\s*(?:\d{1,2}(?:[.\-]\d{1,2}){0,4}\s*[、.．]?\s*"
    r"|[（(]\s*[一二三四五六七八九十\d]{1,3}\s*[)）]\s*"
    r"|[一二三四五六七八九十]{1,3}\s*[、.．]\s*"
    r"|第\s*[一二三四五六七八九十百\d]{1,3}\s*[章节步部分]+\s*[:：]?\s*)")


def strip_heading_number(text: str) -> str:
    """剥离标题编号前缀，供模式匹配使用（不改变持久化的 heading 原文）。"""
    s = (text or "").strip()
    stripped = _HEADING_NUMBER.sub("", s, count=1).strip()
    return stripped or s


# 噪声表格的内容特征（**不按"是不是表格"判，按表格是什么判**）。
# 实测教训：技术参数表（序号|检测项目|平台|技术要求）与基因表达结果表（gene id|Fold Change|p-value）
# 都是方案核心内容，一律按表格排除会把技术方案主体全删掉。
_NOISE_TABLE_HEADER = re.compile(
    r"(买方名称|合同价格|合同签订日期|合同金额|申报人名称|控股股东|管理关系|"
    r"总报价|投标声明|身份证号|法定代表人\s*/\s*单位负责人|被管理关系|"
    r"采购人名称.*项目名称.*金额|申报清单)")

# 噪声章节标题（真值为 unknown）：报价/开标一览表、表单字段、论文与专利清单
_NOISE_HEADING = re.compile(
    r"(开标.{0,4}一览表|报价一览表|首轮报价|投标一览表|"
    r"项目编号\s*[:：∶]|采购编号\s*[:：]|招标编号\s*[:：]|"
    r"相关文章|署名.{0,4}文章|发表.{0,4}(论文|文章)|文章清单|"
    r"^一种.{0,40}(方法|系统|设备|试剂盒|介质)\s*$|"
    r"专利清单|软著|著作权清单)")


def _looks_like_heading_cover(text: str) -> bool:
    """标题**自身**是否像封面要素（不再按"出现在前 40 行"整片划为封面）。

    实测教训（两轮）：
      - 按行号划封面区 → 把紧接封面的 投标函/授权委托书 全误判为 cover（该批 19 条 cover 真值为 0）。
      - 即使只认标题自身，也不能把"投标函/响应函"当封面——它们是有实质内容的独立章节。
        只保留明确的封面标记（正本/副本/封面/标段号/投标日期）。
    """
    return bool(re.search(r"(正本|副本|封面|标段号|投标日期)", text))


def _noise_table_starts(lines: list[str], min_run: int = 2) -> list[int]:
    """识别**噪声表格块**（业绩表 / 资质表 / 报价表）首行号，1-based。

    必要性：这类表的数据行不含合法标题特征，会被"标题合理性过滤"移出边界列表，
    那些行就**回落成上一节正文**——整张业绩表随 `content_section` 混进索引。
    只对**表头命中噪声特征**的表格块生效；技术参数表、分析结果表不受影响。
    """
    starts, run = [], 0
    for i, ln in enumerate(lines, 1):
        if "|" in ln and ln.strip():
            run += 1
            if run == min_run:
                head = i - run + 1
                # 以块首两行判定表头特征
                probe = " ".join(lines[head - 1:head + 1])
                if _NOISE_TABLE_HEADER.search(probe):
                    starts.append(head)
        else:
            run = 0
    return starts


def _looks_like_cover(lines: list[str], end_line: int) -> bool:
    """正文开头到第一个标题之间，像不像封面（项目名称/编号/供应商/日期堆叠）。"""
    head = [ln.strip() for ln in lines[:min(end_line, 40)] if ln.strip()]
    if not head:
        return False
    hits = sum(1 for ln in head if any(k in ln for k in _COVER_KW))
    return hits >= 3


def _classify_structural_role(heading: str, body: list[str], line_no: int,
                              cover_end: int, repeats: frozenset | set = frozenset()) -> str:
    """结构角色判定——**复用计划 §5.5 既有枚举，不新增字段**。

    检索规则（用户决定）：**只有 `content_section` 进入方案检索**；
    目录、图题、表单、签字日期、证书与业绩行一律按结构角色排除。

    实测修订（真值集评测后）：
      - 模式匹配前先**剥离标题编号**——带编号的标题会让 ^ 锚定规则全部失效。
      - `cover` 不再按"前 40 行"整片划定（实测该做法精度为 0，把投标函/授权委托书全误判），
        改为只认标题自身的封面要素。
    """
    h = (heading or "").strip()
    c = strip_heading_number(h)          # 供模式匹配的"净标题"
    if _TOC_TITLE.match(c) or _TOC_TITLE_LOOSE.search(c) or _TOC_PAGEREF.search(h):
        return "toc_entry"
    if _CAPTION.match(c) or _FIG_CAPTION.match(c):
        return "table_caption"
    if _looks_like_heading_cover(c):
        return "cover"
    if h in repeats:
        return "header_footer"
    # —— 表单残留 / 日期 / 签字盖章 / 报价一览表 / 论文专利清单 ——
    if _NOISE_HEADING.search(c):
        return "unknown"
    if _PURE_DATE.match(c) or _FORM_RESIDUE.match(c) or _SIGN_LINE.search(c):
        return "unknown"
    # —— 证书与人员凭证行（"项目负责人简历"这类章节标题不在此列）——
    if _CERT_ROW.search(c) or (_NAME_TITLE_ROW.match(c) and not _SECTION_WORD_GUARD.search(c)):
        return "unknown"
    # —— 业绩表行：标题含业绩特征，或**正文**是业绩表数据行（金额+买方+日期）——
    if _PERF_AMOUNT.search(c) or h.count("|") >= 2:
        return "unknown"
    body_probe = " ".join(body[:4])
    if _PERF_AMOUNT.search(body_probe) and _PERF_BUYER.search(body_probe):
        return "unknown"
    if _APPENDIX.match(c):
        return "appendix"
    # 正文以目录点线/page number 为主 → 目录条目
    sampled = [ln for ln in body[:12] if ln.strip()]
    if sampled and sum(1 for ln in sampled if _TOC_LINE.search(ln)) >= max(2, len(sampled) // 2):
        return "toc_entry"
    return "content_section"


# 只有这些角色进入方案检索（用户决定：仅 content_section）
SCHEME_INDEX_ROLES = ("content_section",)


def _repeated_lines(lines: list[str], min_repeat: int = 5, max_len: int = 40) -> set[str]:
    """重复出现的短行（页眉/页脚）。"""
    from collections import Counter
    counts: Counter = Counter()
    for raw in lines:
        s = raw.strip()
        if 0 < len(s) <= max_len:
            counts[s] += 1
    return {s for s, n in counts.items() if n >= min_repeat}


def extract_scheme_sections(text: str, document_id: str, project_key: str = "",
                            content_format: str = "", outline: list[dict] | None = None,
                            min_chars: int = 40) -> list[dict]:
    """从正文提取方案章节。

    返回 [{section_id, document_id, project_key, heading, text, level,
            structural_role, boundary_source, section_type,
            classification_source, classification_confidence, content_format}]

    章节正文 = 标题行起，到**下一个标题（任意级别）之前**——叶子式切分，父章节不含子章节正文，
    避免同一段文字在索引里重复出现。section_type 一律 unclassified（九类未定义，见模块说明）。

    min_chars：正文短于该值的章节丢弃（目录项、孤立标题等）。
    """
    lines = text.splitlines()
    if not lines:
        return []

    outline_headings = _headings_from_outline(outline or [])
    if outline_headings:
        boundary_source = "word_outline"
        headings = sorted(outline_headings, key=lambda h: h["line"])
    else:
        headings = _headings_from_text(lines)
        boundary_source = "text_pattern" if headings else "none"
    if not headings:
        return []

    # 标题合理性过滤：伪标题必须**从边界列表整个移除**（回落为正文），
    # 只跳过不删的话，它仍会错误地截断上一节。
    plausible, rejected = [], 0
    for h in headings:
        if 1 <= h["line"] <= len(lines) and is_plausible_heading(lines[h["line"] - 1]):
            plausible.append(h)
        else:
            rejected += 1

    # 表格块（连续 ≥2 行含 `|`）单独作为结构边界，标 unknown 排除。
    # 否则被移除边界的表格行会回落成上一节正文，整张业绩表随 content_section 进入索引。
    have = {h["line"] for h in plausible}
    forced_unknown = [ln for ln in _noise_table_starts(lines) if ln not in have]
    headings = sorted(plausible + [{"line": ln, "level": 9, "forced_role": "unknown"}
                                   for ln in forced_unknown], key=lambda h: h["line"])
    if not headings:
        return []

    # 封面区：开头 40 行内若有 3+ 个封面特征词，则该区间内的标题判为 cover
    cover_end = 0
    if _looks_like_cover(lines, 40):
        in_cover = [h["line"] for h in headings if h["line"] <= 40]
        if in_cover:
            cover_end = max(in_cover)
    repeats = _repeated_lines(lines)

    sections = []
    for idx, h in enumerate(headings):
        start = h["line"]
        end = headings[idx + 1]["line"] - 1 if idx + 1 < len(headings) else len(lines)
        if start > len(lines) or end < start:
            continue
        heading_text = lines[start - 1].strip()
        if heading_text in repeats and len(heading_text) < 30 and not h.get("forced_role"):
            continue                                    # 页眉/页脚复读行
        body = [ln.strip() for ln in lines[start:end] if ln.strip()]
        if not body:
            continue
        body_text = "\n".join(body)
        if len(body_text) < min_chars:
            continue
        role = h.get("forced_role") or _classify_structural_role(
            heading_text, body, start, cover_end, repeats)
        sections.append({
            "section_id": f"{document_id[:16]}-S{idx + 1:04d}",
            "document_id": document_id,
            "project_key": project_key,
            "heading": heading_text[:200],
            "text": body_text,
            "level": int(h["level"]),
            "structural_role": role,
            "boundary_source": boundary_source,
            "section_type": "unclassified",
            "classification_source": "none",
            "classification_confidence": 0.0,
            "content_format": content_format,
        })
    return sections


# —— 产品归一（2026-09-13 新增）——
# 背景：`contract_items.product_canonical` 全库 **597/597 为 NULL**，导致同一产品跨合同写法不一
# 时无法汇总 —— 实测 `LC-MS-MS精准靶向检测` / `LC-MS/MS 精准靶向代谢` / `多组学检测（非范本合同）/
# LC-MS/MS 精准靶向代谢-…` 是同一个产品族的三种写法。用户要的「识别哪个产品」不能只给原始串。
#
# 归一侧重**保守**：只做「去掉修饰后缀 + 统一已知同义写法」，**不做语义猜测**。
# 判不准的保留原样（宁可比对不上，也不要把两个不同产品并成一个）。

_PRODUCT_PREFIX_NORM = (
    # ⚠️ 这些是**前缀写法归一**（只替换前缀、保留后面真正的产品名），**不是**整串替换。
    # 曾经写成整串替换，结果 `10x Genomics 单细胞转录组测序` 与 `10x Genomics 空间转录组测序`
    # 都被压成 `10x Genomics` —— 那是**平台**不是产品，两个完全不同的产品被并成一个。
    (re.compile(r"^10\s*[xX×]\s*Genomics\b", re.I), "10x Genomics"),
    (re.compile(r"^10\s*[xX×]\s*"), "10x"),
    (re.compile(r"^LC[\s\-]*MS[\s/\-]*MS"), "LC-MS/MS"),
    (re.compile(r"^LC[\s\-]*MS(?![-\s/]*MS)"), "LC-MS"),
    (re.compile(r"^(?:Pro\s*)?DIA\s*定量蛋白质组\s*(?:检测)?"), "DIA 定量蛋白质组"),
)
# 整串无意义的噪声（表头/序号/纯数量），归一结果留空 —— 空**不等于**「没有产品」，只是「没识别出产品名」
_PRODUCT_NOISE = re.compile(r"^(?:\d+|\d+\s*个样本|/\s*样本|---|—+)$")


def canonical_product(product_raw: str | None) -> str | None:
    """把 `类别/服务名` 归一成**产品族**（取类别段 + 去修饰后缀 + 统一同义写法）。

    实测 `product_raw` 有 405 个不同取值，但第一段（类别）只有几十个 —— 类别才是「哪个产品」的粒度。

    返回 `None` 表示**未能识别出产品名**（噪声行），与「产品为空」是两回事，调用方需区分。
    """
    if not product_raw:
        return None
    s = product_raw.strip()
    if not s or _PRODUCT_NOISE.match(s):
        return None
    cat = s.split("/")[0].strip() or s          # 无斜杠时整串就是类别
    # 去掉括号后缀：`（非范本合同）`/`（GEM`/`（V1.0）`/`(SP版)` …（半角括号常不成对，故不要求闭合）
    cat = re.split(r"[（(]", cat, maxsplit=1)[0].strip()
    cat = re.sub(r"\s+", " ", cat).strip(" ·-—")
    if not cat:
        return None
    for rx, repl in _PRODUCT_PREFIX_NORM:
        if rx.match(cat):
            cat = rx.sub(repl, cat, count=1).strip()   # 只换前缀，保留产品名
            break
    return cat or None
