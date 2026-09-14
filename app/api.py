"""只读演示服务：合同定位 + 冻结售后证据/示范稿预览。"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

# ⚠️ `python -m app.api` 时本文件以 `__main__` 执行；而底部 include_router 会让路由模块
# 执行 `from app.api import ...` —— 若不注册，Python 会把 app/api.py **再加载一遍**为
# 新的 `app.api`，与正在执行的 `__main__` 形成双重实例 + 循环导入（实测 ImportError）。
# 自注册让后续 `import app.api` 直接复用当前主模块。测试走 `import app.api`（单份）不受影响。
if __name__ == "__main__":
    sys.modules.setdefault("app.api", sys.modules[__name__])

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.search import DEFAULT_ROOTS as MATERIAL_ROOTS
from app.search import (APPROVED_DOCUMENT_IDS, locate_by_product_amount,
                        search_scheme_sections)

BASE_DIR = Path(__file__).resolve().parents[1]
WORKSPACE = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
DEMO_DB = Path(os.environ.get("BID_AI_DEMO_DB", BASE_DIR / "bid_ai_clean_reg.db"))
# ponytail: 原先这里还有 EVIDENCE_FILE / DRAFT_FILE 两个指向 r0-snapshot 的**冻结示范稿**常量，
# 服务于已删除的 `/api/scheme-preview`（页签 02）。示范稿是试读阶段的道具、不是功能，
# 需求二的真实产物由 `/api/proposal-generate` 现场生成 —— 两个页面就够了。

PRODUCT_ALIASES = {
    # —— 2026-09-13 用户确认后补入。以下在语料里都是**真实产品**（见 contract_items.product_canonical）——
    # ⚠️ 两条硬约束，写错会静默串味：
    #   1. 别名是**子串关键词**（`alias in text` 判查询），**首个命中的条目即返回** → **窄的产品必须写在宽的前面**。
    #      只写「代谢」会让「空间代谢组」被「代谢组」抢走；只写「基因组」会同时命中
    #      「全基因组重测序」与「二代宏基因组测序」。
    #   2. 每个条目的别名里**要包含它自己的规范名**，否则「不要脂质组」这类排除写法
    #      会在 `_classify_term` 里归不了类（它判的是 `t == alias or t in aliases`）。
    # —— 具体产品线（窄）——
    "空间转录组": ("空间转录组", "空间转录"),
    "空间代谢组": ("空间代谢组", "空间代谢"),
    "脂质组": ("脂质组", "脂质"),
    "靶向检测": ("精准靶向", "靶向检测", "靶向"),
    "宏基因组": ("宏基因组", "二代宏基因组"),
    "全基因组重测序": ("全基因组重测序", "重测序"),
    "ATAC": ("ATAC",),
    "Xenium": ("Xenium",),
    "Olink": ("Olink",),
    "多组学": ("多组学",),
    # —— 宽产品线（后）——
    "代谢组": ("代谢", "代谢组", "全谱代谢", "LC-MS", "LC-MS/MS", "双平台代谢"),
    "单细胞": ("单细胞", "10x", "10X", "10x Genomics"),
    "蛋白组": ("蛋白", "蛋白组", "蛋白质组", "DIA"),
    # 「转录组」必须排在「单细胞」**之后**：`单细胞转录组` 是单细胞产品，
    # 若转录组在前会被抢走。而裸写「转录组」不含「单细胞」，仍能落到这一条。
    "转录组": ("转录组", "真核转录组", "真核有参转录组", "有参转录组"),
}
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(万元|万|元)")

# —— 产品的**负向**匹配词（2026-09-13 对抗审计后补）——
# 为什么需要：别名是**纯子串**匹配，宽词会把整条别的产品线吞掉。审计实测两例：
#   · 「转录组」命中「10x Genomics 单细胞转录组测序」「空间转录组测序」→
#     查转录组返回 38 份合同里只有 7 份真是转录组（**过度率 81.6%**）
#   · 「靶向」命中**反义词**「中药非靶向代谢组检测」（非靶向 = untargeted）
# 这一层按**明细行**判（不是合同级）：多组学打包合同里可能同时有真转录组行和单细胞行。
PRODUCT_MATCH_EXCLUDE = {
    "转录组": ("单细胞", "空间"),      # 单细胞转录组 / 空间转录组 是**别的产品线**
    "靶向检测": ("非靶向",),          # 非靶向是反义词
}

# —— 当前预览**不支持**的检索条件 ——
# 目的：宁可明确拒绝，也不把条件悄悄丢掉后继续搜，产出"看着正确、其实漏了条件"的结果。
# 顺序：在解析产品/金额**之前**判定，否则 "…代谢组合同，2024年12月以后" 会被解析出
# 产品与金额后正常返回，日期条件被静默忽略。
_UNSUPPORTED_CONDITIONS = (
    # 更具体的条件放在前面：同时含日期与社保时，报「财务／社保」比报「时间」更有指向性
    (re.compile(r"财务|社保|纳税|审计报告|社保月"), "财务／社保条件"),
    (re.compile(r"仪器|质谱仪|流式|冰箱|离心机|设备|型号"),
     "仪器／设备条件"),
    # 负向过滤（"不要蛋白组""排除宏基因组"）：当前只做正向产品匹配，
    # 若不拦，"找代谢组合同，不要蛋白组" 会照常返回——排除条件被静默丢弃。
    (re.compile(r"排除|不要|不含|去掉|除了|以外|剔除"), "排除／负向过滤条件"),
    (re.compile(r"发票|照片|凭证|回单|截图"), "发票／照片材料条件"),
    (re.compile(r"供应商|乙方|甲方|竞品|华大|吉凯|诺禾|分包"),
     "供应商／竞品范围条件"),
    # 方案主题检索：本页方案入口只提供一份冻结的售后服务示范稿，不支持按主题跨库检索。
    # 若不拦，"找写过售后方案的文件"会落到"请写明最低金额"，属误导性提示。
    (re.compile(r"方案|售后|培训|应急|保密|质量控制|风险识别|项目理解|样本接收"),
     "方案主题检索条件"),
    # 日期**已支持**「XXXX年X月 以后/以前」（见 parse_demo_date）。
    # 这里只拦**解析不了**的时间表述——不静默丢弃。
    (re.compile(r"近\s*[一二三四五六七八九十\d]+\s*年|截止|不早于|签约时间|签订日期"),
     "时间／日期条件"),
)


_CN_DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?")
_AFTER_WORDS = ("以后", "之后", "以来", "起", "后")
_BEFORE_WORDS = ("以前", "之前", "前")


def parse_demo_date(text: str) -> tuple[str | None, str | None]:
    """解析日期条件 → (date_from, date_to)，形如 `YYYY-MM` / `YYYY-MM-DD`。

    只认显式的「X年Y月 之后/以前」。**解析不出来就返回 (None, None)，
    由调用方决定是拒绝还是忽略**——绝不静默丢弃（见 unsupported_condition）。
    """
    m = _CN_DATE.search(text or "")
    if not m:
        return None, None
    y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (2015 <= y <= 2030 and 1 <= mo <= 12):
        return None, None
    stamp = f"{y:04d}-{mo:02d}-{int(d):02d}" if d else f"{y:04d}-{mo:02d}"
    tail = (text or "")[m.end(): m.end() + 6]
    if any(w in tail for w in _AFTER_WORDS):
        return stamp, None
    if any(w in tail for w in _BEFORE_WORDS):
        return None, stamp
    return None, None


def unsupported_condition(text: str) -> str | None:
    """返回命中的不支持条件名；无命中返回 None。"""
    for pattern, label in _UNSUPPORTED_CONDITIONS:
        if pattern.search(text or ""):
            return label
    return None


# —— 机构/产品范围条件（「乙方是X」「排除Y」）——
# 设计原则：**拒绝仍是默认**。只有能**完整解析出对象**（已知产品名 或 已知机构名）的条件才执行；
# 只要有一个词归不了类，就整体拒绝并说明是哪个词 —— 绝不"执行一半、丢掉一半"。
_KNOWN_ORGS = ("上海欧易生物医学科技有限公司", "上海鹿明生物科技有限公司",
               "欧易生物", "鹿明生物", "欧易", "鹿明",
               "华大", "华大基因", "吉凯", "吉凯基因", "诺禾", "诺禾致源",
               "联川", "百迈客", "美吉", "迈维", "中科新生命")
_ORG_ASK = re.compile(r"(?:乙方|供应商|承接方|服务方|受托方|中标人)\s*(?:是|为)\s*"
                      r"([^\s，,。；;、]*?)(?=\s|$|，|,|。|；|;)")
_SEP = re.compile(r"[或和与及、，,\s]+")
# 排除子句的窗口放宽后会夹带主句尾巴（如 `不要蛋白组，2024年以后的代谢组合同`）。
# 判据 A：某个词既归不了类、又含主句特征词 → 排除清单已结束、进入主句 → **停止解析且不报 unresolved**
#         （报成 unresolved 会把一条合法查询拒掉）。
_CLAUSE_HINT = re.compile(r"合同|万元|元|采购|响应文件|资料|材料|方案|发票|照片|凭证|年|月|日")
# 判据 B：但若该词**以**一个已知产品/机构名开头（`宏基因组的2万元以上代谢组合同`），
#         那它是一条**粘着主句的排除项**，必须**举报并拒绝** —— 判据 A 的 `break` 用在这里
#         会把用户真正要排的东西静默丢掉（已有测试钉住这个失效模式）。
def _starts_with_known(term: str) -> bool:
    for i in range(len(term), 1, -1):
        if _classify_term(term[:i])[0]:
            return True
    return False
# 捕获窗口**必须限长**：中文没有词边界，`乙方是欧易的2万元以上代谢组合同` 若不截断，
# 整段尾巴都会被吞进来，而整段里同时含「欧易」和「代谢组」——按产品优先判定就会归错类。
# ⚠️ `不含` **不在起手词里**：业务上「不含税」「不含运费」说的是价格口径，不是要排除某类材料。
# （对抗性复核实测：把「不含」当排除词会让 `…不含税` 被拒，理由还指向无关的「产品名/公司名」。）
# ⚠️ 捕获必须**跨逗号**：`不要蛋白组，宏基因组` 是两个排除项，在逗号处截断会让第二项
# 既不生效也不报错（**静默丢掉**，2026-09-13 对抗审计实测）。故窗口放宽到句末标点为止。
_EXCLUDE_ASK = re.compile(r"(?:排除|不要|去掉|剔除)\s*([^。；;]{0,40})")
# 排除子句的**起点**：主产品判定要从此处截断（见 `parse_demo_query`）。含 `_EXCLUDE_ASK`
# 的全部触发词，保证「摘掉主产品判定里的排除段」与「真正解析排除项」用同一套触发词。
_EXCLUDE_TRIGGER = re.compile(r"排除|不要|去掉|剔除")
# ⚠️ `_EXCLUDE_ASK` **故意不认**的否定词（`不含` 是价格口径词，「不含税」不是排除材料）。
# 但若它们后面紧跟一个**能归类的产品/机构名**，说明用户确实在排除 —— 解析器抓不到就必须**拒绝**，
# 不能静默丢掉。实测（2026-09-13 对抗审计）：`找2万元以上的代谢组合同，不含蛋白组，不要宏基因组`
# 会被放行且**只排掉宏基因组**（正解应排两个），多返回 2 份含蛋白行的合同。
_ALT_NEGATION = re.compile(r"(?:不含|除了|以外)\s*([^\s，,。；;]{0,12})")


def _org_key(term: str) -> str | None:
    """term 命中的**最短**已知机构名；取不到返回 None。

    取最短：这个键要拿去对 `party_a`/`party_b` 做**子串匹配**，简称（`欧易`）比全称匹配面更广。
    ⚠️ **只认「机构名出现在 term 里」（正向包含），不做反向包含** —— 反向包含会让任何作为
    公司名子串的泛词都算命中：对抗性复核实测 `排除上海` 会被归到「上海鹿明生物科技有限公司」
    （min 取短的那个），于是只排掉 3 条鹿明、欧易那 11 条照旧返回，而 filter_note 却宣称
    「已排除甲乙方含「上海鹿明生物科技有限公司」的合同」—— 排除对象和范围都错。
    """
    hits = [o for o in _KNOWN_ORGS if o in term]
    return min(hits, key=len) if hits else None


def _classify_term(term: str) -> tuple[str | None, str | None]:
    """把一个词归类 → ('org'|'product', 归一关键词)；归不了返回 (None, None)。

    产品判定**要求该词基本就是产品名**：别名与词的**长度差 ≤6**（放过 `Olink蛋白质组`
    这类带前缀的写法），而不是「含别名即算」。否则 `不要蛋白组和宏基因组的2万元以上代谢组合同`
    按「和」切出的后半片（含「代谢组」）会被当成一个产品，用户没点名的类别被凭空排除，
    而真正要排的「宏基因组」不被举报（对抗性复核实测：该查询返回 6 条且 exc_prod 含「代谢组」）。
    """
    t = (term or "").strip()
    if not t:
        return None, None
    org = _org_key(t)
    if org:
        return "org", org
    for alias, aliases in PRODUCT_ALIASES.items():
        if t == alias or t in aliases:
            return "product", alias
        if any(a in t and len(t) - len(a) <= 6 for a in aliases):
            return "product", alias
    return None, None


def parse_scope_conditions(text: str) -> tuple[tuple[str, ...], tuple[str, ...],
                                               tuple[str, ...], tuple[str, ...]]:
    """解析「乙方是X」与「排除Y」→ (机构包含, 机构排除, 产品排除, 无法归类)。

    **无法归类非空时调用方必须拒绝**（不静默丢弃条件）。
    """
    t = text or ""
    inc: list[str] = []
    exc_org: list[str] = []
    exc_prod: list[str] = []
    unresolved: list[str] = []

    for m in _ORG_ASK.finditer(t):
        for term in _SEP.split(m.group(1)):
            org = _org_key(term)              # include 分支**只认机构**：「乙方是X」里 X 必须是公司
            if org:
                inc.append(org)
            elif term.strip():
                unresolved.append(term.strip())

    for m in _EXCLUDE_ASK.finditer(t):
        for term in _SEP.split(m.group(1)):
            term = term.strip()
            if not term:
                continue
            kind, key = _classify_term(term)
            if kind == "org":
                exc_org.append(key)
            elif kind == "product":
                exc_prod.append(key)
            elif _CLAUSE_HINT.search(term) and not _starts_with_known(term):
                break          # 排除清单结束、进入主句 → 停止，且**不报 unresolved**
            else:
                unresolved.append(term)

    # `_EXCLUDE_ASK` 抓不到的否定词（不含/除了/以外）：若后面紧跟**能归类的对象**，
    # 就是一条我们解析不了的排除条件 → 归入 unresolved 让调用方拒绝。**不能静默丢掉。**
    # 只对「能归类」的才报（`不含税` 归不了类 → 不报，避免把价格口径词误判成排除材料）。
    for m in _ALT_NEGATION.finditer(t):
        for term in _SEP.split(m.group(1)):
            kind, _key = _classify_term(term)
            if kind and term.strip():
                unresolved.append(term.strip())

    return (tuple(dict.fromkeys(inc)), tuple(dict.fromkeys(exc_org)),
            tuple(dict.fromkeys(exc_prod)), tuple(dict.fromkeys(unresolved)))


_SCOPE_LABELS = ("排除／负向过滤条件", "供应商／竞品范围条件")


def scope_filter_note(party_include, party_exclude, product_exclude) -> str:
    """把实际施加的过滤如实说给用户 —— **尤其要说清「排除」做到什么程度**。"""
    parts = []
    if party_include:
        parts.append("只看甲乙方含「" + "」「".join(party_include) + "」的合同")
    if party_exclude:
        parts.append("已排除甲乙方含「" + "」「".join(party_exclude) + "」的合同")
    if product_exclude:
        parts.append("已排除**涉及**「" + "」「".join(product_exclude) + "」的合同（整份排除）")
    if not parts:
        return ""
    note = "本次实际生效的额外条件：" + "；".join(parts) + "。"
    if party_exclude:
        note += ("注意：本页只检索**我方**已核合同，库里没有竞品响应文件，"
                 "所以「排除某公司」实际做的是**排除甲乙方含该公司的合同**，"
                 "并不等于排除了竞品的响应材料。")
    return note


class SearchRequest(BaseModel):
    query: str


def parse_demo_query(query: str) -> tuple[str, tuple[str, ...], float, str | None, str | None]:
    """解析演示所需的 产品 / 最低金额 / 日期区间。

    返回 (product, aliases, min_amount, date_from, date_to)。
    金额可省：**带日期条件时**允许不给金额（此时按 0 起算）。
    日期条件日期解析不出来（如"最近三个月"）时仍按不支持拒绝，不静默忽略。
    """
    text = (query or "").strip()
    if not text:
        raise ValueError("请输入查询，例如：查找2万元以上的代谢组合同")
    date_from, date_to = parse_demo_date(text)
    # 含年月但解析不出「之后/以前」→ 条件语义不明，明确拒绝而非丢弃
    if _CN_DATE.search(text) and not (date_from or date_to):
        raise ValueError("日期条件请写成「XXXX年X月 以后 / 以前」，例如「2024年12月以后」")
    blocked = unsupported_condition(text)
    # 机构/产品范围条件：**只有确实解析出了对象**才放行。
    # ⚠️ 这里曾犯过一个严重后果的错误：只要 `parse_scope_conditions` 没报 unresolved 就清掉标签，
    # 但「排除/供应商」**词表是解析器的超集** —— `除了X以外`、`甲方是X`、`乙方不是X`、`找华大的…`
    # 都会命中词表却解析不出对象，于是标签被清、条件**静默消失**、结果按无条件返回
    # （对抗性复核实测：`找2万元以上的代谢组合同，除了欧易以外` → 返回全量 14 条且 filter_note 为空；
    #  `乙方不是欧易` 更糟 —— 返回的 14 条里有 11 条乙方正是欧易，语义反转）。
    # 故现在要求：**解析出至少一个对象**，且**其余不支持条件一个都不命中**，才允许放行。
    # 机构/产品范围条件是否确实解析出了对象 —— 后面判「要不要金额」时要用
    has_scope = False
    if blocked in _SCOPE_LABELS:
        inc_p, exc_org, exc_prod, unresolved = parse_scope_conditions(text)
        if unresolved:
            raise ValueError(
                f"无法确定要筛选/排除的对象：{'、'.join(unresolved)}。"
                f"请写明具体的产品名（代谢组／单细胞／蛋白组）或公司名（如 欧易、鹿明、华大、吉凯）。")
        if not (inc_p or exc_org or exc_prod):
            raise ValueError(
                f"当前预览暂不支持「{blocked}」的这种写法——未能识别出要筛选/排除的具体对象。"
                f"目前只认两种写法：「乙方是X」（可写「乙方是欧易或鹿明」）与「排除X／不要X」"
                f"（X 须是已知公司名或已知产品名）。")
        remaining = next((label for pattern, label in _UNSUPPORTED_CONDITIONS
                          if label not in _SCOPE_LABELS and pattern.search(text)), None)
        if remaining:
            # 同一句里还叠着别的不支持条件（发票/方案主题/时间…）→ 一律拒绝，不能只放行前半句
            raise ValueError(f"当前预览暂不支持「{remaining}」。本页只支持按产品＋最低金额查找合同，"
                            f"请去掉该条件后重试。")
        blocked = None
        # ⚠️ 只认**机构**条件 —— 用户 2026-09-13 确认的是「只给机构不给金额」。
        # **不含**排除型条件：把「代谢组合同，不要蛋白组」也放行属实现自行扩大，
        # 用户并未确认（对抗审计指出这一点）。
        has_scope = bool(inc_p or exc_org)
    if blocked:
        raise ValueError(f"当前预览暂不支持「{blocked}」。本页只支持按产品＋最低金额查找合同，"
                         f"请去掉该条件后重试。")
    if any(word in text for word in ("以下", "以内", "不超过", "最多")):
        raise ValueError("当前预览只支持“金额以上”的最低金额条件")
    amount = _AMOUNT.search(text)
    if not amount:
        # 金额可省的两个前提（都是**收窄**条件，不是放宽）：有日期、或有机构/产品范围。
        # 2026-09-13 用户确认放开后者 —— 原先只给机构（「乙方是欧易的代谢组合同」）
        # 会被要求「请写明最低金额」，而机构本身就是一道有效的收窄。
        if date_from or date_to or has_scope:
            value = 0.0
        else:
            raise ValueError("请写明最低金额，例如“2万元以上”；"
                             "也可以改用日期（“2024年12月以后”）或机构（“乙方是欧易”）来收窄范围")
    else:
        value = float(amount.group(1)) * (10_000 if amount.group(2) in ("万", "万元") else 1)
    # ⚠️ 主产品的判定必须**先把「不要X」及其并列续项整段摘掉**，否则被排除的产品会反过来
    # 被当成主产品。实测：`查找1万元以上的蛋白组合同，不要宏基因组` → 扫整句时先命中「宏基因组」，
    # 语义变成「查宏基因组、再排除宏基因组」→ **恒返回 0 条**（而库里明明有 19 份纯蛋白组合同）。
    # 这是**早就存在**的缺陷，不是补别名引进的：`查找2万元以上的单细胞合同，不要代谢组`
    # 在旧表下同样返回 0（「代谢组」当时就排第一）。
    # 用「从第一个排除触发词截断」而不是只删 `_EXCLUDE_ASK` 的捕获段 —— 后者的捕获窗口
    # 在**逗号**处截断，于是 `…不要蛋白组，宏基因组` 里的「宏基因组」仍留在正文里继续劫持
    # （2026-09-13 对抗审计实测的另一条可达路径）。
    include_text = _EXCLUDE_TRIGGER.split(text)[0]
    for canonical, aliases in PRODUCT_ALIASES.items():
        if any(alias.casefold() in include_text.casefold() for alias in aliases):
            return canonical, aliases, value, date_from, date_to
    raise ValueError("没能从这句话里认出产品。目前支持的产品：" + "、".join(PRODUCT_ALIASES))


def readonly_db() -> sqlite3.Connection:
    if not DEMO_DB.exists():
        raise RuntimeError(f"演示数据库不存在：{DEMO_DB}")
    con = sqlite3.connect(DEMO_DB.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


app = FastAPI(title="标书文库 · 初步预览", docs_url=None, redoc_url=None)


def live_scope(con) -> dict:
    """当前**实际**可查范围（从数据算，不写死文案）。

    教训：这几句边界说明原先写死为"2 份合同"，合同扩到 95 份后仍在页面上说"只有 2 份"——
    数字是实时的、说明文字是死的，二者脱节。改为实时计算。
    """
    approved = list(APPROVED_DOCUMENT_IDS)
    total_contracts = con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    return {"total_contracts": total_contracts,
            "queryable_contracts": len(_approved_contract_ids(con)),
            "approved_documents": len(approved)}






def _fold_by_contract(rows: list[dict]) -> list[dict]:
    """R6-06：把同一份合同的多条命中折成**一条文件结果**，内部业务记录进 `records`。

    为什么要折：一次查询里同一份多组学合同可能在多个产品键上各命中一次（实测 5 次），
    业务同事要的是「**哪几份文件能用**」，不是 5 张长得一样的卡片。
    折叠**不丢信息** —— 每条内部记录（产品/金额/证据）原样保留在 `records` 里。

    ⚠️ **保序**：按输入顺序取首次出现的合同建组（输入已按 R6-05 排序），
    组内 `records` 也保持原序 —— 折叠不得打乱排序层的结果。
    """
    order: list[str] = []
    groups: dict[str, dict] = {}
    for r in rows:
        key = r.get("contract_id") or r.get("document_id") or ""
        if key not in groups:
            order.append(key)
            groups[key] = {**r, "records": [], "record_count": 0}
        g = groups[key]
        g["records"].append(r)
        g["record_count"] += 1
        # 卡片标题/金额取**金额最大**的那条内部记录（与排序口径一致：金额大的更贴近"X万以上"的意图）
        cur, new = g.get("amount"), r.get("amount")
        if new is not None and (cur is None or new > cur):
            g["product"] = r.get("product")
            g["amount"] = new
            g["amount_source"] = r.get("amount_source")
            g["amount_status"] = r.get("amount_status")
            g["detail_evidence"] = r.get("detail_evidence")
    return [groups[k] for k in order]


# 自然问句 → fact_type 的粗映射（业务同事不会说「social_security_month」）
_FACT_KW = (
    (("社保", "社会保障", "社会保险"), "social_security_month"),
    (("财务", "审计报告", "资信"), "finance_period"),
    (("纳税", "税收", "完税"), "finance_period"),
    # ⚠️ 「照片」类**必须排在「仪器」之前**：这里取第一个命中，而「找仪器照片」
    # 同时含「仪器」和「照片」——排在后面会被当成 instrument 查询（实测同一坑）。
    (("仪器照片", "设备照片", "照片", "实拍"), "instrument_photo"),
    (("仪器", "设备", "测序仪"), "instrument"),
    (("发票",), "invoice"),
    (("采购合同",), "purchase_contract"),
    (("资质", "证书", "执照", "认证", "许可", "著作权"), "qualification"),
)


def parse_fact_query(text: str) -> tuple[str, str]:
    """自然问句 → (fact_type, 期间)。解析不出就返回空串（不猜）。"""
    t = (text or "").strip()
    ft = next((v for kws, v in _FACT_KW if any(k in t for k in kws)), "")
    fv = ""
    m = re.search(r"(20\d{2})\s*年(?:\s*(\d{1,2})\s*月)?", t)
    if m:
        fv = f"{m.group(1)}-{int(m.group(2)):02d}" if m.group(2) else m.group(1)
    return ft, fv


_MONTHISH = re.compile(r"^\d{4}(-\d{2})?$")


def period_covers(query_value: str, fact_value: str) -> bool:
    """`fact_value` 这个「起始/区间」式期间是否**覆盖** `query_value`。

    实测 `material_facts.fact_value` 的形态（全库 132 条）：
      - `None`            条目本身不含期间（如证书名）—— **不是缺失**
      - `'2024'`/`'2025'` 点值 —— 由精确 LIKE 命中，这里**不重复计入**
      - `'2021~'`/`'2023-05~'` **起始式**（「自2021年起缴纳」，未记录终期）
    同粒度下 ISO 字符串字典序即时间序，可直接比较。

    ⚠️ **起始式只能推出「至查询时点仍可能有效」，不能断言该月一定有材料** ——
    历史材料没记终期，中间是否断缴无从判断。故调用方必须与精确命中**分开呈现**。
    """
    qv = (query_value or "").strip()
    fv = (fact_value or "").strip()
    if not qv or not fv:
        return False
    if fv.endswith("~"):
        start = fv[:-1]
        return bool(start) and start <= qv
    if "~" in fv:
        a, _, b = fv.partition("~")
        return bool(a) and bool(b) and a <= qv <= b
    return False          # 点值不在此判定，避免与精确 LIKE 重复计数


# 需求一「一句话 → 自动判断查哪一类」的关键词表已随 `ask` 迁到 `app/routes_search.py`
# （`_ASK_FACT_KW` / `_ASK_SCHEME_KW` / `_ASK_CONTRACT_KW`）。
# ⚠️ 2026-09-14 清理：APIRouter 拆分时此处误留了三个**无人引用**的副本（真正的使用点
# 只在 `routes_search.py` 的 `ask()` 内）—— 同一口径两份定义正是本项目反复踩的坑，故删除。




# 文档角色 → 面向业务的中文标签（三模块与材料事实**共用一份**，避免两处叫法不一致）
_ROLE_SCOPE_LABEL = {"our": "我方响应/最终版",
                     "tender": "招标要求（采购人要求，非我方已附）",
                     "other": "其它来源文件"}
_FACT_EVIDENCE_RE = re.compile(r"^\[([a-z_]+)\]\s*")


def _annotate_fact_role(r: dict) -> None:
    """给一条材料事实补：**角色分层** + **把 evidence_text 里的内部枚举剥掉**。

    两个实测问题（2026-09-14 B2B 评审 P0）：
      1. `material_facts` 有 **2286/2373 条**的 `evidence_text` 形如 `[our_response] <文件名>`，
         前端把它当「文件中对应文字」上屏 —— 业务同事看到的是**英文内部枚举 + 文件名**，
         而那一行本该是**证据**。同一条规矩 `static/app.js` 开头自己写着「内部枚举一律不直接上屏」。
      2. `/api/material-facts`（页签 01「一句话问材料」走这条）**没有角色分层**，
         而 `/api/three-modules` 有 —— 同一个概念两处不一致。实测「哪些响应文件包含2025年的社保」
         返回 460 条，其中只有 205 条来自我方响应/最终版、68 条其实是**采购人的磋商文件**
         （采购人要求交社保，不是我方已附），标题却写「找到 460 份文件里有这类材料」。
         **不区分就是把「采购人要求」说成「我方已附」。**

    `evidence_text` 剥掉前缀后，若**与文件名相同** → 说明它本来就没有正文证据，
    置 `evidence_is_filename=True`，由前端只展示角色标签、不展示这一行。
    """
    role = r.get("document_role")
    r["role_scope"] = ("our" if role in ("our_response", "final_signed")
                       else "tender" if role == "tender_requirement" else "other")
    r["role_label"] = _ROLE_SCOPE_LABEL[r["role_scope"]]
    ev = r.get("evidence_text") or ""
    m = _FACT_EVIDENCE_RE.match(ev)
    if m:
        r["evidence_text"] = ev[m.end():].strip()
    stripped = (r.get("evidence_text") or "").strip()
    r["evidence_is_filename"] = bool(stripped) and stripped == (r.get("file_name") or "").strip()
    if r["evidence_is_filename"]:
        r["evidence_text"] = ""      # 只给了文件名 → 不是证据，不要当成证据上屏




# —— 用户 2026-09-13 明确的**三类**定位（其余类别不在此列）——
THREE_MODULES = {
    "项目业绩": {
        "source": "contracts（合同）+ 收款凭证台账",
        "fields": ["产品", "合同金额", "合同签订日期", "是否有收款凭证"],
    },
    "财务社保数据": {
        "source": "material_facts（finance_period / social_security_month）",
        "fields": ["包含什么月份"],
    },
    "仪器设备清单": {
        "source": "material_facts（instrument_name / instrument / purchase_contract / "
                  "instrument_purchase_contract / invoice / instrument_photo）",
        "fields": ["包含哪些仪器", "是否有采购合同", "是否有发票", "是否有仪器照片"],
    },
}

# ⚠️ 概览卡上的数字与点进去的条数**必须**来自同一份类型表（下面两个常量）。
# 实测（B2B 评审 P2）：概览卡原用 4 类（漏了 `instrument_name` / `instrument_purchase_contract`），
# 于是卡上写「仪器设备清单 546 条」、点进去 722 条 —— 同一屏两个数互相打架，
# 而这种矛盾会被读成「数据不可信」。类型表只此一份，两处引用同一常量。
_FINANCE_FACT_TYPES = ("finance_period", "social_security_month")
_INSTRUMENT_FACT_TYPES = ("instrument_name", "instrument", "purchase_contract",
                          "instrument_purchase_contract", "invoice", "instrument_photo")


def _count_material_facts(con, types) -> int:
    return con.execute(
        f"SELECT COUNT(*) FROM material_facts WHERE fact_type IN "
        f"({','.join('?' * len(types))})", types).fetchone()[0]


def _approved_contract_ids(con) -> set:
    """已完成核对、可查询的 CTL 合同号 —— **唯一口径**。

    教训：概览卡原按 `COUNT(*) FROM contracts WHERE contract_id LIKE 'CTL-%'` 数（98），
    而侧栏与查询路径按白名单数（95）。同一屏上两个数都叫「项目业绩有几份」，
    浏览与查询还落在**不同批文件**上（点进去能看到的 3 份是白名单外的未核对件）。
    """
    approved = list(APPROVED_DOCUMENT_IDS)
    return {str(row["contract_id"]) for row in con.execute(
        "SELECT c.contract_id, d.document_id FROM contracts c "
        "JOIN documents d ON d.document_id=c.document_id "
        "WHERE c.contract_id LIKE 'CTL-%'")
        if any(str(row["document_id"]).startswith(p) for p in approved)}






# ponytail: 原先这里有一个 `GET /api/proposal-evidence`（只到证据包、不生成正文）。
# 它是页面上的一个**中间步骤**：用户先点「找证据」看一遍原文，再点「生成方案」。
# 但两个生成按钮读的是**同一个输入框**，且 `/api/proposal-generate` 返回的 `citations`
# 已经逐条给出引用来源 —— 中间那一步不产生用户要的东西。按第一性原理删掉：
# **需求二就是一个输入框 → 一份方案**。证据包本身仍在 `app/proposal.py` 里被生成接口复用。






MODE_DEPRECATED_MSG = ("需求二只保留模型起草（会外发、需授权）；本地拼装已下线。"
                       "mode 只能为 llm（缺省即 llm）。")






@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# —— 路由拆分（2026-09-14 APIRouter 整理）——
# ⚠️ include **必须放在文件最底部**：三个路由文件顶部 `from app.api import ...` 取的都是
# 本模块已定义完的名字；若提前 include，路由导入时会撞上**尚未定义的共享辅助**（循环导入）。
# ⚠️ 重导出被测/脚本直接引用的函数名（`three_modules` 被 tests/test_module_counts.py 直接调用、
#    `proposal_generate` 被 scripts/make_quality_review_samples.py 直接调用）—— API 路径与
#    函数语义不变，只是搬了家。
from app.routes_open import router as _open_router
from app.routes_proposal import proposal_generate, router as _proposal_router
from app.routes_search import router as _search_router, three_modules
from app.routes_status import router as _status_router

app.include_router(_status_router)
app.include_router(_search_router)
app.include_router(_proposal_router)
# 文件打开放在最后：它是唯一会启动外部程序的端点，独立成文件便于审阅与"删一行即下线"。
app.include_router(_open_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=False)