# Agent Handoff — bid-ai-clean

> 更新 **2026-09-17**（按本技能 §1–§13 重写；旧版见 `git show HEAD:docs/agent-handoff.md`）。
> 权威：仓库根 `AGENTS.md`（命令与红线，随仓库分发）、工作区级 `../CLAUDE.md`（两版本拓扑）、
> `docs/index.md`、§2 列出的文档。**冲突时以仓库与实测为准**；本文件只是可替换的检查点。

## 1. Current Goal

交付两个能力（**只这两个**，不得扩第三条主链路）：**需求一** 历史材料定位（自然语言 → 我方响应文件 + 内部业务记录）；**需求二** 模块级方案生成（带出处草稿）。

**当前无进行中的开发任务。** 最近两轮：① 需求方试用反馈 5 条的六轮整改（2026-09-15/16）；② **业绩清单进入检索 + 金额覆盖攻坚**（2026-09-16/17，明细见 §3）。

退出门槛（未回退）：Recall **92.3%** / 真实路径 **100%** / 金额条件 **100%**、误返 **0** / Success@5 100%（3 例）/ Precision@10 **93.3% 下界** / 必要小节覆盖 **8/8** / 引用覆盖 100%、竞品 0。

## 2. Linked Authoritative Documents

| 文档 | 控制什么 |
|---|---|
| `AGENTS.md`（仓库根） | 环境、命令、红线、代码地图、文档导航（`CLAUDE.md` 是其一行指针） |
| `docs/index.md` | 文档地图 + 状态列 + 「新文档纪律」5 条 |
| `docs/plans/active/PLAN-20260916-track-record-search.md` | **completed**。业绩清单检索与金额覆盖的权威记录：§5.2–5.6 六轮修订与**八类根因**、§8 口径（含 2026-09-16 金额筛选**改判**）、§11 验收终态、§12 三项候选**已结案** |
| `docs/plans/active/PLAN-20260915-demo-feedback-issues.md` | **active**。需求方 5 条反馈的权威记录 |
| `docs/specs/api.md` | HTTP 契约（§1A `recognition`、§2A 业绩清单段、§8 `tender-check`）|
| `docs/authorizations/` ×5 | 外发授权（OCR×2、方案生成、材料分类、意图识别），**范围不可自行扩大** |
| `docs/business/demo-feedback-open-questions.md` / `query-nl-mapping.md` | 给业务的一页纸 / 问法对照（§二 是页面示例的实测基线） |

## 3. Current Progress

**Completed + Verified（2026-09-17 实测）**
- 需求方 5 条反馈全部实施；意图识别层上线（本地优先 + LLM 兜底）。
- **业绩清单（响应文件内的历史合同声明）进入检索并通过金额攻坚**：`LEDGER-*` **29 → 408 条 / 51 份**，
  **有金额 388 条（95%）**，逐条反查证据原文 **0 条对不上**；与合同原件**同一列表** + 小标记区分；
  跨项目文件夹的同一份声明**只展示一次**（副本位置留在 `also_in`）。
- **口径改判（用户 2026-09-16）**：业绩行的**合同总额参与金额筛选**（改前实测：127 份合同按 5 档门槛比对，
  误返 0 份、漏召最多 2 份）。**硬约束不变**：业绩行永不进 `hits`、不进白名单；未达门槛只报数、
  金额未记载单列给出文件（**不静默丢结果**）。
- `pytest tests -q` → **298 passed**。
- 文档体系化：`AGENTS.md` 落仓、`docs/` 按类型重组、旧系统移出工作区归档（工作区 913M → 544M）。

**In progress**：无。
**Not started**：`refresh_catalog.bat` 计划任务（已问未定）；方案质量的**业务人工评审**（须业务发起）；OCR 混扫的**按页归属拆分**（独立课题）。
**Rejected / abandoned（勿重开）**：确定性规则替代 LLM 材料分类（召回 7.4%）；收款凭证两条补充通道（覆盖 1/93）；拿 OCR 凑覆盖率（命中 0）；**业绩清单第二步三项候选**（① 缺口 0 已闭环 ② 227 份"候选"实为报价/简历/偏离表，不是机会 ③ 业绩表原文不含合同号）。

## 4. Changes Made

本会话（2026-09-16/17）改动集中在**业绩清单链路**，按文件：

| 文件 | 改了什么 |
|---|---|
| `app/extract.py` | 业绩行抽取的六轮判据修订：认「万」/行内金额/合并列取格尾数字/当事人词表+7（实测词频）/`_column_index()` 按列名定位/**结构判据选项目列**（最长含中文格）/行缓冲遇新表头即收尾/行级可信性过滤/金额上限 1 亿/表头须有金额列**或**业绩内容列/`_cross_ref_amount()` 同文档合同清单补数（三种写法 + 采购人唯一性守卫）/`synced_partial` 受限放行（**允许 upsert、禁止删除**）/无金额原因标注 |
| `app/search.py` | `locate_track_records()`：业绩行检索、同一声明折叠（`also_in`）、**金额门槛（改判后）**、未入选两类去向（`excluded_below_amount` / `excluded_no_amount`） |
| `app/routes_search.py` | 三模块「项目业绩」增 `ledger_records`；合同检索增 `ledger` 段；`_ledger_for()` 门槛下沉 |
| `app/api.py` | `live_scope()` **合同只数 `CTL-*`**（原先整表计数，页脚出现"538 份合同"错误口径），业绩行单列 `ledger_records` |
| `static/{app.js,style.css,index.html}` | 业绩行与合同原件同一列表 + 小标记；原文限长（证据 300 字/卡片 160 字）；未入选去向折叠；常用问法示例 8 → 13 条（每条实测过） |
| `scripts/backfill_track_records.py`（新） | 全量重跑业绩抽取（写前备份、拒写正式库、逐状态统计）|
| `tests/`（新/改） | `test_track_records.py`、`test_ledger_guards.py`、`test_ledger_real_cases.py`（**用户报告案例的形态固化**）、`test_intent.py` 等 |
| `docs/` | 计划 §5.2–5.6 / §8 / §11 / §12、`api.md` §2A、`AGENTS.md`、`index.md`、各外发授权登记 |

## 5. Technical Decisions and Documentation Debt

- **金额口径（两步演变，都要知道）**：① 最初业绩金额**不参与**筛选（守「产品金额=明细行之和」红线）→
  ② 2026-09-16 用户改判**参与**，用**合同总额**（依据 §3 的实测）。诚实性要求不随口径变：每条标注
  「合同总额、非产品明细金额」；未达门槛只报数、未记载单列。
- **可疑解析的写入策略**：`synced_partial` = **允许 upsert、禁止删除**。删除仍只在完整解析时发生
  （保护"别在可疑解析下丢数据"的本意不变）。
- **文档债**：无阻塞项。低优先：`CHANGELOG.md` 仍未建（已有 tag `v1.0.0`/`v1.1`/`v1.1.1` 三个发布点）。

## 6. Contracts and Constraints

- **库**：活库 `bid_ai_clean_reg.db`；正式库 `bid_ai_clean.db` **禁写**（脚本硬拒）。
- **端点 12 个**（`/api/{status,ask,material-search,material-facts,scheme-search,three-modules,modules,module-kb,proposal-generate,tender-check,open}`）。
- **合同定位第一道闸** = `data/approved_documents.json`（136 份），**import 期载入 → 改它必须重启服务**。
- **业绩行**：走**独立闸**（角色 `our_response`/`final_signed`），**不进白名单**；`/api/status` 的 `queryable_contracts` **不含**它们。
- 资格门槛**顺序不得改**（`app/search.py`）；NAS 只读；外发授权 5 份不可自行扩大。

## 7. Tests and Verification

```bash
CONDA="C:\Users\hao.guo\AppData\Local\miniconda3\envs\langchain-dev\python.exe"
export BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db"
"$CONDA" -m pytest tests -q              # 298 passed
"$CONDA" scripts/backfill_track_records.py   # 业绩清单重跑（写前自动备份）
"$CONDA" scripts/eval_gold_recall.py         # Recall 92.3% / 误返 0
"$CONDA" -m app.api                          # → http://127.0.0.1:8000
```

- **服务当前状态（实测）**：PID **96880**，启动 2026-09-16 23:13 → 晚于最后一次代码改动，**跑的是当前代码**。
- ️ 改完代码**必须重启**并核对 PID/启动时间（旧进程照样返回 200 = 旧代码）。
- ⚠️ PATH 上的 `python` 不是本项目解释器。

## 8. Problems and Risks

### Confirmed problems（已知、未修）
- **OCR 混扫召回污染**：几份扫描 PDF 把合同/财报与响应文件扫进同一文件（新增章节中 17% 命中方案关键词、32% 含噪声词）。**数据源特性**，治本要「OCR 后按页归属拆分」（独立课题）。
- **无金额的 20 条业绩行**：8 条「表未设金额列」、11 条「表内有金额列但本行未取到」、1 条未标注 —— 已在结果里如实标注；再往上只能靠合同原件/OCR。
- **`华大` 查不到是数据事实**（该词在本语料里既非甲乙方也非产品文本）；**泛问「找仪器」**仍映射 `instrument`（存期间），有意不改（有测试钉住）。

### Unverified risks / assumptions
- 方案质量未经**业务人工评审**；需求方试读反馈未回收 —— 实质风险都在这两处。
- GitLab 令牌明文存于 `.git/config` 且在对话中出现过 → **建议轮换**。
- 「改私有」撤不回早前公开过的内容。

## 9. Next Actions

1. **业务相关（须用户/业务发起）**：把 `docs/business/demo-feedback-open-questions.md` 带给需求方复核；发起方案质量人工评审（指引 `docs/business/proposal-quality-review.md`）。
2. **打 tag（待用户点头）**：`v1.1.1` 之后已有 9 个提交（金额 95%、口径改判、文案清理、候选结案）—— 建议 `v1.1.2` 封版。
3. **可选**：补 `CHANGELOG.md`（已有三个 tag 发布点）；`refresh_catalog.bat` 挂计划任务；GitLab 令牌轮换。
4. **演示前**：`netstat -ano | grep :8000` 核 PID → 重启 → `GET /api/status` 应返回 `queryable_contracts=136`。

## 10. Do Not Repeat / Do Not Change

- **不重开**：业绩清单第二步三项候选（§3 已用证据结案）；确定性材料分类；收款凭证两通道；OCR 凑覆盖率；接合同台账（用户 2026-09-15 裁定不接）。
- **不改**：正式库；资格门槛**顺序**；合同金额口径（产品金额 = 同产品明细行之和，`total_amount` 不得替代 —— **业绩行是唯一例外，且已由用户改判并标注**）；`_FORMAT_RANK` 的 `mixed` 权重位。
- **不删**：ES `bid_chunks_v2`；`tests/test_contract_items.py::test_amount_unknown_stays_unknown_not_zero`。
- **仍适用的坑**：① 同一屏数字必须**同源**；② 前端不得用 Python 方法（`test_frontend_syntax.py` 钉住）；③ 改代码**按函数边界定位**，不能靠一行文本盲替换（本会话被咬过两次）；④ 报数字前先验口径（预演 ≠ 实跑）；⑤ **"函数对" ≠ "接线对"**（要把接线也测了）。

## 11. Minimum Recovery Context

1. 本文件 + `AGENTS.md` + `docs/index.md`
2. `docs/plans/active/PLAN-20260916-track-record-search.md`（业绩链路的全部决策与证据）
3. 最后活跃实现：`app/extract.py`、`app/search.py`、`app/routes_search.py`、`static/app.js`
4. `docs/specs/api.md`（契约）、`docs/authorizations/llm-intent-authorization.md`（外发边界）

## 12. Git State

- **分支**：`main`（工作分支 `feat/info-architecture-rework` 停在 `deda794`，已无独立价值）。
- **HEAD**：`9cd68e5`（`docs: 业绩清单第二步三项候选用证据结案（勿重做）`）。
- **tag**：`v1.0.0`（deda794）、`v1.1`（972d06e）、`v1.1.1`（8148343）、`minimal-rebuild-r1-20260907`。
- `git status --short`：**0 行**（干净）。
- **远端**（`git ls-remote` 一致）：`origin` = GitLab `ai-project/bid-ai`（HTTPS+令牌免交互）；`github` = `SuperGh2233/bid-knowledge`（**私有**）。
- **禁提交**：`.env`、`*.db`（含 `bid_ai_clean_reg.bak-*.db` 26 个/441MB）、`outputs/`（真实投标正文）、`tmp/`、`tmp_probe/`、`data/*.bak-*.json`、`*.log`。**别整体打包外发**（会带上 `.git/config` 里的令牌）。

## 13. Recovery Command

> **当前状态**：需求一/二退出门槛达标；业绩清单链路（含 95% 金额覆盖与口径改判）**已完成并结案**；
> **无进行中的开发任务**，下一步是业务侧动作或可选杂事（§9）。

`Invoke $resume-work in this repository, verify AGENTS.md, docs/index.md, linked authoritative documents, Git state, and docs/agent-handoff.md, then continue from Next Actions item 1.`