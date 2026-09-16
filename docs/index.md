# 文档索引（docs/index.md）

> 本仓库的**文档地图与权威来源登记**。新增/移动文档时同步更新此表。
> 建立 2026-09-15。Agent 指令见仓库根 `CLAUDE.md`（**权威**）与父级 `../CLAUDE.md`。

## Start here

| 文档 | 何时读 |
|---|---|
| `../CLAUDE.md` | 所有开发前必读：架构、红线、数据模型 |
| `agent-handoff.md` | 跨会话恢复：当前 Git 状态、未提交改动、下一步动作（**可替换的检查点，不是规格**） |
| `api.md` | 接入/联调：HTTP 接口契约与踩坑 |

## Active requirements and plans

| 文档 | 状态 | 何时读 |
|---|---|---|
| `plans/active/PLAN-20260915-demo-feedback-issues.md` | active | 需求方首次试用反馈的 5 条问题（需求一检索 4 条 + 需求二方案 1 条），含实测证据与待确认口径。**权威定义** |
| `demo-feedback-open-questions.md` | — | 上述 PLAN 的**一页纸**版本（会前对齐用）；仅三个待拍板问题 + 已修项 |

## Specifications

| 文档 | 何时读 |
|---|---|
| `api.md` | HTTP 契约（端点、字段、错误语义、踩坑） |
| `success5-precision10-clarification.md` | Success@5 / Precision@10 门槛口径澄清 |

## Business scope and evidence

| 文档 | 何时读 |
|---|---|
| `proposal-quality-review.md` | 方案质量业务人工评审指引与判定标准 |
| `material-facts-feasibility.md` | 材料事实抽取可行性 |
| `query-nl-mapping.md` | 冻结 query 集的自然语言问法对照（2026-09-10 快照） |
| `d1-checklist.md` / `d2a-preregistration.md` / `d2a-report.md` | D1/D2a 阶段清单与结果 |

## External source boundaries（外发授权）

| 文档 | 覆盖范围 |
|---|---|
| `ocr-authorization.md` | `contract_evidence` 扫描件 OCR 外发（2026-09-10） |
| `ocr-authorization-response-docs.md` | `our_response`/`final_signed` 扫描件 OCR 外发（2026-09-11 扩大） |
| `llm-generation-authorization.md` | 方案生成正文外发（2026-09-11 建立，§9 首次真实生成） |
| `llm-classification-authorization.md` | 材料来源语义分类的 LLM 外发 |
| `llm-intent-authorization.md` | **查询意图识别**的 LLM 外发（2026-09-16；只发**用户那一句查询**，不含文档正文）|

> ⚠️ **三份（+1）授权范围不可自行扩大**；新增数据源/角色须先确认。

## Evaluations（测评记录）

| 文档 | 何时读 |
|---|---|
| `r5-holdout-eval.md` / `r5-holdout-eval-corrected.md` / `r5-holdout-fn-fp-split.md` | R5 留出集评测与误判拆分 |
| `r5-structural-eval.md` / `r5-structural-filter-review.md` | 结构化抽取评测 |
| `r5-taxonomy-proposal.md` | 分类体系提案 |

## Operations

暂无独立 runbook。运行/回滚命令见父级 `CLAUDE.md` 与 `agent-handoff.md` §6。

## Incidents and resolved plans

暂无独立 BUG/RCA 文档（严重程度未达阈值）。缺陷以**回归测试 + 计划表记录**承载。

## External references

`Z:\01 投标项目文件\{2025年,2026年}` —— 只读共享盘（唯一数据源，**禁写**）。
