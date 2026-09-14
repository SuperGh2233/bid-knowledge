r"""招标文件的服务时限要求 → 逐条抽取 → 对照历史承诺 → 标出历史未覆盖的。

**为什么要有这个模块**（2026-09-14 实测得出的业务事实）：
逐项目对照 43 个项目后发现，响应文件里的服务时限**不是各项目自己拍的** ——
招标文件写了时限的，**56% 被逐字照抄**进响应文件，另 38% 同类改写。
即：**时限的依据是「该项目的招标文件」，不是「历史响应文件」**。

故对一份**新**标书，正确做法是**从新招标文件里取**；历史材料的作用是：
  ① 当模板（「响应 / 到场 / 解决 / 服务期」这套结构）；
  ② 当底账（我们历史上最多承诺过什么，避免写出做不到的数）。
本模块就是把这两件事接起来：**逐条列出招标要求 → 给出历史同类的承诺分布 → 标出没覆盖的条款**。

安全：纯文本处理，**零外发**；上传的文件**不落库**，解析在内存里完成。
"""
from __future__ import annotations

import re

from app.proposal import _slot_of, _time_mentions

# 槽位 → 展示名（招标文件里的叫法更正式）
SLOT_LABEL = {
    "响应": "响应", "回复": "回复", "反馈": "反馈", "到场": "到场",
    "上门": "上门", "交付": "交付", "完成": "解决/完成",
    "服务周期": "服务周期", "质保": "质保", "保修": "保修", "供货周期": "供货周期",
}
# 招标文件里的**要求式**措辞 —— 用于提示「这是硬性条款」而不是可选承诺
_MANDATORY = ("必须", "须", "应当", "应", "不得", "▲", "★", "实质性")
_SENT_SPLIT = re.compile(r"[。；;\n]|(?<=\d)\s*\|\s*")


def _clause_of(text: str, start: int, end: int, width: int = 70) -> str:
    """时限所在的**分句**：从时限位置**向两侧各找最近的句读**，保真可回溯。

    ⚠️ 首版写成「取 ±60 字，再砍掉最后一个句读之前的部分」—— `rfind` 会找到时限**之后**的
    句读，于是把整句砍成了尾巴（实测产出 `供应商应具备质量管理体系`、甚至空串）。
    必须**分别**向左、向右找边界。
    """
    lo = max(0, start - width)
    hi = min(len(text), end + width)
    left = max((text.rfind(c, lo, start) for c in "。；;\n"), default=-1)
    right = min((p for p in (text.find(c, end, hi) for c in "。；;\n") if p >= 0),
                default=-1)
    # ⚠️ 窗口内找不到边界时必须退回 `lo`／`hi`，**不能**用 -1 / 落空 ——
    # 招标文件封面那一段（「密级：公开 竞争性谈判文件 专用文件商务册 …」）没有句读，
    # 回退成 -1 会从**文档开头**整段截取，原句变成一堆无关的封面文字。
    seg = text[(left + 1) if left >= 0 else lo: right if right >= 0 else hi]
    return re.sub(r"\s+", " ", seg).strip()


def extract_time_requirements(text: str, *, limit: int = 40) -> list[dict]:
    """从招标文件正文抽出**服务时限要求**。每条带原句（可回溯）、槽位、归一后的分钟数。

    抽不到就返回空 —— **不猜**。槽位判复用 `app.proposal._slot_of`（它与生成侧同一套判据）。
    """
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _time_mentions(text or ""):
        slot = _slot_of(text, m.start, m.end)
        if not slot:
            continue
        try:
            from app.proposal import _UNIT_MINUTES, _UNIT_MINUTES_MORE
            minutes = float(m.value) * _UNIT_MINUTES.get(
                m.unit, _UNIT_MINUTES_MORE.get(m.unit))
        except (ValueError, KeyError, TypeError):
            continue
        key = (slot, int(minutes))
        if key in seen:
            continue
        seen.add(key)
        clause = _clause_of(text, m.start, m.end)
        out.append({
            "slot": slot,
            "slot_label": SLOT_LABEL.get(slot, slot),
            "value": m.raw.replace(" ", ""),
            "minutes": int(minutes),
            "clause": clause,
            # 硬性条款提示：招标文件用「必须/须/应当/▲」标实质性要求 —— 必须响应，不能漏
            "mandatory": any(k in clause for k in _MANDATORY),
        })
        if len(out) >= limit:
            break
    # 硬性条款排前面（漏了会废标的先看）
    out.sort(key=lambda x: (not x["mandatory"], x["minutes"]))
    return out


def compare_with_history(reqs: list[dict], kb: list[dict]) -> list[dict]:
    """给每条招标要求配上**历史同类承诺的分布**与**覆盖判定**。

    `kb` = `app.proposal.build_module_kb` 的产出（方案模块的 `commitments`）。
    跨模块取**同一槽位**的并集 —— 招标说「响应≤24小时」时，售后方案与应急预案里的
    响应承诺都是有效的先例，不分小节。

    判定（**三态，不得压成二态**）：
      - `covered`   —— 历史里出现过**同等或更严**的承诺 → 我司做过，可照此响应；
      - `stricter`  —— 招标要求比历史**任何一次**都严 → **需确认能否做到**，不能直接抄；
      - `uncovered` —— 历史材料里没有该槽位的量化承诺 → **没有先例**，须自行拟定。
    """
    hist: dict[str, dict[int, dict]] = {}
    for m in kb or []:
        for c in m.get("commitments") or []:
            slot = c.get("slot")
            if not slot:
                continue
            bucket = hist.setdefault(slot, {})
            for raw in c.get("values") or []:
                bucket.setdefault(_minutes_of(raw), {"raw": raw, "modules": set()})
                bucket[_minutes_of(raw)]["modules"].add(m.get("module"))
    out = []
    for r in reqs:
        bucket = hist.get(r["slot"]) or {}
        vals = sorted(bucket.items())
        hist_list = [{"value": v["raw"], "minutes": k, "modules": sorted(v["modules"])}
                     for k, v in vals]
        if not vals:
            verdict, note = "uncovered", "历史材料里没有该槽位的量化承诺，**没有先例可循**，需自行拟定"
        elif r["minutes"] >= vals[0][0]:
            verdict = "covered"
            note = f"历史上有更严或同等的承诺（最严 {vals[0][1]['raw']}）—— 我司做过，可照此响应"
        else:
            verdict = "stricter"
            note = (f"招标要求比历史**任何一次**都严（历史最严 {vals[0][1]['raw']}）"
                    f"—— **需确认能否做到**，不要直接照抄历史")
        out.append({**r, "verdict": verdict, "note": note, "history": hist_list})
    return out


def _minutes_of(raw: str) -> int:
    """历史承诺值的字符串 → 分钟（用于比较）。解析不出给一个极大值，排到最后。"""
    m = re.match(r"^(\d+(?:\.\d+)?)(.*)$", (raw or "").strip())
    if not m:
        return 10 ** 9
    from app.proposal import _UNIT_MINUTES, _UNIT_MINUTES_MORE
    unit = m.group(2).strip()
    mult = _UNIT_MINUTES.get(unit, _UNIT_MINUTES_MORE.get(unit))
    return int(float(m.group(1)) * mult) if mult else 10 ** 9


def summarize(compared: list[dict]) -> dict:
    """汇总：硬性条款漏了几条、需要确认的几条。**数字要能直接给业务同事看**。"""
    return {
        "total": len(compared),
        "mandatory": sum(1 for c in compared if c["mandatory"]),
        "covered": sum(1 for c in compared if c["verdict"] == "covered"),
        "stricter": sum(1 for c in compared if c["verdict"] == "stricter"),
        "uncovered": sum(1 for c in compared if c["verdict"] == "uncovered"),
    }


# ============================================================================
# 逐条应答核对表：把招标文件的**实质性条款**抽成清单，并指向我司对应的材料池
# ============================================================================
# 为什么用 ★/▲ 作主信号（2026-09-14 实测 253 份招标文件）：
#   ★ 出现 **1076 次**、▲ **480 次**，且中文招标文件里 ★/▲ **就是「实质性条款」的法定标记**
#   —— 不响应即废标。而「投标人须/应…」的义务句有 3282/1919 条，噪声太大，
#   全抽出来反而淹没了真正会废标的那几条。**先抓会死人的**。
_MARKS = "★▲"
# 表格式：`1 | ★ | 交货时间 | 自合同签订之日起30日`
_TABLE_ROW = re.compile(r"^\s*(\d{1,3})\s*\|\s*[" + _MARKS + r"]\s*\|\s*([^|\n]{1,40})\s*\|\s*(.{1,200})")
# 小节式：`★服务内容（服务量清单）` / `★1、投标人应当具备…`
_SECTION = re.compile(r"^\s*[" + _MARKS + r"]\s*([^\n|]{2,60})")

# 条款归类：**顺序即优先级**（前面的更具体）。用于指向我司对应的材料池。
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("资格资质", ("营业执照", "资质", "资格", "证书", "认证", "许可", "CNAS", "ISO",
                  "高新技术", "体系", "执照", "信用", "失信", "声明函", "授权")),
    # ⚠️ 不放裸的「缴纳」：「履约保证金：**不缴纳**」会被归到这里（实测），
    # 而它是商务条款。财务社保只认**明确的凭证类**词。
    ("财务社保", ("审计报告", "财务报告", "财务报表", "纳税", "税收", "完税", "社保",
                  "社会保障", "社会保险", "资信证明", "缴纳证明", "依法缴纳")),
    # ⚠️ 不放「合同金额」：「结算方式」的正文含「支付剩余**合同金额**」→ 会被误归到业绩（实测）。
    # 业绩类只认**明确的业绩措辞**。
    ("业绩案例", ("业绩", "类似项目", "成功案例", "同类项目", "案例一览", "中标通知书", "履约经历")),
    ("人员团队", ("项目负责人", "项目经理", "职称", "人员", "团队", "学历", "驻场", "配备")),
    ("仪器设备", ("仪器", "设备", "型号", "工作站", "测序仪", "质谱")),
    ("履约交付", ("交货", "交付", "期限", "工期", "周期", "地点", "验收", "进度", "完成时间")),
    ("商务条款", ("保证金", "支付", "报价", "价格", "密封", "签章", "份数", "履约",
                  "合同", "发票", "税", "预算", "控制价", "计费", "结算", "金额", "付款")),
    ("服务要求", ("服务内容", "售后", "培训", "质保", "响应", "维护", "技术支持",
                  "应急", "保密", "质量")),
)
# 类别 → 我司材料池（用于「我司有没有对应材料」）。None = 无直接对应池，只能人工判断。
_CATEGORY_POOL: dict[str, str] = {
    "资格资质": "qualification",
    "财务社保": "finance_period",
    "仪器设备": "instrument",
    "业绩案例": "contracts",
    "服务要求": "scheme",
}


# 无材料池的类别，要说清**该做什么** —— 只说「无对应材料」对业务同事没用：
# 「交货时间/支付方式」这类是**要你承诺或填写**的合同条款，不是要你交证明材料。
_NO_POOL_NOTE = {
    "履约交付": "属**履约条款**，须在响应文件里明确承诺（交货时间/地点/验收方式），无需额外证明材料",
    "商务条款": "属**商务条款**，须在响应文件里逐条确认或填写（支付方式/保证金/报价口径）",
    "人员团队": "须在响应文件里给出**人员名单与资质**（项目负责人/职称/社保证明）",
}


def classify_clause(text: str) -> str:
    """把一条条款归到类别。**归不了返回「其它」**，不硬塞。"""
    t = text or ""
    for cat, kws in _CATEGORY_RULES:
        if any(k in t for k in kws):
            return cat
    return "其它"


def extract_hard_requirements(text: str, *, limit: int = 60) -> list[dict]:
    """抽出招标文件里的**实质性条款**（★/▲）。每条带条款名、原文、类别。

    只抓 ★/▲ —— 那是「不响应即废标」的标记，也是最不容易误报的信号
    （义务句「须/应」有数千条，全抓会把真正致命的几条淹掉）。
    """
    out: list[dict] = []
    seen: set[str] = set()
    lines = [re.sub(r"\s+", " ", x).strip() for x in (text or "").splitlines()]
    for i, line in enumerate(lines):
        if not line or not any(m in line for m in _MARKS):
            continue
        m = _TABLE_ROW.match(line)
        if m:
            seq, name, value = m.group(1), m.group(2).strip(), m.group(3).strip()
        else:
            m = _SECTION.match(line)
            if not m:
                continue
            seq, name, value = "", m.group(1).strip(), ""
            # `★1、投标人应当具备…` → 名字取到句读为止，其余算正文
            cut = min((p for p in (name.find(c) for c in "：:，,。；;") if p > 0), default=-1)
            if cut > 0:
                value, name = name[cut + 1:].strip(), name[:cut].strip()
            else:
                # 小节式（`★服务内容（服务量清单）`）**正文在下面几行** ——
                # 不接住的话这条只剩个标题，业务同事看不到要求内容（实测）。
                body: list[str] = []
                for nxt in lines[i + 1: i + 6]:
                    if not nxt or any(k in nxt for k in _MARKS):
                        break
                    body.append(nxt)
                    if sum(len(b) for b in body) > 160:
                        break
                value = " ".join(body)[:160]
        if len(name) < 2:
            continue
        key = re.sub(r"\s+", "", name + value)[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "seq": seq,
            "name": name[:40],
            "text": (value or name)[:160],
            "category": classify_clause(name + " " + value),
            "severity": "hard",      # ★/▲ = 实质性条款，不响应即废标
        })
        if len(out) >= limit:
            break
    return out



# 材料池的**事实类型**归属（与 `_CATEGORY_POOL` 的键一致）
_POOL_FACT_TYPES: dict[str, tuple[str, ...]] = {
    "qualification": ("qualification",),
    "finance_period": ("finance_period", "social_security_month"),
    "instrument": ("instrument", "instrument_name", "instrument_photo",
                   "invoice", "purchase_contract", "instrument_purchase_contract"),
}


def material_pools(con) -> dict[str, dict]:
    """我司各材料池的规模 —— 回答「这类要求，我司有没有对应材料、有多少」。

    ⚠️ 这是**粗粒度线索**，不是「已满足」的结论：它只说「我司在库里有多少份这类材料」。
    具体某一条要求（如「具备 CNAS 认证」）是否满足，仍要人工打开文件核对 ——
    **不替用户下结论**（与项目一贯口径一致）。
    """
    pools: dict[str, dict] = {}
    for pool, types in _POOL_FACT_TYPES.items():
        q = ",".join("?" * len(types))
        # 用**下标**取值：调用方传进来的连接不一定设了 `row_factory`（实测传裸连接会
        # `TypeError: tuple indices must be integers`）。
        row = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT document_id) FROM material_facts "
            f"WHERE fact_type IN ({q})", types).fetchone()
        pools[pool] = {"count": row[0], "documents": row[1]}
    n = con.execute("SELECT COUNT(*) FROM contracts WHERE contract_id LIKE 'CTL-%'").fetchone()[0]
    pools["contracts"] = {"count": n, "documents": n, "unit": "份合同"}
    return pools


def build_check_table(con, text: str) -> dict:
    """**逐条应答核对表**：实质性条款 + 我司对应材料池的规模。

    与 `extract_time_requirements` 的分工：
      · 本函数抓**全部** ★/▲ 实质性条款（资质、业绩、交付、商务…），给的是「别漏条款」的清单；
      · 时限那一类另有 `compare_with_history`，给的是「该承诺多少」的对照。
    """
    reqs = extract_hard_requirements(text)
    pools = material_pools(con)
    for r in reqs:
        pool = _CATEGORY_POOL.get(r["category"])
        r["pool_key"] = pool
        if pool and pool in pools:
            p = pools[pool]
            r["pool_note"] = (f"我司库里有 {p['documents']} 份这类材料可作响应依据"
                              if p["documents"] else "我司库里**没有**这类材料")
        elif pool == "scheme":
            r["pool_note"] = "属方案类，见「模块化经验」与生成时的证据包"
        else:
            r["pool_note"] = _NO_POOL_NOTE.get(
                r["category"],
                "**未能归类**，请人工确认这一条到底要求什么")
    by_cat: dict[str, int] = {}
    for r in reqs:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    return {"checklist": reqs, "by_category": by_cat, "total": len(reqs)}
