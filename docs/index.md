# 文档索引（docs/index.md）

> 本仓库的**文档地图与权威来源登记**。新增/移动文档时同步更新此表。
> 建立 2026-09-15；**2026-09-16 目录重组**（类型分目录 + 状态列）。
> Agent 指令见仓库根 **`AGENTS.md`**（随仓库分发的权威）；工作区级拓扑见 `../../CLAUDE.md`。

## Start here

| 文档 | 状态 | 何时读 |
|---|---|---|
| `../AGENTS.md` | 权威 | 所有开发前必读：环境、命令、红线、代码地图 |
| `agent-handoff.md` | 检查点 | 跨会话恢复：Git 状态、未提交改动、下一步（**可替换，不是规格**；固定路径，不进子目录） |
| `specs/api.md` | active | 接入/联调：HTTP 接口契约与踩坑 |

## Plans（计划）

| 文档 | 状态 | 何时读 |
|---|---|---|
| `plans/active/PLAN-20260915-demo-feedback-issues.md` | **active** | 需求方试用反馈 5 条问题的**权威记录**：§6 需求 / §8 已定口径 / §9 实现与遗留 / §11 验证证据 / §12 状态 |
| `plans/legacy/MINIMAL_REBUILD_PLAN.md` | legacy（冻结） | 旧系统最小重建路线（§2 金额口径修订 / §10 执行记录 / §11A 门槛）。2026-09-16 自旧系统仓库逐字复制入仓 |
| `plans/legacy/MINIMAL_REBUILD_PLAN-DECISIONS.md` | legacy（冻结） | 同一路线的决策覆盖层（D1–D15，与基线冲突时以它为准） |

> 完结的计划移入 `plans/completed/`（目录待首个完结计划时再建）。
> ⚠️ **旧系统代码已移出工作区**：归档于 `C:\Users\hao.guo\Desktop\标书文库-旧系统归档\`（含全部 git 历史与 R0 数据快照）。本仓库文档**不得再引用工作区内的 `../bid-ai/`**。

## Specifications（规格）

| 文档 | 状态 | 何时读 |
|---|---|---|
| `specs/api.md` | active | HTTP 契约（端点、字段、错误语义、踩坑）。2026-09-16 已同步 `recognition` / `files` / `file_count` 与 §8 `tender-check` |

## Authorizations（外发授权，范围不可自行扩大）

| 文档 | 状态 | 覆盖范围 |
|---|---|---|
| `authorizations/ocr-authorization.md` | 生效 | `contract_evidence` 扫描件 OCR 外发（2026-09-10） |
| `authorizations/ocr-authorization-response-docs.md` | 生效 | `our_response`/`final_signed` 扫描件 OCR 外发（2026-09-11 扩大） |
| `authorizations/llm-generation-authorization.md` | 生效 | 方案生成正文外发（2026-09-11 建立，§9 首次真实生成记录） |
| `authorizations/llm-classification-authorization.md` | 生效 | 材料来源语义分类的 LLM 外发 |
| `authorizations/llm-intent-authorization.md` | 生效 | 查询意图识别（2026-09-16；**只发用户那一句查询**，本地优先、判不出才外发） |

> ⚠️ 新增数据源/角色须先取得用户书面授权并在此登记。

## Evaluations（评测）

| 文档 | 状态 | 何时读 |
|---|---|---|
| `evals/success5-precision10-clarification.md` | active | Success@5 / Precision@10 门槛口径（给需求方的确认请求） |
| `evals/archive/`（9 份） | archived | R5 留出集/结构化评测（`r5-*`×6）与 D1/D2a 阶段清单（`d1-*`、`d2a-*`×2）。**历史证据，只查不改**；其中的路径引用是归档时点的位置 |

## Business（给业务/需求方）

| 文档 | 状态 | 何时读 |
|---|---|---|
| `business/proposal-quality-review.md` | active | 方案质量人工评审指引与判定标准（评审须业务发起） |
| `business/demo-feedback-open-questions.md` | completed | 试用反馈 PLAN 的一页纸（三个口径已全部拍板） |
| `business/material-facts-feasibility.md` | completed | 材料事实抽取可行性结论 |
| `business/query-nl-mapping.md` | archived 性质 | 冻结 query 集的自然语言问法对照（2026-09-10 快照） |

## Operations

暂无独立 runbook。运行/回滚命令见 `../AGENTS.md` 与 `agent-handoff.md` §7。

## Incidents

暂无独立 BUG/RCA 文档（严重程度未达阈值）。缺陷以**回归测试 + 计划文档记录**承载。

## 新文档纪律（防再乱）

1. **类型决定目录**：计划→`plans/active/`、规格→`specs/`、授权→`authorizations/`、评测→`evals/`、业务→`business/`；根目录只留 `index.md` 与 `agent-handoff.md`。
2. **命名** `类型前缀-YYYYMMDD-主题.md`（如 `PLAN-20260915-*`）；阶段代号（r5/d1…）**不进文件名**。
3. **修订在原文改** + 文末「修订记录」一行；**不留 `-corrected` 副本**。
4. **生命周期**：阶段结束 → 评测移 `evals/archive/`、计划移 `plans/completed/`；归档件正文冻结。
5. **不进 docs**：一次性探查笔记（→`tmp/`）、真实投标正文（→永不落盘，见 `outputs/` 教训）、密钥。

## External references

`Z:\01 投标项目文件\{2025年,2026年}` —— 只读共享盘（唯一数据源，**禁写**）。
