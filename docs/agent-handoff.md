# Agent Handoff — bid-ai-clean

> 更新 **2026-09-17（下午，第三轮：第二次需求对接实施）**（按本技能 §1–§13；旧版见
> `git show HEAD~1:docs/agent-handoff.md`）。
> 权威：仓库根 `AGENTS.md`、`docs/index.md`、§2 列出的文档。**冲突时以仓库与实测为准**。

## 1. Current Goal

交付两个能力（**只这两个**）：**需求一** 历史材料定位；**需求二** 模块级方案生成。

**当前任务**：**第二次需求对接的两条需求已全部实现并验收**（计划见
`docs/plans/active/PLAN-20260917-round2-feedback.md`）：
① 材料定位给「可复制正文段」+ 仪器空壳行治理 + 新增纳税社保总金额检索；
② 方案生成支持 `outline` **逐字标题结构约束**。
代码已提交 `faeebc8` 并推送两个远程；`pytest` **305 passed**；服务已重启（PID 见 §7）。

退出门槛（需求一累计）：Recall **37/37 = 100%**（明细 + 提及两组）／真实路径 100%／
金额条件 100%／覆盖率 8/8；本轮新增验收全部达标（见 §3 与计划 §8 验收总表）。
**待用户点头**：打 tag `v1.3`（CHANGELOG 条目已写好）。

## 2. Linked Authoritative Documents

| 文档 | 控制什么 |
|---|---|
| `AGENTS.md` | 环境、命令、红线（**服务端硬开关默认全 false**、外发授权 6 份、NAS 只读、正式库禁写） |
| `docs/index.md` | 文档地图 + 状态列 |
| `docs/plans/active/PLAN-20260917-round2-feedback.md` | **本轮任务的权威计划**：实测诊断 D1–D5 + 四项口径 + 五步实施 + 验收总表（✅ 全部完成，§8 有实测数字）|
| `docs/plans/active/PLAN-20260917-contract-product-mention.md` | 上一轮（正文提及组）的权威计划，**已收口冻结** |
| `docs/plans/active/PLAN-20260915-demo-feedback-issues.md` | 需求方**首次**试用 5 条反馈的权威记录 |
| `docs/evals/EVAL-20260917-gold-recall-regression.md` | Recall 复测报告；§7 记新基线 37/37=100% 与 G05 金标准瑕疵证据 |
| `docs/authorizations/llm-contract-mention-authorization.md` | 第 6 份授权，**暂缓启用**（本地规则已达同等召回） |
| `docs/specs/api.md` | HTTP 契约 —— ✅ 已同步本轮：§4（`content_snippet`/沉底排序/`finance_amount`）、§4A、§5（`outline`）|

## 3. Current Progress

**Completed + Verified（2026-09-17 实测，计划 §8 验收总表为权威）**
- **可复制正文段**（痛点主项）：`material-facts` / `three-modules` 每条材料行新增
  `content_snippet` + `snippet_source` + `snippet_missing` —— 按**材料自身标题**从响应文件正文
  裁原样段落。实测仪器清单 **636/645 带正文段**，9 条如实标注「正文里没找到该类材料段落」。
- **仪器空壳行沉底**：新增 `app/api.py::SHELL_ROW_SQL`（**与 `_annotate_fact_role` 判据等价**，
  回归测试钉住 1,986 行零错分）；实测仪器清单**0 条空壳行**排在有内容行之前（改前 211 条排最前）。
- **纳税社保总金额**：新增 fact_type `finance_amount` + `app/extract.py::find_voucher_total()`
  （保守判据：必须有税务/社保机构痕迹 + 合计锚点）；`scripts/backfill_finance_amounts.py`
  回填 reg 库 **29 条 / 19 份凭证**；查询映射「纳税社保总金额」→ `finance_amount`
  （**排在「社保」之前**，否则落到期间类）。实测挡掉 2 份打车发票（也有 `价税合计`、无机构痕迹）。
- **outline 标题约束**（需求2）：`POST /api/proposal-generate` 可选 `outline`（对象 / Markdown 文本）；
  提示词下发硬性结构块 + `GEN_SYSTEM` 规则 7 改写；`_validate_outline` **如实报漂移**
  （缺小标题 / 大标题不符 / 自造小节），容忍模型自加序号。**不传 outline → 行为与 v1.2 逐字节一致**。
- **前端**：卡片「复制这段」按钮（`navigator.clipboard`）+ CSV「可复制正文」列 +
  金额按元显示 + 标题结构输入框（含一键示例）+ `outline_used` 回显。
- `pytest tests -q` → **305 passed**（原 298 + 新 7）。
- 上一轮「正文提及」组仍在（Recall 37/37 = 100%、误返 0、零外发）—— 本轮未触碰。

**In progress**：无。
**Not started**：tag `v1.3`（**待用户点头**）；`PLAN-20260917-round2-feedback.md` §9 第二阶段
（表格结构化整理 / 金额汇总求和与门槛过滤 / 固定 outline 模板）——**本轮明确不做**。
**Rejected（勿重开）**：LLM 外发复核；位置规则（前 1/3）；「邻近词过滤无效」结论（口径算错）；
three-modules 业绩段加提及组（用户裁定暂不决定）。

## 4. Changes Made（本轮，已提交 `faeebc8`）

| 文件 | 改了什么 |
|---|---|
| `app/api.py` | `SNIPPET_ANCHORS` + `cut_snippet()` + `attach_snippets()`（可复制正文段）；`SHELL_ROW_SQL`（空壳行判据）；`_FACT_KW` 加 `finance_amount` 映射（**排在社保前**）；`_FINANCE_FACT_TYPES` 加 `finance_amount` |
| `app/extract.py` | `find_voucher_total()` + `extract_finance_amounts()` + `_FA_VOUCHER_MARKS`（凭证合计金额，保守判据） |
| `app/routes_search.py` | 两处 `ORDER BY` 用 `SHELL_ROW_SQL` 沉底；两处接 `attach_snippets`（**必须在 `con.close()` 前**，同既有教训） |
| `app/proposal.py` | `parse_outline()` / `format_outline_block()` / `_validate_outline()`；`build_gen_prompt`/`generate_proposal`/`validate_generation` 接 `outline`；`GEN_SYSTEM` 规则 7 改写；**给了 outline 时跳过模块名覆盖检查**（否则必然误报） |
| `app/routes_proposal.py` | `GenerateRequest.outline`；接线 `parse_outline`；响应加 `outline_used` |
| `scripts/backfill_finance_amounts.py` | **新增**：回填 `finance_amount`（只写 reg 库、改前备份、幂等、`--dry-run`）|
| `static/app.js` | `snippetBlock()` + `factValueText()`；卡片/CSV 接正文段；outline 输入与 `outline_used` 回显 |
| `static/index.html` / `style.css` | 标题结构输入框（details 折叠 + 一键示例）；`.snippet-*` / `.outline-box` 样式 |
| `tests/test_material_facts.py` | +4 条：锚点裁段、**空壳行 SQL 与 Python 判据等价性**、金额判据正反例、查询映射顺序 |
| `tests/test_proposal.py` | +3 条：outline 解析两形态、提示词注入、**漂移必报**且不误报 |
| `docs/specs/api.md` / `CHANGELOG.md` / 计划文档 | 契约同步（§4/§4A/§5）、v1.3 条目、计划 §8 补实测数字 |

## 5. Technical Decisions and Documentation Debt

**本轮决策（用户已拍板，见计划 §3）**：先做正文段落（结构化整理列第二阶段）／
金额抽取入库+可检索（汇总求和与门槛过滤不做）／outline 每次请求传入（不做固定模板）／
仪器按实测根因修（空壳行治理）。

**实施中新增的两条判据**（如实记录，计划 §修订记录也有）：
1. 金额判据比原计划更保守 —— 加「正文必须含税务/社保机构痕迹」，实测挡掉 2 份打车发票；
2. 给 outline 时校验器跳过「模块名是否出现」——用户标题与模块名**本就不同名**
   （「售后解决方案」vs「售后方案」），继续按模块名判必然误报；结构正确性由逐字校验承担。

**文档债**：**无**（api.md / CHANGELOG / 计划 / index 均已同步）。仅剩：tag `v1.3` 待用户点头。

## 6. Contracts and Constraints

- **端点 12 个不变**（本轮只在既有响应/请求里加字段：`content_snippet` / `snippet_source` /
  `snippet_missing` / `outline` / `outline_used`）→ **只读契约未变**。
- **`fact_type` 新合法值 `finance_amount`**（`fact_value` = 金额字符串，元）。
- **数据变更只走离线脚本**：`scripts/backfill_finance_amounts.py`（只写 `bid_ai_clean_reg.db`、
  改前自动备份、幂等、正式库一律拒绝）。服务仍**纯只读**。
- **红线不变**：产品金额 = 同产品明细行之和；`contracts.total_amount` 不得参与产品金额判断；
  方案生成证据只允许 `our_response`/`final_signed`；四个外发开关默认全 false。
- 外发：**6 份授权**，第 6 份暂缓启用；**本轮零外发**（裁段与金额抽取全本地；
  `outline` 是用户敲的标题文本，仍在既有生成授权内）。

## 7. Tests and Verification

```bash
CONDA="C:\Users\hao.guo\AppData\Local\miniconda3\envs\langchain-dev\python.exe"
export BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db"
"$CONDA" -m pytest tests -q            # 305 passed（本轮实测）
"$CONDA" -m app.api                    # → http://127.0.0.1:8000
"$CONDA" scripts/backfill_finance_amounts.py --dry-run   # 金额回填预览（零写库）
node --check static/app.js             # 前端语法（有护栏测试，但手改后先自查更快）
```
- **服务**：PID **86272** 在跑（含本轮新代码；旧 PID 102300 已 kill）。
  `GET /api/status` → `合同 136 / 可查 136 / 业绩行 408`。
- **实测口径（本轮）**：仪器清单 645 条中 0 条空壳行排前、636 条带正文段；
  「纳税社保总金额」返回 29 条；概览卡 136/1283/645 与点进去的 `total_available` 同源。
- **前端改动后需 Ctrl+F5 一次**（静态文件已加 `no-cache`，但浏览器手里那份旧 JS 要硬刷丢掉）。
- ⚠️ 改代码必须重启并核 PID/启动时间（旧进程照样返回 200 = 旧代码）。

## 8. Problems and Risks

### Confirmed problems
- **无**（本轮发现的三个根因都已修 + 有回归测试；「打车发票被当纳税金额」在实现期就挡掉了）。

### Unverified risks / assumptions
- **金额覆盖面窄且如实**：29 条 / 19 份凭证 —— 因为**社保缴费记录表没有合计行**（该表是明细，
  各险种不可加总）。这是**刻意不猜**的结果，不是 bug；若业务要「某期间社保总额」，
  需先定义口径（按险种分别汇总？只算单位缴纳？）——**属第二阶段，用户未要求**。
- `SNIPPET_ANCHORS` 的锚点是**手写词表**（与「手写词表会漂移」的历史教训同源）。
  实测覆盖 636/645；若发现漏挡，**优先改成按材料段落结构判定，而不是往表里加词**。
- `cut_snippet` 是**朴素 `str.find` + 窗口裁切**（`ponytail:` 标注：未做索引/缓存）。
  实测单次查询（≤1000 行）耗时正常；若将来明显变慢再议缓存。
- `/api/three-modules` 业绩段仍未加提及组（用户裁定暂不决定，勿主动开工）。

## 9. Next Actions

1. **打 tag `v1.3`**（CHANGELOG 条目已写好；**待用户点头**）：
   `git tag v1.3 && git push origin v1.3 && git push github v1.3`
2. **等用户/需求方验收反馈**：本轮四项都是「需求方直接提的痛点」，
   建议请需求方实机确认（尤其「可复制正文段」的措辞与粒度是否合用）。
   实机前提醒：**Ctrl+F5 一次**。
3. **第二阶段（用户未要求，勿主动开工）**：表格结构化整理 / 金额按期间汇总 /
   金额门槛过滤 / 固定 outline 模板 —— 见计划 §9。

## 10. Do Not Repeat / Do Not Change

- **不重开**：LLM 外发复核（授权已标暂缓，代码无触发路径）；位置规则（前 1/3）；
  「邻近词过滤无效」结论（我口径算错）；three-modules 业绩段加提及组（用户裁定暂不决定）。
- **不改**：正式库；资格门槛**顺序**；产品金额口径；`_FORMAT_RANK` 的 `mixed` 权重位；
  提及组不得混进 `hits`；**`SHELL_ROW_SQL` 与 `_annotate_fact_role` 必须保持等价**（有测试）；
  **金额判据不得放松成「扫全文找数字」或「按明细求和」**。
- **不删**：ES `bid_chunks_v2`；`tests/test_contract_items.py::test_amount_unknown_stays_unknown_not_zero`。
- **需新授权才能做**：任何把合同/响应正文送往外发网关的动作（第 6 份授权明确「用户指示后方可执行」）。

## 11. Minimum Recovery Context

1. 本文件 + `AGENTS.md` + `docs/index.md`
2. `docs/plans/active/PLAN-20260917-round2-feedback.md`（**本轮权威计划**：诊断 D1–D5、四项口径、
   五步实施、§8 验收实测数字、§9 第二阶段）
3. 本轮实现：`app/api.py`（`cut_snippet`/`attach_snippets`/`SHELL_ROW_SQL`）、
   `app/extract.py::find_voucher_total`、`app/proposal.py`（`parse_outline`/`_validate_outline`）、
   `app/routes_search.py`、`scripts/backfill_finance_amounts.py`
4. `docs/specs/api.md` §4 / §4A / §5（本轮新增字段与参数的契约）
5. 上一轮（未触碰，仅供对照）：`PLAN-20260917-contract-product-mention.md`

## 12. Git State

- **分支** `main`；**HEAD** `faeebc8`（本轮实现，**已推 origin + github**）。
- **工作区**：干净（除禁提交项）。
- **tag**：`v1.0.0` / `v1.1` / `v1.1.1` / `v1.1.2` / `v1.2` / `minimal-rebuild-r1-20260907`；
  **`v1.3` 待打**（§9 第 1 条）。
- **禁提交**：`.env`、`*.db`（含 `*.bak-*.db` 备份）、`outputs/`（真实投标正文）、`tmp/`、`*.log`。
  ⚠️ **备份文件命名必须命中 `.gitignore` 的 `*.bak-*.db`**：写成 `<name>.db.bak-<标签>` 会
  逃过忽略规则（本轮实测踩到并已修脚本，见 `backfill_finance_amounts.py` 注释）。
- **`tmp/` 下有本轮 13 个探针脚本**（`probe_finance_amounts*.py` / `probe_snippet*.py` /
  `probe_shell_*.py` / `probe_fin_*.py`）—— 一次性产物，不入库；
  其中 `probe_fin_amt_impl.py` 是「29 条」这个数字的来源，`probe_snippet_strong.py` 是 636/645 的来源。

## 13. Recovery Command

> **当前状态**：第二次需求对接的两条需求**已全部实现、验收并推送**（`faeebc8`；pytest 305 passed；
> 服务 PID 86272 含新代码）。剩余动作只有：**打 tag `v1.3`（待用户点头）+ 请需求方实机验收**。
> 第二阶段（表格结构化 / 金额汇总 / 固定模板）用户未要求，**勿主动开工**。

`Invoke $resume-work in this repository, verify AGENTS.md, docs/index.md, linked authoritative documents, Git state, and docs/agent-handoff.md, then continue from Next Actions item 1.`