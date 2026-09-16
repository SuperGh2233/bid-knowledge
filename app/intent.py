"""查询**意图识别**层：一句自然语言 → (意图, 实体)，供 `/api/ask` 分派。

## 为什么要这一层（2026-09-16 需求方驱动）

需求方连续三轮在同一处踩坑（`华大转录组30w` 被拦、`质谱仪` 不行而 `测序仪` 可以、社保月份抓错）。
根因**不是漏了某个词，而是路由靠多份手写词表** —— 以「仪器」为例，同一概念写了三处：

    _FACT_KW（路由用）           : 仪器/设备/测序仪
    _UNSUPPORTED_CONDITIONS（拦截）: 仪器|质谱仪|流式|冰箱|离心机|设备|型号
    instrument_aliases.json（检索）: 质谱仪/测序仪/生物分析仪/…

三份表互不知道对方存在 → `测序仪` 能过、`质谱仪` 掉进洞里。**手写词表之间的漂移**才是缺陷类本身。

## 两条路径

- `recognize_intent_llm()` —— 调公司网关识别（**外发**：只发用户那一句查询，
  授权记录 `docs/llm-intent-authorization.md`）。**默认关闭**。
- `recognize_intent()` —— 入口：开关开→LLM，关/失败→**本地确定性识别**（离线路径），
  返回里的 `source` 如实标注走了哪条（`llm` / `local` / `local_fallback`）。

## 安全（代码层，不靠约定）

- `recognize_intent_llm(query)` 的**签名里只有查询句** —— 没有能传正文/路径/证据的入口。
- 默认关闭时**不构造任何网关请求**。
- 网关不可达/返回非法 JSON → **降级到本地**，不 500、不静默外发。
- 日志不打印查询原文与 key。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import app.config as config

# 意图取值（与 `/api/ask` 的四类分派一一对应）
INTENT_CONTRACT = "contract"          # 查合同（产品/金额/日期/机构）
INTENT_MATERIAL = "material"          # 查材料事实（社保/财务/发票/凭证…）
INTENT_INSTRUMENT = "instrument"      # 查仪器（通称或型号）
INTENT_SCHEME = "scheme"              # 查历史方案章节
INTENT_UNKNOWN = "unknown"
VALID_INTENTS = (INTENT_CONTRACT, INTENT_MATERIAL, INTENT_INSTRUMENT, INTENT_SCHEME, INTENT_UNKNOWN)


@dataclass
class Intent:
    """识别结果。`source` 如实标明走了哪条路径，供页面回显与审计。"""
    intent: str = INTENT_UNKNOWN
    product: str = ""
    amount_min: float | None = None
    date_from: str | None = None
    date_to: str | None = None
    vendor_include: list[str] = field(default_factory=list)
    vendor_exclude: list[str] = field(default_factory=list)
    product_exclude: list[str] = field(default_factory=list)
    instrument: str = ""
    material_type: str = ""
    confidence: float = 0.0
    reason: str = ""
    source: str = "local"             # llm | local | local_fallback
    decided: bool = True              # 本地是否**判得出**（False → 才值得问模型）


_SYSTEM = (
    "你是投标材料检索系统的**查询意图识别器**。只输出 JSON，不要解释、不要 markdown 代码块。\n"
    "意图取值：contract(查合同) / material(查材料事实) / instrument(查仪器) / scheme(查历史方案章节)。\n"
    "判据：\n"
    "- 提到仪器**通称或型号**（质谱仪/测序仪/流式细胞仪/Bruker timsTOF HT…）→ instrument\n"
    "- 提到**材料**（社保/财务/完税/纳税/发票/凭证/资质/照片）→ material\n"
    "- 提到**方案小节**（售后/培训/质量/保密/应急/风险/物流/样本接收…）→ scheme\n"
    "- 其余（产品 + 金额/日期/机构）→ contract\n"
    "JSON 字段（缺省留空，不要编造）：\n"
    '{"intent":"", "product":"", "amount_min":null, "date_from":null, "date_to":null,'
    ' "vendor_include":[], "vendor_exclude":[], "product_exclude":[],'
    ' "instrument":"", "material_type":"", "confidence":0.0, "reason":""}\n'
    "金额单位：万 → 数值×10000（如 30万 → 300000）。日期用 YYYY-MM 或 YYYY-MM-DD。"
)


class IntentNotAuthorized(RuntimeError):
    """未开启 LLM 意图识别（外发）时的显式拒绝。"""


def _guard() -> None:
    """与 `app/ocr.py` / `app/proposal.py` 的 `_guard()` 同一模式：默认关闭、未授权即抛错，
    避免"未授权却默默外发"。"""
    if not config.INTENT_LLM_ENABLED:
        raise IntentNotAuthorized("LLM 意图识别未启用（INTENT_LLM_ENABLED=false）")


def recognize_intent_llm(query: str, *, client=None) -> Intent:
    """调网关识别意图。**只把 `query` 这一句发出去** —— 签名里没有别的东西可传。

    `client` 可注入（测试用）；不注入时按 `app/proposal.py::_gen_client` 同款配置构造。
    """
    _guard()
    q = (query or "").strip()
    if not q:
        raise ValueError("查询为空")
    if client is None:
        from openai import OpenAI

        client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY,
                        timeout=config.INTENT_LLM_TIMEOUT, max_retries=1)
    resp = client.chat.completions.create(
        model=config.LLM_MODEL, temperature=0,
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": q}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    return _parse_llm_json(raw)


def _parse_llm_json(raw: str) -> Intent:
    """把模型输出解析成 `Intent`。**解析不了就抛错**，由调用方降级（不猜）。"""
    import json
    import re

    body = raw.strip()
    m = re.search(r"\{.*\}", body, re.S)          # 容忍模型套了 ```json 或前后废话
    if not m:
        raise ValueError(f"模型未返回 JSON：{body[:120]}")
    d = json.loads(m.group(0))
    intent = str(d.get("intent") or "").strip().lower()
    if intent not in VALID_INTENTS:
        intent = INTENT_UNKNOWN
    amt = d.get("amount_min")
    try:
        amt = float(amt) if amt not in (None, "") else None
    except (TypeError, ValueError):
        amt = None
    return Intent(
        intent=intent,
        product=str(d.get("product") or "").strip(),
        amount_min=amt,
        date_from=(str(d["date_from"]).strip() if d.get("date_from") else None),
        date_to=(str(d["date_to"]).strip() if d.get("date_to") else None),
        vendor_include=[str(x) for x in (d.get("vendor_include") or []) if str(x).strip()],
        vendor_exclude=[str(x) for x in (d.get("vendor_exclude") or []) if str(x).strip()],
        product_exclude=[str(x) for x in (d.get("product_exclude") or []) if str(x).strip()],
        instrument=str(d.get("instrument") or "").strip(),
        material_type=str(d.get("material_type") or "").strip(),
        confidence=float(d.get("confidence") or 0.0),
        reason=str(d.get("reason") or "").strip(),
        source="llm",
    )


def recognize_intent(query: str, *, client=None) -> Intent:
    """**唯一入口：本地优先，本地判不出才问模型**（2026-09-16 用户裁定）。

    为什么要本地优先：LLM 往返实测 **6.5–20 秒/句**；而绝大多数查询本地就能判。
    故顺序为 ——
      1. **本地识别**（零外发、毫秒级）：仪器/材料/方案命中词表，或合同能**完整解析**；
      2. 本地**判不出**（`decided=False`）且开关打开 → 才问模型（外发**只发这一句查询**）；
      3. 模型失败 → 回落本地并在 `source` 标 `local_fallback`（不 500、不静默外发）。

    "判不出"的判据是**既有解析器能否解析成功**（见 `recognize_intent_local`）——
    正是需求方踩坑的那类句子（`华大转录组30w` 被拦、`三十万` 认不出）会让它抛错。
    """
    q = (query or "").strip()
    local = recognize_intent_local(q)
    if local.decided or not config.INTENT_LLM_ENABLED:
        return local
    try:
        return recognize_intent_llm(q, client=client)
    except Exception:  # noqa: BLE001 —— 外发失败不阻断检索：降级到本地
        local.source = "local_fallback"
        return local


def recognize_intent_local(query: str) -> Intent:
    """**本地确定性**识别（零外发）：复用既有词表，与 LLM 路径产出同一 `Intent` 形状。

    **`decided` 的判据**（决定"要不要问模型"）：
      · 仪器通称/型号命中、材料词命中、方案小节词命中 → **已定**（词表够用）；
      · 合同类 → 用 `parse_demo_query` **试解析**：解析成功才算已定；
        抛 `ValueError`（认不出产品/金额、或命中不支持的写法，如 `华大转录组30w` 的厂商守卫、
        中文数字 `三十万`）→ **未定** → 交给模型兜底。
    """
    from app.api import detect_product, parse_demo_date, parse_demo_query, resolve_instrument_query
    # 惰性导入：这两个常量定义在 `routes_search`（本层被它调用），模块级导入会成环。
    from app.routes_search import _ASK_FACT_KW, _ASK_SCHEME_KW

    q = (query or "").strip()
    # 顺序即优先级（与 `/api/ask` 原有判据一致）：仪器 → 材料 → 方案 → 合同
    inst = resolve_instrument_query(q)
    if inst:
        return Intent(intent=INTENT_INSTRUMENT, instrument=q,
                      reason="命中仪器通称/型号", source="local")
    if any(k in q for k in _ASK_FACT_KW):
        return Intent(intent=INTENT_MATERIAL, reason="命中材料词", source="local")
    if any(k in q for k in _ASK_SCHEME_KW):
        return Intent(intent=INTENT_SCHEME, reason="命中方案小节词", source="local")
    dfrom, dto = parse_demo_date(q)
    decided = True
    reason = "按合同条件解析"
    try:
        parse_demo_query(q)
    except ValueError as exc:
        # 本地解析不了 → 未定。**不在这里抛**（调用方据 `decided` 决定是否问模型）。
        decided, reason = False, f"本地解析不了：{str(exc)[:60]}"
    return Intent(intent=INTENT_CONTRACT, product=detect_product(q),
                  date_from=dfrom, date_to=dto, reason=reason,
                  source="local", decided=decided)
