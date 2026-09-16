"""R7 模块级方案生成 —— 运行入口（需求二）。**两条路，默认那条零外发。**

- **默认（不加参数）→ 本地抽取式装配**（`app/proposal.py::assemble_proposal`）：
  零外发、**不需要任何授权**。把 Evidence Pack 里的原文按小节组织成方案草稿，
  **每句话都是原文 + 引用编号**，「只用我方响应 / 可逐句溯源 / 不补造」**由构造保证**。
- **`--send` → 走模型生成**（外发，需 `PROPOSAL_GEN_ENABLED=true` + 授权记录
  `docs/authorizations/llm-generation-authorization.md`）。落盘的 `prompt.md` 就是待审阅的逐字内容。

两条路的产物用 `mode` 字段区分（`local_extractive` / 模型），**永不混淆**。

用法：
  # 本地装配（默认，零外发）
  python scripts/r7_generate.py --query "售后服务方案，必须包含服务周期和应急预案"

  # 模型生成（外发，需授权）
  PROPOSAL_GEN_ENABLED=true python scripts/r7_generate.py --query "..." --send

产物（`outputs/r7-<时间戳>/`）：
  evidence.json  证据包（去重后 3~5 条 + 状态 + 缺口说明）
  prompt.md      将发给模型的完整提示词（仅 --send 时用到）
  report.md      方案草稿 + 引用清单 + 冲突/缺口警告 + 校验结果
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.proposal import (MODULE_KEYWORDS, ProposalGenError, ProposalGenNotAuthorized,  # noqa: E402
                          assemble_proposal, build_evidence_packs, build_gen_prompt,
                          extract_required_sections, generate_proposal, packs_to_payload)


def main() -> int:
    ap = argparse.ArgumentParser(description="R7 模块级方案生成（默认只审阅，不外发）")
    ap.add_argument("--query", default="售后服务方案，必须包含服务周期和应急预案",
                    help="自然语言，如「售后服务方案，必须包含服务周期和应急预案」")
    ap.add_argument("--modules", default="", help="精确指定小节，逗号分隔（优先于 --query）")
    ap.add_argument("--constraints", default="", help="用户要求必须包含的内容")
    ap.add_argument("--db", default=os.environ.get("BID_AI_CLEAN_DB", str(BASE / "bid_ai_clean_reg.db")))
    ap.add_argument("--out", default="", help="产物目录（默认 outputs/r7-<时间戳>）")
    ap.add_argument("--send", action="store_true", help="走模型生成（外发，需 PROPOSAL_GEN_ENABLED + 授权）；不加则用本地抽取式装配")
    args = ap.parse_args()

    mods = [m.strip() for m in args.modules.split(",") if m.strip()] or \
        extract_required_sections(args.query)
    unknown = [m for m in mods if m not in MODULE_KEYWORDS]
    if unknown:
        print(f"未知方案小节：{unknown}\n可选：{list(MODULE_KEYWORDS)}")
        return 2
    if not mods:
        print("没有识别出任何方案小节。请写明，例如「售后服务方案」或「必须包含应急预案」。")
        return 2

    db = Path(args.db)
    if not db.exists():
        print(f"库不存在：{db}")
        return 2
    # 只读打开：本脚本**绝不写库**（检索层与生成层都不需要写）
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        packs = build_evidence_packs(con, mods)
    finally:
        con.close()
    payload = packs_to_payload(packs)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else BASE / "outputs" / f"r7-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    io.open(out / "evidence.json", "w", encoding="utf-8").write(
        json.dumps(payload, ensure_ascii=False, indent=1))
    prompt = build_gen_prompt(payload, args.constraints)
    io.open(out / "prompt.md", "w", encoding="utf-8").write(prompt)

    print(f"小节：{mods}")
    print(f"coverage：{payload['coverage']}")
    for m in payload["modules"]:
        print(f"  {m['module']:<14} {m['status']:<12} 标题命中={m['heading_hits']} "
              f"共={m['deduped']}（召回 {m['pool_size']}，角色拒 {m['role_rejected']}，"
              f"丢重复 {m['dropped_duplicates']}）")
        for n in m["notes"]:
            print(f"     注：{n}")
    print(f"\n产物目录：{out}")
    print(f"  evidence.json  证据包（{payload['coverage']}）")
    print(f"  prompt.md      将发给模型的完整提示词（{len(prompt)} 字）—— 仅 --send 时会用到，可先审阅")

    # **默认走本地抽取式装配**：零外发、不需授权，且「只用我方响应 / 每句可溯源 / 不补造」
    # 由构造保证（见 `app/proposal.py::assemble_proposal` 的说明）。
    # `--send` 才走模型（外发，需 PROPOSAL_GEN_ENABLED + 授权记录）。
    if not args.send:
        result = assemble_proposal(payload, args.constraints)
        print("\n[本地装配] 未调用任何网关、**没有任何外发**。")
    else:
        try:
            result = generate_proposal(payload, args.constraints)
        except ProposalGenNotAuthorized as exc:
            print(f"\n[拒绝] {exc}")
            return 3
        except ProposalGenError as exc:
            print(f"\n[失败] {exc}")
            return 4

    lines = [f"# 方案草稿（{stamp}）", "", f"小节：{mods}", ""]
    if result["validation"]:
        lines += ["## ⚠️ 校验未通过（必须逐条处理）", ""]
        lines += [f"- {p}" for p in result["validation"]] + [""]
    if result["warnings"]:
        lines += ["## ⚠️ 数值冲突（系统不自动择一）", ""]
        lines += [f"- {w['message']}" for w in result["warnings"]] + [""]
    if result["gaps"]:
        lines += ["## 证据不足的小节", ""]
        lines += [f"- {g['module']}（{g['status']}）" for g in result["gaps"]] + [""]
    lines += ["## 正文", "", result["markdown"], "", "## 引用来源", ""]
    lines += [f"- `{c['ref']}` {c.get('heading') or '（无标题）'} — {c.get('file_name')}"
              for c in result["citations"]]
    io.open(out / "report.md", "w", encoding="utf-8").write("\n".join(lines))

    print(f"\n已生成：{out / 'report.md'}")
    print(f"  引用 {len(result['citations'])} 条；校验问题 {len(result['validation'])} 条；"
          f"冲突告警 {len(result['warnings'])} 条；证据不足小节 {len(result['gaps'])} 个")
    print("**这是草稿，不是最终稿**：校验列出的问题必须逐条处理后再用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
