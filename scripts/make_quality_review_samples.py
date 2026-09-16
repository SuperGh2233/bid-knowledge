"""生成「方案质量业务人工评审」的样本包。

用途：`docs/authorizations/llm-generation-authorization.md §7 未决` —— 方案草稿质量从未经过业务人工评审。
本脚本把评审前置做掉：按一组**覆盖能力面**的查询，现场生成草稿（走的正是生产端点函数），
落成 `outputs/proposal-quality-review/<ts>/` 下逐个样本目录。业务同事只需要**打开对照着评**。

**每个样本包含**
- `meta.json` —— 查询、模式、coverage、引用数、冲突、gaps、warnings（机器可判定项）
- `report.md` —— 产物正文（评审同事主要读这个）
- `response.json` —— **原始响应体**（要完整复现展示流程就看它；`report.md` 只是其中 markdown 那一项）

**样本设计**（7 个，覆盖不同能力面）
| # | 查询 | 模式 | 评什么 |
|---|---|---|---|
| 01 | 培训方案 | local | 单模块最典型形态 |
| 02 | 售后服务方案，必须包含服务周期和应急预案 | local | 点名多模块 + 「必须包含」解析 |
| 03 | 应急预案 | local | **时限冲突**：不自动择一、如实并列 |
| 04 | 项目风险识别与措施 | local | 曾靠扩词形才 9/9 的模块，证据是否真实对题 |
| 05 | 质量控制方案，必须包含质控要求，保密方案 | local | 双模块 + **未识别小节告警**（质控≠质量控制） |
| 06 | 项目管理与实施方案，必须包含进度计划和人员配置 | local | 大方案可读性 / 完整性 |
| 07 | 售后方案 | llm | **外发真实生成**（qwen3.7-flash，授权见 §9）+ KB 进提示词 |

产物含**真实投标正文** —— 只能落 `outputs/`（gitignore），评审记录表 `review_record.md`
由业务同事照着勾，改完留在同目录。

ES 不可达时 local 样本会失败（503），如实退出，不产出残缺包。
"""
from __future__ import annotations

import json
import platform
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pydantic import BaseModel


class GenerateRequest(BaseModel):
    query: str = ""
    modules: str = ""
    mode: str = "llm"
    constraints: str = ""


# ⚠️ 需求二只保留**模型生成**（2026-09-14 用户指令），本地拼装已从 API 下线
# （`mode=local` → 400）。因此本脚本的**每一个样本都会把真实投标正文外发**到公司网关
# （授权记录 docs/authorizations/llm-generation-authorization.md §9：用户已配置网关并开过真实生成）。
# 运行即外发 —— 脚本只做一次性确认，不另行授权。
SAMPLES = [
    ("01-培训方案", dict(query="培训方案")),
    ("02-售后-服务周期和应急预案",
     dict(query="售后服务方案，必须包含服务周期和应急预案")),
    ("03-应急预案-时限冲突", dict(query="应急预案")),
    ("04-项目风险识别与措施", dict(query="项目风险识别与措施")),
    ("05-质控要求-未识别告警",
     dict(query="质量控制方案，必须包含质控要求，保密方案")),
    ("06-项目管理-进度和人员",
     dict(query="项目管理与实施方案，必须包含进度计划和人员配置")),
    ("07-售后方案-双盲对照", dict(query="售后方案")),
]


def main() -> int:
    import argparse

    from app.api import proposal_generate

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要外发的查询清单，不发网关")
    args = parser.parse_args()

    if args.dry_run:
        print("以下是本脚本将**外发**给公司网关的查询（每个都带着对应真实投标正文）：")
        for name, req in SAMPLES:
            print(f"  {name}: {req['query']!r}")
        print("确认在 `docs/authorizations/llm-generation-authorization.md` 授权范围内后，去掉 --dry-run 再跑。")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = REPO / "outputs" / f"proposal-quality-review-{ts}"
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"样本目录：{out_root}")
    print("⚠️ 以下生成会把我方响应正文发往公司网关（qwen3.7-flash）；"
          "授权记录见 docs/authorizations/llm-generation-authorization.md §9。")

    # 空白评审记录表：业务同事照着勾（判定标准见 docs/business/proposal-quality-review.md）
    row = lambda name: f"{name:32s} ✓/✗   ✓/✗   ✓/✗   ✓/✗   ✓/✗   __ / 5   …\n"
    (out_root / "review_record.md").write_text(
        "# 方案质量业务评审记录（人工填）\n\n"
        "列：完整性 / 引用 / 数字 / 冲突 / 诚实 / 成文质量(1-5) / 备注。判定标准见 docs/business/proposal-quality-review.md。\n\n"
        "样本                            完整性  引用  数字  冲突  诚实  成文质量  备注\n"
        + "".join(row(f"{name}/") for name, _ in SAMPLES),
        encoding="utf-8")

    for name, req in SAMPLES:
        r = proposal_generate(GenerateRequest(**req))
        out = out_root / name
        out.mkdir(exist_ok=True)
        # 机器可判定项汇总成 meta（评审同事只看摘要，判定标准见 docs/business/proposal-quality-review.md）
        modules = [(m["module"], m["status"], len(m.get("evidence") or [])) for m in r.get("modules", [])]
        meta = {
            "query": req["query"], "mode": r.get("mode"),
            "modules": modules,
            "coverage": r.get("coverage"),
            "refs": (r.get("sections") or {}).get("references", None)
                    if isinstance(r.get("sections"), dict) else None,
            "warnings": [w.get("message") for w in r.get("warnings", [])],
            "gaps": [g for g in r.get("gaps", [])],
            "kb_used": r.get("kb_used"),
            "chars": len(r.get("markdown") or ""),
        }
        (out / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "report.md").write_text(
            (r.get("markdown") or f"[该样本无草稿产物] {json.dumps(r, ensure_ascii=False)[:800]}"),
            encoding="utf-8")
        # ⚠️ **原始响应体必须落盘**（2026-09-14 走查补）：先前只存摘要 meta.json，于是
        # `citations`（引用编号→文件）/`validation`/`scope_note`/`refs` 事后全无法复现 ——
        # 想拿真实结果走一遍**完整展示流程**时只能靠重建，等于验不了引用与校验那两段。
        # 含真实投标正文，只能留在 outputs/（已 gitignore）。
        (out / "response.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✓ {name}: modules={modules} coverage={r.get('coverage')} "
              f"warnings={len(meta['warnings'])} gaps={len(meta['gaps'])} kb={meta['kb_used']}")
    print("样本包生成完成（全部为模型起草产物，含外发）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())