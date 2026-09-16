# MINIMAL_REBUILD_PLAN Decisions

> **副本说明（2026-09-16）**：本文件原属旧系统仓库 `bid-ai/`，2026-09-16 随旧系统移出工作区而**复制**进本仓库
> （正文逐字未改）。旧系统代码与数据快照现归档于 `C:\Users\hao.guo\Desktop\标书文库-旧系统归档\`；
> 基线文档在本仓库的位置是 `docs/plans/legacy/MINIMAL_REBUILD_PLAN.md`。**冻结历史文档，正文不再修改。**

## 0. 文档定位

- 基线文档：`MINIMAL_REBUILD_PLAN.md`
- 本文：对基线方案的增量决策与最终裁决（D1–D15）
- **优先级：当 `MINIMAL_REBUILD_PLAN.md` 与本文冲突时，以本文为准**
- 决策范围：D1–D15；本附录与基线合并生成 `MINIMAL_REBUILD_PLAN-v2.md` 后方可作为唯一执行版本

---

## 1. Decision Summary

| # | 决策 | 结论 | 影响面 |
|---|---|---|---|
| D1 | 项目范围 | **A**：2025/2026 下、**非"非标书/内部资料/系统临时"** 的项目全部登记；比选/询比/磋商/调研/报名等按其是否有真实响应材料纳入 | 3.1、R2-01、R9-01、所有 project_type=标书 硬过滤描述 |
| D2 | contract_evidence 是否解析 | **A**：**解析 + 结构化提取**（编号/甲乙方/日期/总额/产品服务明细/明细金额），进入 contracts/contract_items，**不进方案章节向量索引**；是"历史合同定位"一等证据来源；**不改角色**（仍是 contract_evidence，不是我方响应） | 3.2、4.1、R3/R4/R6、验收集范围 |
| D3 | 解析缓存机制 | **A**：**DB 内内容去重即缓存**（`sha256→canonical_document_id`）；同内容只解析一次、副本多路径；不建 data/parse-cache 目录；临时 OCR 文件任务后删除 | 4.2（删除"本地解析缓存"）、R3-06、5.1 |
| D4 | 响应材料包资格 | **A**：只有**实际存在确认 our_response/final_signed** 的项目才建立响应包；报名/保证金/邮件/报价沟通只登记不形成包；调研/比选/磋商项目有真响应则纳入 | 3.3、验收口径（Recall 分母=人工确认项目，Precision 分母=系统判定响应包） |
| D5 | 我方合同归属 | **A**：正文乙方优先接管**合同记录归属**（不接管文件角色）；`path_vendor` 仅审计；`contract_vendor/vendor_scope/vendor_evidence/vendor_conflict`；462 份只抽样核验 | 5.2 新增字段、合同过滤语义、D10 Vendor Gold Set |
| D6 | 新库播种顺序 | **A**：三阶段（gold → 确认 seed → native target → scanned target）按 `seed_priority.csv` 优先播种;旧库信息只决定优先序,不做新库角色来源 | R0/R2/R3/R9 修订 |
| D7 | 验收集 | **A**：迁移旧 229 条(0-disputed 冻结) + 补财务社保/仪器/方案小类 10–20 条 → 30–50 条；旧合同行不再反向改变 | R0-05~07 |
| D8 | 非标书范围 | **A**：默认排除 + 高价值角色例外（contract_evidence/资质/签章）纳入；`scope_status/scope_exception/inclusion_reason`；不删 Gold 正例 | 5.1 新字段、评估按 project_type+document_role 分层 |
| D9 | 产品金额口径 | **A+**：`同一合同内同一标准化产品下、去重后叶子 detail 行 line_amount 之和`；row_type=detail/product_subtotal/contract_total/header/note；subtotal 仅显式归属产品时才用；**contract total 永不作产品金额 fallback**；8 条回归 + 金额 Precision/Recall + G01/03/05 重跑 | 5.3、R4-03、金额过滤查询 |
| D10 | 字段验证语义 | **A**：双层验证（Extraction Grounding + Source Fidelity）；native/mixed 字段须定位回原生 evidence span，scanned/mixed-OCR 字段须定位回 OCR span 并记 evidence_origin=ocr_text/provider/version/page；462 份**全部人工核验**并固化 `vendor_extraction_gold_v1`；verification 六态(native_exact/native_normalized/ocr_grounded/ocr_ambiguous/manual_verified/rejected)；验收按 native/mixed-native/mixed-OCR/scanned-Qwen/scanned-MinerU 分层；上线后 10% 随机 QA + 每 parser_version 每月 ≥30 样本；变更必须完整重跑 462 Gold | R4-05 |
| D11 | 方案分节 | **A+**：结构与语义正交双层；`structural_role`(content_section/toc/header_footer/cover/appendix/table_caption/unknown) 强排除 toc/页眉/封面；九类业务词典高置信确定 section_type；LLM 仅 unclassified/ambiguous 且需 grounding；子标题继承最近已分类祖先 envelope;附录不自动=non_scheme;unclassified 仍进通用 ES;建立 gold + Heading Boundary/九类 Macro-F1/TOC-FPR/Unclassified/LLM-Fallback/Grounding 六指标 | 5.5、R5 修订 |
| D12 | 生成证据选择 | **A+**：candidate pool 20–30 → 近重复聚类 → 去重后 3~5 条(5=硬上限/3=软下限)；sparse/insufficient 标记，不用低质补足；多样性=软策略（同项目≤2）；相关度优先于 provenance quality；新增 Evidence Unique Project/Cluster、Near-Dup、Same-Project、Relevance、Sparse Rate 指标 | R7-03、7 验收指标 |
| D13 | 增量版本 | **A**：按可复用产物分版（catalog/classification_version、parse_version、extract_version、section_version、chunk_version、embedding_spec/version + index_schema_version/index_generation）；依赖 DAG + 输出 hash 短路；pipeline_version 仅 release bundle，不触发全量 reparse；**非 parse_version 变化不得触发 OCR（R8 硬不变量）** | R8-05/06、R3 修订 |
| D14 | 命令拆分 | **A+**：`scan`(只登记 inventory，不含 parse/OCR/LLM/chunk/embed，size/mtime 复用 SHA)、`classify`(登记台账的 scope/role/content_format)、`ingest`(只从台账选 DocumentVersion，禁止 walk NAS)、`rebuild --stage=extract|section|chunk|embedding`(禁隐式 parse/OCR)、`sync=scan→classify→ingest` 仅便利 wrapper；root safety soft-delete；三条集成测试(改规则 OCR=0 / 同 SHA 单 ParseArtifact / ingest 不 walk NAS) | R2、R8、cli 结构 |
| D15 | 切换与清理 | **A**：**连续稳定 30 天回滚窗口**(非固定日历日期，从最近成功 cutover 起算，回滚或 P0/P1 修复并重切则重新计时)；T0 前剥离 462 Vendor Gold + 229 Gold 为独立版本化 gold 集；每周完整性校验 + rollback manifest；删除需六条件(连续≥30 天/无 P0-P1/各 Gold 通过/指标正常/备份可恢复/用户确认)；删除前 snapshot + 只读备份；长期只保留版本化 Gold、迁移统计、异常清单、删除记录 | R9 |
| D16 | 文档策略 | **A**：新增 `MINIMAL_REBUILD_PLAN-DECISIONS.md`（本文），基线 `MINIMAL_REBUILD_PLAN.md` 保留为执行基线；实施前合并生成 `-v2.md` | 版本管理 |

---

## 2. Scope Decisions

- **D1A** 项目范围 = 2025/2026 下、非"非标书/内部资料/系统临时"项目全部登记；比选/询比/磋商/调研/报名等按其是否有真实响应材料纳入。所有 project_type=标书 硬过滤描述改为范围规则。
- **D4A** 响应材料包资格 = 项目内实际存在确认的 our_response/final_signed；不要求必须存在招标文件；报名/保证金/邮件/报价沟通项目仅登记。
- **D8A** 非标书默认排除 + 高价值文档角色例外纳入（contract_evidence/资质/签章 允许登记、解析、索引、检索）；新增 `scope_status/scope_exception/inclusion_reason`；评估按 project_type + document_role 分层；不通过删除 Gold 正例适配范围。

## 3. Data Model / Architecture Decisions

- **D2A** `contract_evidence`：解析 + 结构化提取（编号/甲乙方/日期/总额/产品明细金额），进入 contracts/contract_items，不进方案向量索引；不改角色；是"历史合同定位"一等证据。
- **D3A** 解析缓存 = DB 内 `canonical_document_id`（sha256→canonical）；同内容只解析一次、副本多路径；删除 data/parse-cache 目录设计；临时 OCR 文件任务后删除。
- **D5A** documents 拆 `path_vendor`（路径厂商，审计）；contracts 增 `contract_vendor/vendor_scope/vendor_evidence/vendor_conflict`；我方合同只过滤 contract_vendor_scope；路径厂商不参与归属硬过滤；462 份全部人工核验 ⇒ vendor_extraction_gold_v1。
- **D9** contract_items 拆分 detail/subtotal/contract-total 行语义 + `row_type/product_amount_source`；A+ 产品金额口径。
- **D11** bid_scheme_sections_v1 正交双层（structural_role + section_type）+ `boundary_source/classification_source/classification_confidence`。
- **D13** 六独立 stage version + index_schema/version + index_generation；输出 hash 短路。

## 4. Runtime / Pipeline Decisions

- **D6A** 播种：scan 全登记（无解析）→ seed_priority.csv 排序 → 第一批 gold/confirmed-seed/native → 第二批其余 native → 第三批 scanned 去重后 OCR → 其他仅台账。
- **D7A** 验收集：迁移旧 229 + 补财务社保/仪器/方案 10–20 条；旧合同行冻结不再反向改变。
- **D10** 双层验证语义（见 1.D10）。
- **D12** 生成证据选择（见 1.D12）。
- **D14** scan/classify/ingest 三层解耦 + rebuild --stage + 禁隐式 OCR（见 1.D14）。
- **D15** 连续稳定 30 天回滚窗口 + Gold Set 剥离 + rollback manifest（见 1.D15）。

## 5. R0–R9 Override Map

| 阶段 | 被影响决策 |
|---|---|
| R0 | D6（seed_priority.csv）、D7（229 迁移+补小类）、D15（T0 剥离 Gold） |
| R1 | D3（删 parse-cache、canonical 缓存）、D13（schema/index version） |
| R2 | D1（范围）、D4（响应包资格）、D6（scan 只登记）、D14（scan/classify 拆分） |
| R3 | D2（contract_evidence 解析）、D3（canonical 缓存）、D6（三阶段播种）、D13（parse_version）、D14（ingest 只从台账） |
| R4 | D2（合同提取）、D5（vendor/归属）、D9（产品金额）、D10（双层验证）、D13（extract_version） |
| R5 | D11（分节双层）、D13（section_version/chunk_version/embedding_spec） |
| R6 | D2（合同检索双通道）、D5（vendor 过滤）、D9（产品金额）、D11（结构过滤） |
| R7 | D10（grounding）、D11（section 双层）、D12（candidate pool + sparse/insufficient） |
| R8 | D13（分段版本+硬不变量）、D14（rebuild --stage） |
| R9 | D1（验证样本范围）、D6（种子批验证前置）、D15（30 天窗口+删除条件） |

## 6. Hard Invariants

1. **非 parse_version 变化不得触发 OCR**（R8 硬业务不变量；需回归统计 documents_reparsed/ocr_pages_sent/documents_reextracted/chunks_rebuilt/embeddings_recomputed）。
2. **contracts.total_amount 永不得作产品金额 fallback**，不得参与产品金额阈值判断（D9）。
3. **Gold Set 不因系统召回失败而删正例**（R0-07 / D8）。
4. **scan 不得隐式 ingest**；**ingest 不得隐式 walk NAS**（D14，集成测试锁定）。
5. 相同 SHA-256 只产生一个 ParseArtifact；同内容只解析/OCR 一次（D3）。
6. 竞品污染 = 0；竞品内容禁止进入方案索引/生成（3.3 边界，不变）。
7. manual_override / 人工 Gold 是最高优先，自动规则不得覆盖（不变）。
8. NAS 源文件只读；删除需符合 D15 六条件 + 用户显式确认；不用通配符删库/索引/目录（不变）。

---

*（实施前执行：baseline `MINIMAL_REBUILD_PLAN.md` + 本附录 D1–D15 → 合并生成 `MINIMAL_REBUILD_PLAN-v2.md`；旧基线保留在 Git 历史，新库不新增长期兼容层。）*