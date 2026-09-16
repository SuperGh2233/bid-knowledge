"""候选行语义分类（LLM，已授权）→ 只把「我方已附材料」写入 material_facts。

授权依据：docs/authorizations/llm-classification-authorization.md
  - 只发**候选行短文本**，不整篇发送；
  - 发送前对手机号/身份证号打码；
  - 不发文件路径与文档名。

三段式（据 4 路对抗复核的建议）：
  ① 结构特征找候选（确定性，宽口径保召回）—— 见本文件 candidates()
  ② 语义分类 source（LLM）—— 本文件 classify()
  ③ 字段抽取（确定性，本地）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLEAN = Path(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean")
sys.path.insert(0, str(CLEAN))

from dotenv import dotenv_values  # noqa: E402
_env = dotenv_values(r"C:\Users\hao.guo\Desktop\标书文库\bid-ai\.env")
os.environ["BID_AI_CLEAN_DB"] = str(CLEAN / "bid_ai_clean_reg.db")

from app import db as dbm  # noqa: E402

BATCH = 40
STATE = CLEAN / "data" / "material_classify_state.json"

NUM_PREFIX = re.compile(
    r"^\s*(?:[（(]\s*[一二三四五六七八九十\d]{1,3}\s*[)）]"
    r"|[一二三四五六七八九十]{1,3}\s*[、.．]"
    r"|\d{1,2}\s*[、.．]"
    r"|\d{1,2}\s*[-－]\s*\d{1,2}"
    r"|\d{1,2}(?:\.\d{1,2}){1,3})\s*")
MATERIAL_KW = ("财务", "审计", "纳税", "税收", "税务", "社保", "社会保障", "社会保险",
               "公积金", "资信", "资质", "证书", "执照", "许可", "认证", "仪器", "设备",
               "发票", "保证金", "学历", "学位", "职称", "缴纳", "缴费", "保险", "报告")
_PHONE = re.compile(r"\b1[3-9]\d{9}\b")
_IDCARD = re.compile(r"\b\d{17}[\dXx]\b")
# 行内**含年份**——这是"带期间的材料条目"的结构特征（如
# `社保证明（2023年5月至今为上海鹿明(子公司)缴纳，在职证明）`）。
# 这类行**既无编号前缀、也不以冒号结尾**，首版判据把它们整片漏掉（实测 15 条），
# 正是对抗复核警告的「窄到没用」。加此判据后一并召回。
_HAS_PERIOD = re.compile(r"20\d{2}\s*年")


def redact(s: str) -> str:
    """外发前打码：手机号、身份证号（授权记录 §四）。"""
    s = _PHONE.sub("[手机号已打码]", s)
    return _IDCARD.sub("[身份证号已打码]", s)


def candidates(text: str) -> list[dict]:
    """候选行 + **上文**（1 行）。

    为什么要带上文：首轮分类**不带上下文**，`社保缴纳记录：` 被判成 other——
    但它上文是 `项目组成员-赵仕兰`，脱离上下文确实歧义（**无上下文是我的设计缺陷**）。
    带 1 行上文即可消歧，外发量增加很小。
    """
    out = []
    lines = [x.strip() for x in (text or "").splitlines()]
    for i, ln in enumerate(lines):
        if not ln or len(ln) > 40:
            continue
        if not any(k in ln for k in MATERIAL_KW):
            continue
        if not (NUM_PREFIX.match(ln) or ln.endswith(("：", ":")) or _HAS_PERIOD.search(ln)):
            continue
        prev = lines[i - 1] if i else ""
        out.append({"line": i + 1, "text": ln, "prev": prev[:40]})
    return out


PROMPT = """你在帮助整理一家公司（投标人）的历史投标响应文件。

下面是从**响应文件正文**里抽出的候选行。请判断**每一行**属于哪一类：

- `our_attachment`：**投标人自己列出的、随标书提交的材料**（例：`3-4纳税证明`、`2.2ISO9001质量管理体系认证证书`、`社保缴纳记录：`、`（三）财务状况表或银行资信证明`）
- `requirement`：**采购人/招标文件提出的要求**（常含「须」「应提供」「评分」「评审」「证明材料：」等；例：`提供参选文件递交截止日前6个月内任意1个月的纳税证明`）
- `commitment`：**承诺函/声明函的正文**，复述资格条件（例：`具有良好商业信誉和健全的财务会计制度`、`有依法缴纳税收和社会保障资金的良好记录`）
- `heading`：**章节标题**（例：`（五）拟派项目实施团队（含社保证明）`、`1.2 实验仪器及软件`）
- `other`：以上都不是

**关键区分**：
- `our_attachment` 是**名词短语**，指一份具体材料；`commitment` 是**完整句子**，含「具有/承诺/保证」等谓词。
- `requirement` 是**对投标人的要求**，`our_attachment` 是**已附的材料**。二者形式可能很像，看有无「提供/须/应」等要求性谓词。
- 拿不准时**宁可标 other**，不要猜。

输入（每行格式 `编号<TAB>上文 │ 本行`；「上文」是**紧邻的上一行**，可能为空，仅用于消歧）：
%s

**注意**：判断的是「本行」；上文只用来理解本行在说什么。
例如本行是 `社保缴纳记录：`、上文是 `项目组成员-赵仕兰` → 这是**该人员的材料条目**，属 `our_attachment`；
若本行单独出现、上文无关联，才考虑 `other`。

输出 JSON 数组，每项 `{"id": <编号>, "kind": "<类别>"}`，**每行都要有**，不要解释。"""


def classify(lines: list[str]) -> dict[int, str]:
    from openai import OpenAI
    cli = OpenAI(base_url=_env.get("LLM_BASE_URL"), api_key=_env.get("LLM_API_KEY"),
                 timeout=180, max_retries=2)
    payload = "\n".join(f"{i}\t{redact(c['prev'])} │ {redact(c['text'])}"
                        for i, c in enumerate(lines) if isinstance(c, dict))
    resp = cli.chat.completions.create(
        model=_env.get("LLM_MODEL"), temperature=0,
        messages=[{"role": "user", "content": PROMPT % payload}],
        extra_body={"enable_thinking": False},
    )
    raw = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return {}
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return {int(x["id"]): x.get("kind", "other") for x in arr if "id" in x}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计候选，不调用 LLM")
    args = ap.parse_args()

    con = dbm.connect()
    docs = [dict(r) for r in con.execute(
        "SELECT d.document_id, d.relative_path, a.text FROM documents d "
        "JOIN parse_artifacts a ON a.canonical_document_id=d.canonical_document_id "
        "WHERE d.document_role IN ('our_response','final_signed') AND length(a.text)>60000")]

    allc = [(d, candidates(d["text"])) for d in docs]
    total = sum(len(c) for _, c in allc)
    print(f"候选 {total} 行 / {len([1 for _, c in allc if c])} 份文档")
    if args.dry_run:
        return 0

    results, kinds_total = [], Counter()
    t0 = time.time()
    for di, (d, cands) in enumerate(allc, 1):
        if not cands:
            continue
        for i in range(0, len(cands), BATCH):
            chunk = cands[i:i + BATCH]
            try:
                lab = classify(chunk)
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {d['relative_path'][-40:]} 批次失败: {type(exc).__name__}: {str(exc)[:60]}")
                continue
            for j, c in enumerate(chunk):
                k = lab.get(j, "other")
                kinds_total[k] += 1
                if k == "our_attachment":
                    results.append({"document_id": d["document_id"], "line": c["line"],
                                    "text": c["text"], "kind": k})
        print(f"  [{di}/{len(allc)}] {d['relative_path'].rsplit('/',1)[-1][:48]}  累计 our_attachment={len(results)}")

    con.close()
    print(f"\n分类完成，用时 {time.time()-t0:.0f}s")
    print("类别分布:", dict(kinds_total))
    io.open(CLEAN / "data" / "material_classified.json", "w", encoding="utf-8").write(
        json.dumps(results, ensure_ascii=False, indent=1))
    print(f"our_attachment（我方已附材料）{len(results)} 条 → data/material_classified.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
