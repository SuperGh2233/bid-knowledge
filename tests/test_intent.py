"""查询意图识别层（`app/intent.py`）单测。

由来：需求方连续三轮在同一处踩坑（`华大转录组30w` 被拦、`质谱仪` 不行而 `测序仪` 可以），
根因是**路由靠多份手写词表**且互相漂移。用户 2026-09-16 选择「调 LLM 做意图识别」，
授权记录 `docs/llm-intent-authorization.md`。

本文件钉住三件事：
  1) **默认关闭**，关闭时走本地识别，且**绝不构造网关请求**；
  2) 开启时走 LLM，能处理口语化问法；
  3) 网关失败 → **降级到本地**（不 500、不静默外发）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config as config  # noqa: E402
import app.intent as I  # noqa: E402


class _StubLLM:
    """模拟网关。`content` 可换，用来测不同返回（含非法 JSON）。"""
    def __init__(self, content: str):
        self._content = content

    def __call__(self):  # 让实例可直接当 client 用
        return self

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **_):
        outer = self

        class _M:
            content = outer._content

        class _Choice:
            message = _M()

        class _R:
            choices = [_Choice()]
        return _R()


def test_local_first_never_calls_gateway_when_local_decides(monkeypatch):
    """**本地优先**：本地判得出时**绝不发网关请求**（快、零外发）。

    2026-09-16 用户裁定「本地优先 + 判不出大模型兜底」—— LLM 往返实测 6.5–20 秒/句，
    故绝大多数查询必须走本地。用「一被调用就失败」的 client 钉住这一点。
    """
    monkeypatch.setattr(config, "INTENT_LLM_ENABLED", True)   # 即使开着，本地能判也不该调用

    def _boom(**_):
        raise AssertionError("本地判得出时不得发起网关请求")

    for q in ("质谱仪", "找含2025年社保的资料", "售后服务方案", "代谢组2万元以上的合同"):
        i = I.recognize_intent(q, client=_boom)
        assert i.source == "local" and i.decided, q


def test_undecidable_query_goes_to_llm(monkeypatch):
    """本地**判不出**（既有解析器抛 ValueError）→ 才交给模型兜底。

    需求方踩坑的那类句子正是判据来源：`华大…` 被厂商守卫拦、中文数字 `三十万` 认不出。
    """
    monkeypatch.setattr(config, "INTENT_LLM_ENABLED", True)
    stub = _StubLLM('{"intent":"contract","product":"转录组","amount_min":300000,'
                    '"vendor_include":["华大"],"confidence":0.95,"reason":"口语化改写"}')
    q = "帮我把华大做的那种转录组、三十万往上的合同找出来"
    assert I.recognize_intent_local(q).decided is False      # 本地：未定
    i = I.recognize_intent(q, client=stub)
    assert i.source == "llm" and i.amount_min == 300000.0    # 模型：兜底成功


def test_undecidable_and_disabled_stays_local(monkeypatch):
    """本地判不出但**开关关闭** → 仍返回本地结果（行为与改前一致），**不外发**。"""
    monkeypatch.setattr(config, "INTENT_LLM_ENABLED", False)

    def _boom(**_):
        raise AssertionError("关闭状态下不得发起网关请求")

    i = I.recognize_intent("帮我把华大做的那种转录组、三十万往上的合同找出来", client=_boom)
    assert i.source == "local" and i.decided is False


def test_llm_role_guard_still_enforced(monkeypatch):
    """未授权时**直接调用** LLM 识别必须抛错（守卫本身没被本地优先绕过）。"""
    monkeypatch.setattr(config, "INTENT_LLM_ENABLED", False)

    def _boom(**_):
        raise AssertionError("未授权不得发起网关请求")

    with pytest.raises(I.IntentNotAuthorized):
        I.recognize_intent_llm("质谱仪", client=_boom)


def test_llm_path_parses_colloquial_query(monkeypatch):
    """开启后由模型识别 —— 能处理**本地词表判不出**的口语化问法。"""
    monkeypatch.setattr(config, "INTENT_LLM_ENABLED", True)
    stub = _StubLLM('{"intent":"contract","product":"转录组","amount_min":300000,'
                    '"vendor_include":["华大"],"confidence":0.9,"reason":"乙方=华大+转录组+30万"}')
    i = I.recognize_intent_llm("帮我把华大做的那种转录组、三十万往上的合同找出来", client=stub)
    assert i.intent == I.INTENT_CONTRACT
    assert i.product == "转录组" and i.amount_min == 300000.0
    assert i.vendor_include == ["华大"] and i.source == "llm"


def test_llm_tolerates_code_fence_and_rejects_garbage(monkeypatch):
    """容忍模型套 ```json 围栏；**返回非法 JSON 时抛错**（由调用方降级，不猜）。"""
    monkeypatch.setattr(config, "INTENT_LLM_ENABLED", True)
    ok = _StubLLM('```json\n{"intent":"instrument","instrument":"质谱仪"}\n```')
    assert I.recognize_intent_llm("质谱仪", client=ok).intent == I.INTENT_INSTRUMENT
    with pytest.raises(ValueError):
        I.recognize_intent_llm("质谱仪", client=_StubLLM("我觉得这是查仪器"))   # 无 JSON → 抛错


def test_gateway_failure_falls_back_to_local(monkeypatch):
    """本地判不出、且网关不可达 → **降级到本地**，`source=local_fallback`；不 500、不静默外发。"""
    monkeypatch.setattr(config, "INTENT_LLM_ENABLED", True)

    def _boom(**_):
        raise RuntimeError("网关不可达")

    q = "帮我把华大做的那种转录组、三十万往上的合同找出来"
    i = I.recognize_intent(q, client=_boom)
    assert i.source == "local_fallback" and i.decided is False


def test_unknown_intent_is_not_guessed(monkeypatch):
    """模型给出词表外的 intent → 归 `unknown`（不硬塞成某一类）。"""
    monkeypatch.setattr(config, "INTENT_LLM_ENABLED", True)
    i = I.recognize_intent_llm("随便问问", client=_StubLLM('{"intent":"chat"}'))
    assert i.intent == I.INTENT_UNKNOWN
