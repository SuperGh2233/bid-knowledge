# Agent Handoff — bid-ai-clean

> 更新 2026-09-16 13:20（**按本技能 §1–§13 结构重写**）。旧版 458 行中随工作完成而失效的部分已收敛，
> 旧内容仍可取回：`git show HEAD:docs/agent-handoff.md`。
> 权威：仓库根 `AGENTS.md`（命令与红线，**随仓库分发**）、工作区级 `../CLAUDE.md`（两版本拓扑）、`docs/index.md`、
> §2 列出的文档。**冲突时以仓库与实测为准**；本文件只是可替换的检查点，不是规格。

## 1. Current Goal

交付两个能力（**只有这两个**，不得扩第三条主链路）：**需求一** 历史材料定位（自然语言条件 → 我方响应文件 + 内部业务记录）；**需求二** 模块级方案生成（方案名 + 必须包含内容 → 带出处草稿）。

**当前没有进行中的开发任务。** 最近一轮是需求方试用反馈 5 条的六轮整改（2026-09-15/16，已全部实施+验证）、仓库发布与工作区清理（2026-09-16）。

退出门槛 vs 实测（未回退）：

| 门槛 | 实测 |
|---|---|
| 一 · Recall ≥90% | **92.3%**（26 条可判定 → 24） |
| 一 · 真实路径正确率 100% | **100%**（逐条 Test-Path） |
| 一 · 金额条件准确率 100% / 误返 0 | **100%** / **0** |
| 一 · Success@5 100% / Precision@10 | 100%（3/3，样本少）/ **93.3% 下界**（口径见 `docs/evals/success5-precision10-clarification.md`） |
| 二 · 必要小节覆盖 8/8 | **100%**（模块数 9→8，见计划 §9） |
| 二 · 引用覆盖 100% / 竞品 0 / 不可追溯数字 0 | **100% / 0 / 0** |

## 2. Linked Authoritative Documents

| 文档 | 控制什么 |
|---|---|
| `AGENTS.md`（仓库根） | **随仓库分发的稳定指令**：环境、命令、红线、代码地图、文档导航（2026-09-16 新建；`CLAUDE.md` 是指向它的一行指针） |
| `../CLAUDE.md`（工作区根） | 两版本拓扑（现役 `bid-ai-clean/` vs 归档）、三份历史设计文档的地位。**不再含命令与红线**（已移交 `AGENTS.md`） |
| `docs/index.md` | 文档地图与权威来源登记（2026-09-16 重组后含**状态列**与「新文档纪律」） |
| `docs/plans/active/PLAN-20260915-demo-feedback-issues.md` | **active**。需求方 5 条反馈的权威记录：§6 需求 / §8 已定口径 / §9 实现与遗留 / §10 验收 / §11 验证证据 / §12 状态 |
| `docs/business/demo-feedback-open-questions.md` | 上述计划的一页纸（会前对齐用；三个口径已全部拍板） |
| `docs/specs/api.md` | HTTP 契约（端点、字段、错误语义、踩坑） |
| `docs/authorizations/llm-intent-authorization.md` | **意图识别层**的外发授权（只发用户那一句话） |
| `docs/authorizations/ocr-authorization.md` / `ocr-authorization-response-docs.md` / `llm-generation-authorization.md` / `llm-classification-authorization.md` | 另四条外发授权，**范围不可自行扩大** |
| `docs/plans/legacy/MINIMAL_REBUILD_PLAN.md`（+ `-DECISIONS.md`） | 旧系统路线（§2 需求与金额口径修订 / §10 执行记录 / §11A 门槛）。2026-09-16 自旧系统仓库复制入本仓库，**旧代码已移出工作区归档** |

## 3. Current Progress

**Completed + Verified（2026-09-16 实测）**
- 需求方 5 条反馈全部实施：R1-1.a（覆盖面公示）、R1-1.b′（合同 127→**165**，白名单 95→**136**）、R1-1.c（`Visium HD`/`Stereo-seq` 并入空间转录组，`Xenium` 单列）、R1-2（社保月份，**六轮**修复）、R1-3（仪器通称→型号）、R1-4（模糊检索/裸厂商名）、R2-1（方案产品线维度）。逐条验证见计划 §11。
- 查询意图识别层（`app/intent.py`，本地优先 + LLM 兜底）已上线，`INTENT_LLM_ENABLED=true`。
- 三类材料卡片补齐「打开文件/文件夹」三件套（根因是 `material-facts` SELECT 漏 `d.document_id`）。
- `pytest tests -q` → **288 passed**（2026-09-16 最后一轮实测；写作时为 267）。
- **业绩清单（响应文件内的历史合同声明）已进入检索**（第四轮）：`LEDGER-*` **29 → 402 条 / 50 份文件**
  （**有金额 289 条 = 72%**，逐条反查证据原文 0 条对不上）；单列来源标签、不参与金额过滤、
  同一声明跨项目副本只展示一次；见 `docs/plans/active/PLAN-20260916-track-record-search.md`
  （**completed**；§5.2–5.5 记录四轮金额修订、两类误抽拦截、受限放行与 OCR 分诊结论）。
- 库规模（只读复核 `bid_ai_clean_reg.db`）：`documents 6043 / contracts 165（可查 136）/ contract_items 649 / material_facts 1986 / parse_artifacts 2810`；社保事实 **913**、财务 341、`instrument_name` 168。
- 仓库发布：GitLab `ai-project/bid-ai` 与 GitHub `SuperGh2233/bid-knowledge`（**已私有**）两侧 `main` + tag `v1.0.0` 与本地 `deda794` 逐字节一致（`git ls-remote` 核对）；工作区从 1.4G 清到 **544M**。

**In progress**：无。
**Not started**：`refresh_catalog.bat` 计划任务（已问未定）；方案质量的**业务人工评审**（前置已就绪，须业务发起）；OCR 混扫的**按页归属拆分**（独立课题）。
**Rejected / abandoned（勿重开）**：确定性规则替代 LLM 语义分类材料来源（召回仅 7.4%）；收款凭证的两条补充通道（只覆盖 1/93）；拿 OCR 凑「项目风险识别与措施」（命中 0）。

## 4. Changes Made

2026-09-16 下午共**四轮**：① 旧系统迁出准备 + docs/ 重组 + `AGENTS.md` 落仓；② 清文档债（api.md 契约）；
③ 前端常用问法示例重排；④ **业绩清单进入检索**（唯一动代码的一轮：`app/search.py` 新增 `locate_track_records`、
`routes_search.py` 接线、`static/*` 渲染，含 11 条新测试）。

| 文件 | 改了什么 |
|---|---|
| `AGENTS.md`（**新建**，仓库根） | 随仓库分发的稳定指令：环境/命令/红线/代码地图/文档导航。**不放进度与密钥**。`CLAUDE.md`（新建）是指向它的一行指针 |
| `docs/` **目录重组**（`git mv`，21 个文件移位） | `specs/api.md`；`plans/legacy/MINIMAL_REBUILD_PLAN(.md/-DECISIONS.md)`（自旧仓库逐字复制，正文冻结）；`authorizations/`×5；`evals/success5-*` + `evals/archive/`×9（r5-*/d1/d2a，冻结不改名）；`business/`×4。**文件名一律未改**（授权文件名出现在 403 错误文案与脚本提示里，改名风险>收益；命名规范只约束**新**文档，见 index.md「新文档纪律」） |
| 引用同步（25 个文件） | `docs/` 内互引、README、PLAN、代码注释（`app/config.py`、`app/ocr.py`、`app/proposal.py`、`app/intent.py`、`app/routes_*.py`、`app/search.py`、`app/extract.py`、`scripts/*` ×7、`tests/test_intent.py`）全部改指新路径；`docs/business/demo-feedback-open-questions.md` 与 `docs/evals/success5-*` 的裸相对引用单独修正 |
| `docs/index.md`（重写） | 按新目录树重排 + **状态列**（active/legacy/archived/completed）+ 归档件一行化 + 「新文档纪律」5 条 |
| `README.md`（M） | 「当前状态/运行/仓库与远端」三段重写；文档引用改指仓库内新路径；写明旧系统已移出工作区归档 |
| `docs/agent-handoff.md`（M） | 本文件 |
| **仓库外** `标书文库\CLAUDE.md`（工作区根） | **瘦身为纯拓扑**（两版本+归档+三份历史设计文档的地位）；命令与红线**移交** `bid-ai-clean/AGENTS.md`，不再双份维护 |
| **仓库外** 工作区根 `.pytest_cache/` | **已删除**（4 文件 / 15K，仅 pytest 缓存元数据） |
| **仓库外** `标书文库-旧系统归档\README.md` | 已建（说明内容、旧 git 历史唯一副本警告、`.env` 含真 key 勿外发、恢复步骤） |

**第二轮（同日，清文档债）**：`docs/specs/api.md` 补齐 4 项（`recognition` / `files` / `file_count` / §8 `tender-check`）**并修正两处契约漂移**（§0 外发声明、§1 旧计数）；计划与一页纸的「当前基线」数字 245/256 → **267**（逐轮历史数字保留）；`AGENTS.md` 补「已知问题与风险」「持久决策」两个恢复指针；计划 §9 两处「遗留」改写为**终态**；`docs/index.md` 去掉 api.md 的债务标注。**纯文档改动，未动代码。**

**第三轮（同日，前端示例重排）**：`static/index.html` 的「常用问法示例」**8 条 → 13 条**，每条都先实测过
（新增 5 条：平台别名 `空间转录组5万以上`、模糊说法 `欧易的代谢组2万以上`、财务 `2025年度财务报告`、
仪器通称 `找质谱仪`、`找仪器照片`、`发票材料`；删掉 `找仪器设备清单` —— 上方「按类浏览」已有一键入口，重复）。
**选择标准**：结果非空 + **本地可判定（0.0–0.2s）** + 覆盖一类真实问法。实测数据与两条边界写入
`docs/business/query-nl-mapping.md` §二（**含「去掉金额会走 LLM 兜底、耗时 20 秒」与「`测序仪` 库内无型号」**）。
静态文件按请求实时读取，**无需重启**；`pytest 267 passed`。

**第四轮（同日，业绩清单进入检索 —— 用户提问「响应文件里也有合同作为业绩展示，要不要补进来」）**：
先按 docs-first 立项 `docs/plans/active/PLAN-20260916-track-record-search.md`（含三个口径裁定与实测数据），
再实现并验证（**计划状态 completed**，验收 7 项全过）：
- **数据**：`scripts/backfill_track_records.py` 把业绩清单抽取从 D1 的 29 份重跑到全部已解析响应文件 →
  `LEDGER-*` 行 **29 → 126**、来源文件 **4 → 16**（表头命中 34 份，其中 12 份因"清单不完整"按既有门槛**不写**，
  内部有 305 条 —— 已列为第二步首要目标）；
- **检索**：`app/search.py::locate_track_records`（**签名里没有金额参数**，结构上杜绝红线）+
  `routes_search.py::_ledger_for`（有金额条件返回 None）；
- **端点**：`/api/three-modules?module=项目业绩` 增 `ledger_records/ledger_count`；合同检索增 `ledger` 段；
- **前端**：`ledgerBlock()` 单列区块（虚线框 + 来源标签 + 金额口径说明 + 打开文件），**不与合同原件混排**；
- **测试**：`tests/test_track_records.py` 11 条（含**接线测试** —— 见下）；`pytest` **278 passed**。
- ⚠️ **两个自己踩的坑（都已加护栏）**：① `resp["ledger"]` 写在了 `return {…}` **之后**成了死代码 →
  单测全绿而端点不带；**"函数对"≠"接线对"**。② 一开始按查询里的「乙方」筛业绩行 → 误杀
  （查询里乙方=供应商，业绩行 `party_a`=采购人，角色不同名同义），修后从 0 → **14 条**。

**第五轮（同日，「尽可能获取金额」+ OCR 分诊）**：用户要求「有的金额没获取到，要尽可能获取」并允许必要 OCR。
- **诊因**（逐份分类，非猜）：金额格写 `3.5万`（单位只认「万元」）→ 19 条；金额写进**项目名** → 5 条；
  **83 份表头认不出**（当事人列写 `单位名称/用户情况/客户名称/业主单位`，旧词表只有 8 个写法）；
  以及**列序问题**（当事人在后，旧解析会把项目名当采购人）。
- **修**：`_to_yuan` 认「万」+ 表头认「（万）」+ 行内金额 + **当事人词表补 7 个（实测词频驱动）** +
  **新增 `_column_index()` 按列名定位**（采购人/金额/项目/日期）。
- **结果**：`LEDGER-*` **126 → 402 条 / 16 → 50 份 / 有金额 50 → 289 条（72%）**；**逐条反查证据原文，0 条对不上**。
- ⚠️ **放宽带出两类误抽，均已拦下并定向清库 130 条**：① 偏离表冒充业绩表（"我司完全响应"24 行全假）；
  ② 关联方表（无金额列）把**电话号码**当金额（4.46 亿）。拦法：行级「既无金额、采购人又不像机构名」丢弃
  （**且不计 `unparsed`**）+ 表头**必须有金额列** + 金额上限 1 亿。
- ⚠️ **受限放行**：旧门槛「一行解析失败 → 整份不写」拦下 14 份/312 条；改为**仅当**「有行 + 已收尾 +
  该文档无既有行」三者成立才写，状态记 `synced_partial`；**删除保护一字未动**（有单测钉住）。
- **OCR 分诊（用户批准跑）**：201 份未解析响应 PDF 中 **199 份是真扫描件**（0 份有文字层），
  全量 OCR ≈ **45 小时**；但这 201 份里 **187 份所在项目已有已解析兄弟文件、156 份兄弟正文含业绩**
  → **金额不用 OCR 即可拿到**，**本轮不做 OCR**。
- **回归**：`pytest` **288 passed**（+9 条 `tests/test_ledger_guards.py`）。


✅ **旧系统搬运已完成**（2026-09-16 下午）：用户结束残留 `tail` 进程后，`bid-ai/` 与 `bid-ai-r0-snapshot/` 已移入 `C:\Users\hao.guo\Desktop\标书文库-旧系统归档\`。**搬运后逐项核对**：文件数 **1600 / 85** 与搬运前完全一致；旧仓库 git 完整（HEAD `6590fc2`、**12 个提交**、分支 `r0-baseline-20260907`、tag ×2、**0 行未提交**）；`.env`（1212 字节）与 `app/tests/scripts/data` 均在位。工作区 **913M → 544M**。

已提交的代码/测试改动在 `deda794`（`git show --stat deda794`），**不在此重复**；实现细节见计划 §9/§11。

## 5. Technical Decisions and Documentation Debt

已落档的决策：三份+1 外发授权（§2）；意图识别「本地优先、判不出才外发」，判据是既有解析器能否解析成功（`docs/authorizations/llm-intent-authorization.md`）；三个业务口径已定（**不接合同台账** / **Xenium 单列** / **模块通用-特异不做人工清单、交 LLM 裁**）。

**文档债（2026-09-16 下午已全部清偿）**：
1. ~~`docs/specs/api.md` 未同步新增响应字段~~ → **已补**：§1A 的 `recognition`（含 `kind` vs `intent` 的区别、本地优先与外发边界）、§4/§4A 的 `files`/`file_count`（并写明「几份文件 ≠ 几条记录」）、新增 §8 `POST /api/tender-check`（含「功能已暂停」标记与 `verdict` 三态语义）。
   ⚠️ **顺带修了同一文件里两处契约与现实不符**：§0 原写「所有端点均不外发」（实际意图识别兜底会外发**那一句查询**）、§1 的 `/api/status` 示例数字仍是 95 份/127 合同（实测 136/165）。**契约文档与现实不符，与缺字段是同一类缺陷。**
2. ~~计划与一页纸里的测试数字过时（245/256）~~ → **已改为实测 267**（只改「当前基线」类陈述；§11 的逐轮历史数字保留原值，那是当时的真实测量）。
3. ~~`docs/index.md` 的 Agent 指令指针悬空~~ → **已修**（新建 `AGENTS.md` + `CLAUDE.md` 指针）。
4. ~~仓库拓扑与凭据只存在于对话里~~ → 已写入 README、本文件 §12 与归档目录 `README.md`。
5. **新增（低优先）**：`CHANGELOG.md` 尚不存在，而已有发布点（tag `v1.0.0` = `deda794`）—— 按 `AGENTS.md`/docs-first 约定，CHANGELOG 只记已发布变更，需要时补建。

## 6. Contracts and Constraints

- **库**：活库（测试/演示）`bid_ai_clean_reg.db`；正式库 `bid_ai_clean.db` **禁写**（脚本硬拒，退出码 2）。
- **端点 12 个**，分布在 5 个文件（`api.py` + `routes_{search,proposal,status,open}.py`）：`/`、`/api/{status, ask, material-search, material-facts, scheme-search, three-modules, modules, module-kb, proposal-generate, tender-check, open}`。`POST /api/open` 是**唯一**会在服务端机器上启动外部程序的端点（默认关闭）。
- **`data/approved_documents.json`（136 份）= 合同定位的第一道闸**，在 **import 期**载入 `APPROVED_DOCUMENT_IDS` → **改它必须重启服务**才生效。白名单过滤必须在 `LIMIT` 之前。
- **资格门槛顺序不得改**（`app/search.py`）：角色 → 非 `BLOCKED_STATUSES` → 非 `framework_sample` → `sha256==canonical` → `contracts.source_sha256/parser_version` 一致 → `product_amount_status` ok。
- **需求二只保留模型生成**：`mode=local` → 400（`assemble_proposal` 实现与单测保留待命）。每次生成都外发正文，依赖网关可达 + `PROPOSAL_GEN_ENABLED=true`。
- `.env` 关键开关（**不含任何密钥值**）：`PROPOSAL_GEN_ENABLED=true`、`INTENT_LLM_ENABLED=true`、模型 `qwen3.7-flash`、网关 `newapi.oebiotech.com`（**外部网关，会外发**）。
- NAS 只读；派生物只写本地；上传文件不落库；新别名/新角色规则**须人工确认**后落库。

## 7. Tests and Verification

```bash
CONDA="C:\Users\hao.guo\AppData\Local\miniconda3\envs\langchain-dev\python.exe"
export BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db"
"$CONDA" -m pytest tests -q              # 267 passed（9s，无需 ES/key）——本文件写作时实测
"$CONDA" scripts/eval_gold_recall.py     # Recall 92.3% / 误返 0
"$CONDA" scripts/eval_success_precision.py
"$CONDA" scripts/refresh_catalog.py --dry-run        # 增量登记预览（只读，零外发）
"$CONDA" scripts/refresh_ocr_whitelist.py --dry-run  # 白名单 OCR 预览（真跑会外发，需授权）
"$CONDA" scripts/refresh_index_whitelist.py --dry-run# 白名单索引（零外发）
BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db" "$CONDA" -m app.api   # → http://127.0.0.1:8000
```

- **服务当前状态（实测 2026-09-16 15:06 重启后）**：PID **97852**（`python -m app.api`，工作目录 `bid-ai-clean`，`BID_AI_CLEAN_DB=…/bid_ai_clean_reg.db`），启动于 **15:06:22**，晚于最后一次代码改动 → **跑的是当前代码**。冒烟实测：`/api/status` 返回 `contracts 165 / queryable 136`；`/api/ask?q=找质谱仪` → `kind=fact` / `intent=instrument` / `source=local` / `decided=true` / **32 命中 / 9 份文件**（意图层本地判定，**零外发**）；`/api/material-facts` 带 `files`/`file_count`。
  ⚠️ 此前那个 10:05 启动的 PID 26368 **已按要求停止**（避免"旧进程返回 200 → 验证到旧代码"）。重启方式见下方命令。
- ⚠️ **演示前重启并按 PID+启动时间确认**：历史上旧进程占着 8000 会让新进程 bind 失败，而 `curl` 照样返回 200（**旧代码的 200**）。
- ⚠️ **PATH 上的 `python` 是别的 venv，没有 pytest** —— 必须用上面的 conda 解释器。
- ⚠️ Windows 上 `curl` 传中文参数会被 GBK 编码 → 验接口用 Python `urllib.parse.urlencode`。

## 8. Problems and Risks

### Confirmed problems（已知、未修，均已记录）
- **示例/问法层两条边界（2026-09-16 实测，详见 `docs/business/query-nl-mapping.md` §二）**：
  ① **去掉金额或机构会走 LLM 兜底、耗时 20 秒**（`空间转录组合同` 本地判不出 → 外发；对比本地路径 0.1s）
  —— 演示前须知，非故障；② **`找仪器采购合同` 与 `找仪器设备清单` 返回同一个 211 条泛桶**
  （真实 `purchase_contract` 仅 62 条）—— 更具体的意图被吞，改它要动 `_FACT_KW`，须单独验证。
- **OCR 混扫召回污染**：几份扫描 PDF 把合同正本/财务报表与响应文件扫进同一文件，新增 930 条章节里 **159 条**能命中方案关键词、其中 **51 条**标题含合同/财会噪声（如「售后租回」误命中「售后」）。**是数据源特性而非抽取 bug**；治本要「OCR 后按页归属拆分」，**独立课题**。
- **`华大` 查不到是数据事实**：该词在本语料里既不是甲乙方也不是产品文本（各 0 条）。「平台名当产品」（如 `华大Stereo-seq`）不在已完成范围。
- **泛问「找仪器」仍映射到 `instrument`（存期间）**：有意不改，有既有测试钉住。
- **草稿渲染样式与真实产物不匹配**：`draft-para`/`cite-ref`/`draft-cite` 全为 0 —— 那三类样式是照已下线的 local 格式写的；真实模型产物是散文 + 行内 `[En]`。**不影响可用**，未修。

### Unverified risks / assumptions
- **方案质量未经业务人工评审**、需求方试读反馈未回收 —— 下一步的实质风险都在这里。
- **令牌卫生**：GitLab 访问令牌以明文存在 `.git/config`（跟踪文件中 0 处、从未推送），且已在 2026-09-16 的对话里明文出现过 → **建议吊销重建**。
- 「改私有」**撤不回**早先公开过的内容（旧库曾含 4 个文件合同号、8 个文件医院名、1 个 `Z:\` 路径）。

## 9. Next Actions

1. **演示前重启服务并复核**：`netstat -ano | grep :8000` 记下 PID → 结束旧进程 → `BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db" "$CONDA" -m app.api` → `GET /api/status` 应返回 `queryable_contracts=136`。
2. **把 `docs/business/demo-feedback-open-questions.md` 带给需求方复核**五条修复（**必须由用户/业务发起**）。
3. **等用户点头再动**：`refresh_catalog.bat` 挂计划任务（需定时间与运行账号）；GitLab 令牌轮换；删掉已无独立价值的 `feat/info-architecture-rework` 分支；补 `CHANGELOG.md`；方案质量业务评审；OCR 按页归属拆分。

> **本轮已完成的动作**（勿重做）：旧系统目录搬出工作区（用户结束残留 `tail` 进程后移入归档，逐项核对见 §4）；
> `docs/specs/api.md` 契约补齐 + 测试数字统一（见 §5）；两个提交 `19213cb` / `925e6b8` 已推送至 origin + github。

## 10. Do Not Repeat / Do Not Change

- **不重开**：接公司合同台账（2026-09-15 用户裁定不接，改为补齐语料内覆盖）；把 `Xenium` 并入空间转录组；做人工的模块通用/特异清单；确定性规则替代 LLM 材料分类；收款凭证的两条补充通道。
- **不改**：正式库；资格门槛**顺序**；合同金额口径（产品金额 = 同产品 detail 行 `line_amount` 之和，`contracts.total_amount` **不得**替代）；合同级产品排除语义；`_FORMAT_RANK` 的 `mixed` 权重位。
- **不删**：ES `bid_chunks_v2`；`tests/test_contract_items.py::test_amount_unknown_stays_unknown_not_zero`（口径已改写，不是删）。
- **敏感/忽略**：`.env`（真实 key）、`bid_ai_clean_reg.bak-*.db`（**26 个写前备份 / 441MB**）、`*.db`、`tmp/`、`tmp_probe/`、`outputs/`（**真实投标正文**）、`data/*.bak-*.json`、`*.log`。**别把整个项目目录打包外发**（会带上 `.git/config` 里的令牌）。
- **仍适用的坑**（旧版 23 条教训的浓缩；全文见 `git show HEAD:docs/agent-handoff.md`）：
  ① 「有多少」的数字必须**同源**（唯一常量/函数，白名单过滤在 LIMIT 之前）；
  ② 前端不得出现 Python 专有方法（`node --check` 抓不到，靠 `tests/test_frontend_syntax.py`）；
  ③ `str.endswith(多字节串)` ≠ 末尾任一分隔符，须逐字判；半角 `+` 勿打成全角 `＋`；
  ④ 改 `app/extract.py` 前先查模块级常量重名（已撞过一次 `_SIGN_LINE`）；
  ⑤ **重算类脚本必须用与生产路径相同的输入**（曾用空文本重算，抹掉 3 个合同总额）；
  ⑥ **报结论前先验口径**（R1-1 初判错误：只在可检索白名单内取证 → 误判「库里有/没有」）；
  ⑦ 增量脚本一律**只登记不删**，正式库硬拒；`refresh_*_whitelist.py` 是白名单驱动，别用 `ocr_batch.py`/`r5_index_bm25_only.py` 的「补到 N 份」模式补新增。

## 11. Minimum Recovery Context

1. 本文件 + `docs/index.md` + 仓库根 `AGENTS.md`（命令与红线；工作区拓扑见 `../CLAUDE.md`）
2. `docs/plans/active/PLAN-20260915-demo-feedback-issues.md`（§6 需求、§8 口径、§9/§11 实现与验证、§12 状态）
3. 最后活跃的实现：`app/api.py`、`app/routes_search.py`、`app/intent.py`、`app/proposal.py`、`app/extract.py`
4. `docs/specs/api.md`（契约）、`docs/authorizations/llm-intent-authorization.md`（外发边界）
5. 两个常驻检查：`scripts/eval_gold_recall.py`、`scripts/eval_success_precision.py`

## 12. Git State

- **分支**：本轮提交在 **`main`**（此前工作分支 `feat/info-architecture-rework` 停在 `deda794`，已无独立价值，可删可留）。
- **上一发布点**：`deda794`（`feat: 需求方试用反馈五条整改 + 查询意图识别层`，2026-09-16 10:15:15）= tag `v1.0.0`，两远端一致；其后是本轮两个提交：`19213cb`（AGENTS.md 落仓 + docs/ 重组）与**文档债清偿提交**（`docs: 同步 api.md 契约并清理文档债`，见 `git log -1`）。
- **提交数 12**；根提交 `cc28082`（R1 骨架）。
- **tag**：`v1.0.0`（→`deda794`）、`minimal-rebuild-r1-20260907`。
- `git status --short`：**0 行**（本轮改动已全部入提交；**尚未推送**，见 §9 第 2 条）。
- **工作区根（`标书文库\`，不在任何 git 仓库内）**：`CLAUDE.md`（纯拓扑）、三份历史设计文档 + `任务分析.docx`、`.claude/`、
  以及 **`bid-ai-clean/`（唯一代码目录）**。旧系统的 `bid-ai/`、`bid-ai-r0-snapshot/` **已移出**（见 §1/§4），根残留 `.pytest_cache/` 已删。
  工作区体积 **913M → 544M**；归档目录 369M。
- **远端**（`git ls-remote` 与本地逐字节一致）：

| 远端 | 地址 | 角色 |
|---|---|---|
| `origin` | `gitlab.oebiotech.com/ai-project/bid-ai` | 公司内网主库；**HTTPS + 访问令牌**已配在 `.git/config`（明文）→ `git push` 免交互 |
| `github` | `github.com/SuperGh2233/bid-knowledge` | 个人库，**2026-09-16 已设为私有**；默认分支 `main` |
| `C:\Users\hao.guo\Desktop\标书文库-旧系统归档\bid-ai\`（**另一个本地仓库，已移出工作区**）| 旧 GitHub 远端（历史） | **旧系统 R0 基线 + 全部 12 个提交的 git 历史**；其 GitHub 远端已被新代码**强制覆盖**，旧历史**只在此归档内**；同目录另有 R0 数据快照 `bid-ai-r0-snapshot\`（非 git，166M）|

- ⚠️ **两个仓库历史互不相关**（根提交是 `cc28082`）→ 往旧库推新代码**必须 `--force`**（已按用户指示执行过）。
- ⚠️ 旧系统的 tag `v1.0.0`（`6590fc2`）**未推任何远端**（随归档留在 `标书文库-旧系统归档/bid-ai/`）；
  其两份权威计划文档已于 2026-09-16 复制进本仓库 `docs/`（见 §2）。
- ⚠️ 行尾：`core.autocrlf` 提交时把 CRLF 归一为 LF（提交时大量 warning，属正常）。

## 13. Recovery Command

> **当前状态**：需求一/二退出门槛**全部达标**；需求方 5 条反馈**六轮整改全部完成并验证**；仓库已发布到两个远端；
> **没有任何正在进行的任务**，下一步都是决策或文档同步，不是 bug 修复。
>
> 动手前：① 读 §5 的文档债与 §9 的动作清单；② 跑 §7 第一条确认基线（应为 **267 passed**）；
> ③ 改完代码**重启 8000 端口**再验（旧进程会让你验证到旧代码）。

`Invoke $resume-work in this repository, verify docs/index.md, the linked authoritative documents, Git state, and docs/agent-handoff.md, then continue from Next Actions item 1.`
