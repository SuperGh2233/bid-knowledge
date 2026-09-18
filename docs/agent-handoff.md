# Agent Handoff — bid-ai-clean

> 更新 **2026-09-18（需求方第三轮实测反馈：三个 bug + 输出结构从查询推）**
> （按本技能 §1–§13；旧版见 `git show HEAD~1:docs/agent-handoff.md`）。
> 权威：仓库根 `AGENTS.md`、`docs/index.md`、§2 列出的文档。**冲突时以仓库与实测为准**。

## 1. Current Goal

交付两个能力（**只这两个**）：**需求一** 历史材料定位；**需求二** 模块级方案生成。

**当前任务**：**需求方第三轮实测反馈已全部修复并验收**（2026-09-18）：
① 「25年的社保」期间条件被静默丢弃（正则只认 4 位年份）→ 现 913 条 → **35 条**；
② 解析不出条件时**落回全表**（`Xenium` 返回 2015 条）→ 现恒空 + 明确说明；
③ 「期间未提取到」用在**没有期间概念的五类**上（556 行）→ 现按类别给说法；
④ **输出结构从 query 推**（需求方新要求）：大标题 + 小标题从他那句话里拆，
   模型逐字照此输出、不写结构外的内容。
代码已提交 `a5a519e`/`fc7d81f`/`e56e378`/`9da3ecf` 并推送两个远程；`pytest` **313 passed**。

**⚠️ 我自己造成的事故（已处置，留档）**：为读 `extract_three_modules.py` 里的纯函数而
`importlib` 执行了整个文件 —— 该脚本**没有 `__main__` 守卫**，模块级直接 connect + 写库，
于是重跑了一遍抽取。**逐类型比对证明幂等、无数据损坏**（10 类计数逐一相同），
正式库 `bid_ai_clean.db` 未被触碰（脚本硬编码写 reg 库）。**根因已修**：
4 个同类脚本全部加了 `_main()` + `__main__` 守卫，并验证 import 不再写库。

退出门槛（需求一累计）：Recall **37/37 = 100%**（明细 + 提及两组）／真实路径 100%／
金额条件 100%／覆盖率 8/8；本轮新增验收全部达标（见 §3 与计划 §8 验收总表）。
**tag `v1.3` 已打并推送两个远程**（打在 `fdab772`）。**`v1.4` 待打**（CHANGELOG 条目已写好）。

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

**第三轮修复（2026-09-18 实测）**
- **「25年的社保」**：`parse_fact_query` 的正则只认 4 位年份 → 「25年」的期间条件**被静默丢弃**，
  退化成搜「社保」（913 条）。现认两位数（`25`→`2025`，`(?<!\d)` 挡 `125年`）；
  实测 **913 → 35 条**，与「2025年的社保」逐字段一致。查询自带期间时同步收窄
  （「期间未提取到」的行不再混进「某月的社保」）。
- **解析不出条件不得落回全表**：`Xenium` / `型号` / 任意生词原先都返回 **2015 条全表**
  （= `material_facts` 总数），页面看着像「查到了很多」。现恒空 + `scope_note` 明确说
  「没解析出条件、没有执行筛选」并给可用问法。**违反「拒绝优于静默」红线，属本轮最危险的一条。**
- **「期间未提取到」按类别给说法**：`instrument` / `invoice` / `qualification` /
  `purchase_contract` / `instrument_photo` **五类共 556 行 `fact_value` 非空数 = 0**
  （存的是「存在性」）。现 `VALUE_KIND` 分四族给词；CSV 表头「期间」→「期间/型号」。
  另：无期间问法时**有值行排前**（两个端点都改；财务社保实测 208 条有值行全落前 208 位）。
- **输出结构从 query 推**（需求方新要求）：`plan_sections()` —— 大标题取原句里**最早出现**的
  模块、按**用户措辞长形**（`售后服务方案`）；小标题来自「必须包含X」子句切分 + 句外点名的模块；
  挂不上模块的**保留**并归到大标题模块、标 `module_inferred`。
  `build_evidence_packs` 加 `section_map`（自造小节名按归属模块召回）；响应加
  `outline_source` / `title_planned` / `sections_planned`。
  ⚠️ 修一处**静默丢小节**：原告警按整片段判「已认出」→ 片段里的「服务周期」被连带跳过、无提示。
- **脚本执行守卫**（事故根因）：4 个脚本原先 `import` 即写库，现全部 `_main()` + `__main__`。

**第二轮交付（2026-09-17 实测，计划 §8 验收总表为权威）**
- **可复制正文段**（痛点主项）：`material-facts` / `three-modules` 每条材料行新增
  `content_snippet` + `snippet_source` + `snippet_missing` —— 按**材料自身标题**从响应文件正文
  裁原样段落。实测仪器清单 **636/645 带正文段**，9 条如实标注「正文里没找到该类材料段落」。
- **仪器空壳行沉底**：新增 `app/api.py::SHELL_ROW_SQL`（**与 `_annotate_fact_role` 判据等价**，
  回归测试钉住 1,986 行零错分）；实测仪器清单有内容行 176 条**全部排在**空壳行之前
  （第一条空壳行在第 176 位；改前 211 条空壳行排最前）。
- **纳税社保总金额**：新增 fact_type `finance_amount` + `app/extract.py::find_voucher_total()`
  （保守判据：必须有税务/社保机构痕迹 + 合计锚点）；`scripts/backfill_finance_amounts.py`
  回填 reg 库 **29 条 / 19 份凭证**；查询映射「纳税社保总金额」→ `finance_amount`
  （**排在「社保」之前**，否则落到期间类）。实测挡掉 2 份打车发票（也有 `价税合计`、无机构痕迹）。
- **outline 标题约束**（需求2）：`POST /api/proposal-generate` 可选 `outline`（对象 / Markdown 文本）；
  提示词下发硬性结构块 + `GEN_SYSTEM` 规则 7 改写；`_validate_outline` **如实报漂移**
  （缺小标题 / 大标题不符 / 自造小节），容忍模型自加序号。**不传 outline → 行为与 v1.2 逐字节一致**。
- **前端**：卡片「复制这段」按钮（`navigator.clipboard`）+ CSV「可复制正文」列 +
  金额按元显示 + 标题结构输入框（含一键示例）+ `outline_used` 回显。
- `pytest tests -q` → **313 passed**（第二轮 298→308；第三轮 308→313）。
- 上一轮「正文提及」组仍在（Recall 37/37 = 100%、误返 0、零外发）—— 本轮未触碰。

**实施期自查发现并修掉（`0e4daf4`）**
- **说明长文的 Markdown 星号上屏**：`scope_note` / `mention_note` / `filter_note` / `amount_note`
  这些服务端下发的说明文里用了 `**强调**`，但渲染位点走的是 `esc()` → 页面上原样显示星号。
  改用项目既有的 `inline()`（先 esc 再转 `<strong>`，无 XSS）；JS 字面量里那些不经过渲染器的
  `**` 一并去掉。**新增护栏测试**：说明文不得走 `esc()`，且 `inline()` 必须先转义后替换。
- **`amount_note` 补全**：`finance_amount` 行原先没有口径说明 —— 与业绩行同一诚实性原则，
  凭证合计数字必须写清「非合同金额、不参与金额筛选」（否则会被读成合同金额）。
- **两个端点标注分叉**（`5944d0a`）：`/api/three-modules` 原先*就地重写*了一遍角色分层，
  与 `material-facts` 的 `_annotate_fact_role` 分叉 → ① **645/645** 条仪器行 `evidence_text`
  带 `[our_response]` 内部枚举上屏；② `finance_amount` 的 `amount_note` 整段丢失。
  现共用同一实现；实测前缀行 **645 → 0**，`amount_note` 恢复，空壳行 469 条里 **467 条**
  已由 `content_snippet` 补上可复制正文。**新增跨端点一致性回归测试**。

**In progress**：无。
**Not started**：`PLAN-20260917-round2-feedback.md` §9 第二阶段
（表格结构化整理 / 金额汇总求和与门槛过滤 / 固定 outline 模板）——**本轮明确不做**。
**Rejected（勿重开）**：LLM 外发复核；位置规则（前 1/3）；「邻近词过滤无效」结论（口径算错）；
three-modules 业绩段加提及组（用户裁定暂不决定）。

## 4. Changes Made（本轮，已提交 `faeebc8` + `0e4daf4`）

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

**文档债**：**无**（api.md / CHANGELOG / 计划 / index / 交接均已同步）。

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
"$CONDA" -m pytest tests -q            # 313 passed（本轮实测）
"$CONDA" -m app.api                    # → http://127.0.0.1:8000
"$CONDA" scripts/backfill_finance_amounts.py --dry-run   # 金额回填预览（零写库）
node --check static/app.js             # 前端语法（有护栏测试，但手改后先自查更快）
```
- **服务**：PID **46612** 在跑（含本轮全部新代码；旧进程已按 §7 教训逐个 kill）。
  `GET /api/status` → `合同 136 / 可查 136 / 业绩行 408`。
- **实测口径（本轮）**：仪器清单 645 条中，有内容行 176 条全排在空壳行之前、636 条带正文段
  （空壳 469 条里 467 条已补上正文段）；
  「纳税社保总金额」返回 29 条；概览卡 136/1283/645 与点进去的 `total_available` 同源。
- **前端改动后需 Ctrl+F5 一次**（静态文件已加 `no-cache`，但浏览器手里那份旧 JS 要硬刷丢掉）。
- ⚠️ 改代码必须重启并核 PID/启动时间（旧进程照样返回 200 = 旧代码）。

## 8. Problems and Risks

### Confirmed problems
- **无**（本轮发现的三个根因都已修 + 有回归测试；「打车发票被当纳税金额」在实现期就挡掉了）。

### Unverified risks / assumptions
- **金额覆盖面**：29 条 / 19 份凭证 —— 因为**社保缴费记录表没有合计行**（该表是明细，
  各险种不可加总）。这是**刻意不猜**的结果，不是 bug。
  ✅ **用户 2026-09-18 已确认「维持现状」**：不要为凑条数放松判据。
  若业务将来要「某期间社保总额」，需先定义口径（按险种分别汇总？只算单位缴纳？）——属第二阶段。
- `SNIPPET_ANCHORS` 的锚点是**手写词表**（与「手写词表会漂移」的历史教训同源）。
  实测覆盖 636/645；若发现漏挡，**优先改成按材料段落结构判定，而不是往表里加词**。
- `cut_snippet` 是**朴素 `str.find` + 窗口裁切**（`ponytail:` 标注：未做索引/缓存）。
  实测单次查询（≤1000 行）耗时正常；若将来明显变慢再议缓存。
- `/api/three-modules` 业绩段仍未加提及组（用户裁定暂不决定，勿主动开工）。

## 9. Next Actions

1. **打 tag `v1.4`**（CHANGELOG 条目已写好；按上一轮惯例，用户点头后打）：
   `git tag v1.4 && git push origin v1.4 && git push github v1.4`
2. **请需求方实机复核第三轮四项**（尤其新的输出结构是否符合他的预期）：
   · 「25年的社保」应返回 35 条、首页不再有「期间未提取到」；
   · 查仪器时不再显示「期间未提取到」，改显示型号或「本类只表示有仪器材料」；
   · `Xenium` 不再返回 2015 条全表，而是明确说「没解析出条件」；
   · 生成「售后服务方案，必须包含服务周期和应急预案」→ 大标题「售后服务方案」+
     两个小标题，且不写结构外内容。
   实机前提醒：**Ctrl+F5 一次**。
3. **遗留（用户未要求，勿主动开工）**：
   · **Xenium 仪器查不到**（第三轮报告里需求方提的「仪器定位有问题」的深挖项）——
     根因链已诊断清楚（四个断点：路由档位 / 抽取门 `kind_of` 单标签把社保放首位 /
     句式只认「N台<名字>」 / 数据面 0 条 Xenium 型号），修法涉及**重跑抽取**，
     需单独提计划与用户确认（尤其是「仪器名抽取要不要放开 kind 门」这个口径）。
   · 计划 §9 第二阶段（表格结构化 / 金额汇总 / 固定模板）。

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

- **分支** `main`；**HEAD = 本文件最后一次提交**（⚠️ 不写死 hash：写下去的瞬间它就变了，
  这条本身也把 HEAD 往前推一格）。**用 `git log --oneline -1` 现场核对**，并用
  `git ls-remote origin main` / `git ls-remote github main` 确认双远程与本地一致。
  本轮关键提交：`faeebc8` 实现 · `0e4daf4` 展示修复 · `5944d0a` 两端点标注统一 + 文档若干。
- **工作区**：干净（除禁提交项）。
- **tag**：`v1.0.0` / `v1.1` / `v1.1.1` / `v1.1.2` / `v1.2` / **`v1.3`（本轮，打在 `fdab772`）** /
  `minimal-rebuild-r1-20260907`。**两远程各 10 个 tag，`v1.3` 均已推送。**
- **禁提交**：`.env`、`*.db`（含 `*.bak-*.db` 备份）、`outputs/`（真实投标正文）、`tmp/`、`*.log`。
  ⚠️ **备份文件命名必须命中 `.gitignore` 的 `*.bak-*.db`**：写成 `<name>.db.bak-<标签>` 会
  逃过忽略规则（本轮实测踩到并已修脚本，见 `backfill_finance_amounts.py` 注释）。
- **`tmp/` 下有本轮 13 个探针脚本**（`probe_finance_amounts*.py` / `probe_snippet*.py` /
  `probe_shell_*.py` / `probe_fin_*.py`）—— 一次性产物，不入库；
  其中 `probe_fin_amt_impl.py` 是「29 条」这个数字的来源，`probe_snippet_strong.py` 是 636/645 的来源。

## 13. Recovery Command

> **当前状态**：第二次需求对接的两条需求**已全部实现、验收并推送**（双远程与本地 HEAD 一致；
> pytest 308 passed；服务 PID 64672 含新代码）；**tag `v1.3` 已打，双远程均已推送**。
> 剩余动作：**请需求方实机验收**。
> 第二阶段（表格结构化 / 金额汇总 / 固定模板）用户未要求，**勿主动开工**。

`Invoke $resume-work in this repository, verify AGENTS.md, docs/index.md, linked authoritative documents, Git state, and docs/agent-handoff.md, then continue from Next Actions item 1.`