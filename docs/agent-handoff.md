# Agent Handoff — bid-ai-clean

> 更新 **2026-09-18（需求方第三轮实测反馈收尾：引用写法优化 + 标题结构自动填入）**
> ⚠️ **2026-09-21（晚）追加一轮工作**：需求方提问「业绩叫法会不会太局限」→ 前端文案中性化
> （历史合同/类似项目声明）+ **放宽业绩抽取表头判据并重跑**（408→494 行 / 63 来源，+12 份真表）。
> 详见本头部下方 §3 新增「2026-09-21 晚」与 `docs/plans/active/PLAN-20260921-widen-ledger-header.md`。
> ⚠️ **2026-09-21 恢复时更正**：本文件此前停在 09-18 12:20（`91a18c5`），漏记了 13:57–14:46 的
> 三个提交（第四批：标题结构自动填入 / 引用写法优化 / 提示词规则 7 交叉引用）。
> 本次已按现场核对补齐 §1/§3/§4/§6/§7/§9/§12/§13 —— 遗漏原因即下述「Next Actions 当天过时」的常态。
> （按本技能 §1–§13；旧版见 `git show HEAD~1:docs/agent-handoff.md`）。
> 权威：仓库根 `AGENTS.md`、`docs/index.md`、§2 列出的文档。**冲突时以仓库与实测为准**。

## 1. Current Goal

交付两个能力（**只这两个**）：**需求一** 历史材料定位；**需求二** 模块级方案生成。

**当前任务**：**需求方第三轮实测反馈已全部修复并验收**（2026-09-18）。分两批：

**第一批（上午，`a5a519e`/`fc7d81f`）**：
① 「25年的社保」期间条件被静默丢弃（正则只认 4 位年份）→ 现 913 条 → **35 条**；
② 解析不出条件时**落回全表**（`Xenium` 返回 2015 条）→ 现恒空 + 明确说明；
③ 「期间未提取到」用在**没有期间概念的五类**上（556 行）→ 现按类别给说法；
④ **输出结构从 query 推**：大标题 + 小标题从用户那句话里拆，模型逐字照此输出。

**第二批（下午，`b01dcfb`/`95fada2`/`f0c87b7`，本文件此前漏记）**：
⑤ **「根据【E3】」写法优化**：需求方实测「现在的输出会显示根据{E3} 这个需要优化」——
   全角【E3】原先**工具链三处都不认**（校验器报「没有任何引用编号」+「无法回溯的数字」、
   前端原样留在正文里）→ 提示词 + 后端 `_CITE` + 前端正则**三处同修**，
   `validation` 新增**行文风格提示**（只报不改写）；
⑥ **标题结构自动填入**：需求方「现在还需要用户自动填入」→ **新端点 `GET /api/plan-outline`**
   （零外发、不生成，走 `plan_sections` 同一份实现）；前端输入防抖 400ms 自动拆填，
   **手改优先不覆盖**、**拆不出就说拆不出**（`planned=false`）；
⑦ 提示词规则 7 的交叉引用补齐到 8–11（`f0c87b7`，收尾）。

代码已提交 `a5a519e`/`fc7d81f`/`e56e378`/`9da3ecf`/`b01dcfb`/`95fada2`/`7241ae6`/`f0c87b7`
并推送两个远程；`pytest` **323 passed**（2026-09-21 本机实跑复核）。

**⚠️ 我自己造成的事故（已处置，留档）**：为读 `extract_three_modules.py` 里的纯函数而
`importlib` 执行了整个文件 —— 该脚本**没有 `__main__` 守卫**，模块级直接 connect + 写库，
于是重跑了一遍抽取。**逐类型比对证明幂等、无数据损坏**（10 类计数逐一相同），
正式库 `bid_ai_clean.db` 未被触碰（脚本硬编码写 reg 库）。**根因已修**：
4 个同类脚本全部加了 `_main()` + `__main__` 守卫，并验证 import 不再写库。

退出门槛（需求一累计）：Recall **37/37 = 100%**（明细 + 提及两组）／真实路径 100%／
金额条件 100%／覆盖率 8/8；本轮新增验收全部达标（见 §3 与计划 §8 验收总表）。
**tag `v1.3` 已打并推送两个远程**（打在 `fdab772`）。
**`v1.4` 待打**：CHANGELOG v1.4 条目已写好（**覆盖上述两批全部条目**），
本地与双远程均**尚无 `v1.4` 标签**（2026-09-21 核对）—— 按惯例**待用户点头**。

**2026-09-21 晚追加（需求方提问驱动，未打 tag）**：需求方问页面「带有业绩会不会太局限」→
① 前端文案全部中性化（「业绩」→「历史合同/类似项目声明」，标注各叫法均收录）；
② 排查发现业绩抽取**表头判据过严漏真表**（列名带空格/无序号/无当事人列）→ 放宽判据并重跑：
   408 → **494 行 / 63 来源**（+12 份真业绩表，无报价表混入，零外发）。详见 §3 与
   `PLAN-20260921-widen-ledger-header.md`。

## 2. Linked Authoritative Documents

| 文档 | 控制什么 |
|---|---|
| `AGENTS.md` | 环境、命令、红线（**服务端硬开关默认全 false**、外发授权 6 份、NAS 只读、正式库禁写） |
| `docs/index.md` | 文档地图 + 状态列 |
| `docs/plans/active/PLAN-20260917-round2-feedback.md` | **第二轮任务的权威计划**：实测诊断 D1–D5 + 四项口径 + 五步实施 + 验收总表（✅ 全部完成，§8 有实测数字）|
| `docs/plans/active/PLAN-20260917-contract-product-mention.md` | 上一轮（正文提及组）的权威计划，**已收口冻结** |
| `docs/plans/active/PLAN-20260921-widen-ledger-header.md` | **2026-09-21 晚新增**：业绩抽取表头判据放宽 + 重跑（含 §4.1 两次重跑/误写报价表/数据修正的完整留档）|
| `docs/plans/active/PLAN-20260921-instrument-query-fix.md` | **2026-09-21 晚新增**：仪器型号碎片检索修复（第三路方向反转 + 词表拼写对齐），纯检索层 |
| `docs/plans/active/PLAN-20260921-instrument-kind-gate.md` | **2026-09-21 晚新增（系统性）**：仪器名抽取与文档分类解耦（kind 门误伤 92% 文档）+ 残渣规则 + 重跑，`instrument_name` 168→1327 |
| `docs/plans/active/PLAN-20260915-demo-feedback-issues.md` | 需求方**首次**试用 5 条反馈的权威记录 |
| `docs/evals/EVAL-20260917-gold-recall-regression.md` | Recall 复测报告；§7 记新基线 37/37=100% 与 G05 金标准瑕疵证据 |
| `docs/authorizations/llm-contract-mention-authorization.md` | 第 6 份授权，**暂缓启用**（本地规则已达同等召回） |
| `docs/specs/api.md` | HTTP 契约 —— ✅ 本轮已同步：§4（`content_snippet`/沉底排序/`finance_amount`/按类型的值说法）、§4A、**§4D（`plan-outline`，2026-09-21 补写）**、§5（`outline`/`outline_source`/引用编号口径）|

## 3. Current Progress

**2026-09-21 晚（需求方提问驱动；`PLAN-20260921-widen-ledger-header.md` 为权威）**
- **文案中性化**（前端 + 后端下发字段）：需求方问「带有业绩会不会太局限」——
  `app.js` 卡片标记「业绩声明」→「**历史合同声明**」、摘要行「含此类合同（业绩）」→
  「含此类合同声明」；`search.py` `TRACK_SOURCE_LABEL` / `amount_note` / `scope_note` → 统一
  「历史合同/类似项目声明（业绩、合作单位证明等叫法均收录）」。**收录层本就兼容多种叫法**
  （标题词表 `(业绩|类似项目|合同).{0,10}(清单|一览|汇总|情况表|列表)`+合作单位证明），改的是观感。
- **覆盖排查**（全量 1033 请已解析响应文件）：122 份带该类标题，只有 51 份进 `LEDGER-*`；漏的 70 份里
  **41 份是真业绩表**（列名带空格 `序 号`/无序号列/无当事人列，表头判据过严），其余是标题误报/空表/坏解析。
- **判据放宽**（`app/extract.py::_scan_ledger_full`，三处）：表头识别改为 `(序号 or 当事人列) and 窗口(金额列 or 内容列)`，
  循环内「重复表头跳过/未知表头收尾」与 `_parse_row` 传参同步 `_norm` 归一化；
  ⚠️ **无锚点必须 `序号 AND 当事人列`**（防报价表混入）；内容列仅在有业绩标题锚点时单独作证。
- **重跑**：`backfill_track_records.py` → 终态 **494 行 / 63 来源**（408→494，+12 份真表 / +86 行）；
  第一次判据过宽混入 41 份报价表 90 行 → **校正判据重跑 + 新增 `scripts/cleanup_ledger_quote_residue.py` 定向清理**，
  终验：原始 51 份 0 损失、报价残留 0、行构成干净（有金额 91%、有采购人 98%）。`pytest 327 passed`。
- **数据面**：`/api/status` → `ledger_records: 494`；服务已重启（PID 26912，含新代码）；`material-search` 实测 ledger 段正常。

**2026-09-21 晚（后续追加：仪器检索修复；`PLAN-20260921-instrument-query-fix.md` 为权威）**
- **需求方提问「仪器清单收纳不少，但检索返回很少」** → 数字诊断：
  `instrument_name` 168 条/去重 16 种型号，`instrument` 211 条是**存在性空壳**（无型号）；名义 645 条里
  可支撑型号检索的只有 168 条。
- **根因 1 — 碎片检索方向写反**（`app/api.py::resolve_instrument_query` 第三路）：旧判据
  「库内型号名 ∈ 查询词」，用户必须输完整型号才命中 → `HBH192`/`QE质谱`/`Chromium`/`10X Genomics` 实测全 0。
  改成「**查询词碎片 ⊆ 库内型号名**」（先按原始词切分、再逐个空白归一，`\xa0` NBSP 显式并入）。
- **根因 2 — 词表拼写错**：`instrument_aliases.json` 写 `DNBSEQ-T7`，库内/正文真实值 `DNBSEO-T7` →
  词表对齐 + 补 `DNBSEQ`/`DNBSEQ-T7` 两个正确拼写别名。
- **实测（服务重启 PID 39656）**：`HBH192` 0→**22**、`Chromium` 0→**14**、`QE质谱` 0→**2**、
  `10X Genomics` 0→**14**、`DNBSEQ-T7` 0→**1**、`测序仪` 1→**2**；`质谱仪` 32 不回归；`Xenium`/`10X`(3字) 仍 0（护栏）。
- `pytest` **329 passed**（+2 测试）。纯检索层，零库写、零重跑；未打 tag（工作区含未提交改动）。

**2026-09-21 晚（后续追加：仪器名抽取系统性解耦；`PLAN-20260921-instrument-kind-gate.md` 为权威）**
- **需求方「Xenium 查不到，肯定哪里设计有问题」** → 全库摸底证实是**系统性缺陷**：
  正文可抽 `N台<名字>` 的 199 份文档 / 去重 **80 种**仪器名，库里只有 16 种；**92%（182/199）被 kind 门挡掉**。
- **根因**：`scripts/extract_three_modules.py` 把 `instrument_names_in` 挂在
  `if kind in ("instrument","instrument_photo"):` 门后 —— 而 `kind_of` 把 **social_security_month 排第 1 位**
  + 正文全文扫描，几乎每份完整响应文件都含「社保/完税」→ 整份判成社保类 → 仪器名抽取整体跳过。
- **修复**：**解耦**（无条件抽 `N台<名字>`，仅 `purchase_contracts_in`/photo 门保留）
  + 新增 9 类**残渣规则**（评分条款/序列号粘连/括号未闭合/短词…，`_reject_instrument_name`）
  + 修 **strip 剥括号 bug**（`液质联用仪器（LC-MS/MS）` 右括号被剥 → 误判未闭合拒掉）
  + 词表补 `色谱质谱联用` 短键。
- **实测（重跑落库 + 服务 PID 89924）**：`instrument_name` **168 → 1327 条 / 248 文档 / 68 变体**；
  `Xenium` 0→**11**、`液质联用` 12、`流式细胞仪` 14、`Olink` 12、`MobiNova` 10、`华大C4` 10、
  `Waters SYNAPT` 12、`QTRAP` 12、`质谱仪` 32→**182**、`测序仪` 2→**38**、`HBH192` 22→**149**；
  噪声（`流式细胞仪的`/`≤设备数`/`预备`/`备用`/`得1分`）全 0；其他类别零回归。
- `pytest` **336 passed**（+7 测试）。重跑只写 reg 库、正式库断言拒；未打 tag（工作区含未提交改动）。

**第三轮修复·第一批（2026-09-18 上午实测）**
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

**第三轮修复·第二批（2026-09-18 下午实测，`b01dcfb`/`95fada2`/`f0c87b7`）**
- **「根据【E3】」写法优化**：需求方实测「现在的输出 会显示根据{E3} 这个需要优化」。
  根因是**括号形态只在提示词里约定、工具链三处都只认半角**：模型输出 `根据【E3】，…` →
  ① 校验器 `_CITE` 认不出 → 误报「正文没有任何引用编号」；② 编号里的数字被当正文数字 →
  误报「无法回溯的数字：['3']」；③ 前端也只认半角 → 【E3】**原样留在正文里**。三处同修：
  - **提示词**（`GEN_SYSTEM` 规则 10/11）：编号**放句末**（给正例反例，明写「不要写成
    『根据【E3】，…』」）、同句不重复引用、规范用半角；且**不输出结构外内容**；
  - **后端 `_CITE`**：认 `[E3]`/【E3】/［E3］/「E3」/（E3），**不认裸 `E3`**（挡 `SE3`/`ACE3`）；
  - **前端 3 处正则** + 引用清单行判据同步（与后端同口径，有**跨语言护栏测试**）；
  - **新增 `_style_problems()`**：逐条「根据 Ex」开头（≥3 处）与同句重复引用**如实提示**，
    **只报不改写**（行文风格不是事实错误，拦下来用户就拿不到草稿）。
- **标题结构自动填入**（需求方：「现在还需要用户自动填入」）：原先要用户手敲大/小标题。
  **新端点 `GET /api/plan-outline`**（**零外发、不生成**）—— `?q=` 走 `plan_sections`（与生成时
  **同一份实现**，不另写一套），`?title=&sections=` 手选路径；返回
  `{planned, title, sections[], section_modules[], inferred[], dropped[], scope_note}`。
  前端 `#proposal-query` 输入**防抖 400ms** 自动拆填 + 状态行说明，**如实标注**系统推定的归属
  与点名但未纳入的项。两条诚实性护栏：**手改优先**（`_outlineDirty`，不再自动覆盖，有行为级测试）、
  **拆不出就说拆不出**（`planned=false`，明说「没能拆出结构、生成时会按模块名分节」，不假装填上）。
  拆结构失败**不阻断生成**（生成时服务端会再拆一次）。
- **提示词规则 7 交叉引用补齐到 8–11**（`f0c87b7`）：规则 7 原写「仍必须遵守规则 1–6、8、9」，
  漏了新增的 10、11 —— 只改提示词文本，无行为面变化。

**六项接口实测（2026-09-21，服务 PID 39704；恢复时实跑，不采信文档里的旧数字）**
| 项 | 实测结果 |
|---|---|
| `25年的社保` vs `2025年的社保` | **各 35 条**，两者一致（改前 913 条） |
| `Xenium` / `型号` / `完全不相干的词xyz` | **各 0 条** + `scope_note`「没能从你这句话里解析出材料类别或期间…没有执行任何筛选」（改前 2015 条全表） |
| `GET /api/plan-outline?q=售后服务方案，必须包含服务周期和应急预案` | `planned=true`、`title=售后服务方案`、`sections=[服务周期, 应急预案]`、`inferred=[服务周期]`、`dropped=[]` |
| `GET /api/plan-outline?q=帮我写个投标函` | `planned=false`、`sections=[]`（**如实说拆不出**，不假装填上） |
| `/api/status` | `总合同 136 / 可查 136 / 已核 136 / 业绩行 494`（2026-09-21 晚放宽判据重跑后；原 408） |
| `/static/app.js`（**服务下发的那份**） | 含 `VALUE_KIND` / `/api/plan-outline` / `_outlineDirty` / 「期间/型号」表头；`Cache-Control: no-cache, must-revalidate` |

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

## 4. Changes Made

### 第三轮（2026-09-18，两批；`a5a519e`/`fc7d81f`/`b01dcfb`/`95fada2`/`f0c87b7`）

| 文件 | 改了什么 |
|---|---|
| `app/api.py` | `parse_fact_query` 期间正则**认两位数年份**（`25`→`2025`；`(?<!\d)` 挡 `125年`）|
| `app/routes_search.py` | 解析不出条件 → **`AND 1=0` 恒空 + `scope_note`**（不再落回全表）；查询自带期间时才收窄（`period_narrowed`，只对 `social_security_month`/`finance_period`）；**无期间问法时有值行排前**；`fc7d81f` 把同一「有值行排前」口径补到 `three-modules` 的财务社保/仪器清单 |
| `app/proposal.py` | `plan_sections()`（结构从 query 推：大标题取原句最早出现的模块 + 用户措辞长形；小标题来自「必须包含X」子句切分 + 句外点名；挂不上的保留并标 `module_inferred`）+ `_split_req_clause`/`_dropped_requirements`（告警改按小标题粒度）；`build_evidence_packs` 接 `section_map`；`_CITE` 认全角/中文括号（**不认裸 `E3`**）；**新增 `_style_problems()`**（行文风格提示，只报不改写）；`GEN_SYSTEM` 规则 7/10/11 改写（编号放句末、不输出结构外内容、交叉引用补齐 8–11） |
| `app/routes_proposal.py` | 响应加 `outline_source`/`title_planned`/`sections_planned`；**新增端点 `GET /api/plan-outline`**（标题结构预览／自动填入，零外发，契约见 api.md §4D） |
| `static/app.js` | **`VALUE_KIND`**（按类别给「值说法」：五类存在性材料不再说「期间未提取到」）+ CSV 表头「期间」→「**期间/型号**」；前端 3 处引用正则与后端同口径；`#proposal-query` 防抖 400ms 自动拆结构填入 + 状态行；`_outlineDirty` 手改优先 |
| `static/index.html` | 标题结构框改版（供自动填入 + 手改，`b01dcfb`） |
| `scripts/{extract_three_modules,merge_classified_facts,r4_sync_classified_facts,r4_sync_material_facts}.py` | **事故根因修复**：加 `_main()` + `__main__` 守卫（原先 `import` 即写库） |
| `tests/test_proposal.py` | +13 条：结构从 query 推、自造小节不 KeyError、全角引用形态识别、合法全角不误报/编造仍报、前端同口径、风格提示两条、提示词要求、`plan-outline` 两路径 + 「拆不出就说拆不出」+ 手改不被覆盖的护栏 |
| `docs/specs/api.md` / `CHANGELOG.md` | §4/§5 契约同步；**§4D 为 2026-09-21 恢复时补写**（原 `7241ae6` 的 api.md 同步只改了 §5，漏了这个新端点的独立章节） |

### 2026-09-21 晚（未打 tag；`PLAN-20260921-widen-ledger-header.md` 为权威；工作区含未提交改动）

| 文件 | 改了什么 |
|---|---|
| `app/extract.py` | **表头判据放宽**（`_scan_ledger_full` 三处 + `_parse_row` 传参 `_norm` 归一化）：`(序号 or 当事人列) and 窗口(金额列 or 内容列)`；**无锚点须 `序号 AND 当事人列`**（防报价表）；`_norm()` 去列名空格 |
| `app/search.py` | `TRACK_SOURCE_LABEL`/`amount_note`/`scope_note` → 「历史合同/类似项目声明（业绩、合作单位证明等叫法均收录）」 |
| `app/routes_search.py` | 三模块 `scope_note` 一句改「历史合同/类似项目声明」 |
| `static/app.js` | 卡片 tag/摘要/来源/过滤说明文案中性化（「业绩声明」→「历史合同声明」等，用户可见全部） |
| `tests/test_ledger_guards.py` | +4 条：带空格列名、无序号列、表头跨行可命中；无当事人列（`序号|项目名称|报价`）仍拒 |
| `scripts/cleanup_ledger_quote_residue.py` | **新增**：删「当前判据 `header_found=False` 却有 LEDGER 行」的文档记录（只写 reg 库、删前备份、正式库断言拒）—— 补救第一次宽判据误写的报价表 |
| `docs/plans/active/PLAN-20260921-widen-ledger-header.md` | **新增计划**（含 §4.1 两次重跑/误写/修正完整留档） |

### 第二轮（2026-09-17，已提交 `faeebc8` + `0e4daf4` + `5944d0a`）

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
⚠️ 2026-09-21 恢复时发现并已补：`7241ae6` 声称「api.md §5 已同步」，但**新端点
`GET /api/plan-outline` 在 api.md 里没有独立章节**（只在 CHANGELOG 提了一句）——
本次补写了 **api.md §4D**，并修正 §6 里「端点 12 个不变」的错误说法（见 §6）。

## 6. Contracts and Constraints

- **端点现为 12 个**（2026-09-21 现场核对 `@router` 装饰器）：新增
  **`GET /api/plan-outline`**（标题结构预览／自动填入，**零外发、不生成、不写库**，契约见
  `docs/specs/api.md` §4D）。⚠️ 此前文档里「端点 12 个**不变**」的说法是**错的** ——
  加 `plan-outline` 之前是 11 个（第二轮的计划 §6 写 12 时就已经对不上账）。
  其余端点本轮只在既有响应/请求里加字段（`content_snippet` / `snippet_source` /
  `snippet_missing` / `outline` / `outline_used` / `outline_source` / `title_planned` /
  `sections_planned`）→ **全部仍只读**。
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
"$CONDA" -m pytest tests -q            # 327 passed（2026-09-21 晚实跑复核；323 基线 + 4 条判据放宽测试）
"$CONDA" -m app.api                    # → http://127.0.0.1:8000
"$CONDA" scripts/backfill_finance_amounts.py --dry-run   # 金额回填预览（零写库）
node --check static/app.js             # 前端语法（有护栏测试，但手改后先自查更快）
# 结构预览端点（零外发）：
python -c "import urllib.parse;print(urllib.parse.urlencode({'q':'售后服务方案，必须包含服务周期和应急预案'},encoding='utf-8'))"
```
- **服务（2026-09-21 实况）**：**PID 26912 在跑**（2026-09-21 晚重启，含本轮全部代码 + 放宽判据后的
  数据面；`readonly=true`）。`GET /api/status` → `scope`：**`total_contracts 136 / queryable_contracts 136 /
  ledger_records 494`**（放宽判据重跑后，原 408）；`/static/app.js` 下发文件已含 `VALUE_KIND` /
  `/api/plan-outline` / `_outlineDirty` / 新的「历史合同声明」文案，且带 `Cache-Control: no-cache, must-revalidate`。
  ⚠️ **本次恢复时我自己踩的坑（写下来防再犯）**：核对端口时用了
  `netstat -ano | grep -E "LISTENING.*:8000"` —— **模式写反了**：端口在 `LISTENING` **之前**
  （形如 `TCP 127.0.0.1:8000 0.0.0.0:0 LISTENING 18052`）→ 误得「无监听」，并据此在交接里写下
  「服务已停」（已更正）。**照 AGENTS.md 写的 `netstat -ano | grep :8000` 就对了**，PID 与启动时间一起看。
  实测那次的服务（**PID 18052，启动 09-18 14:41:45**）**一直活着**，但它**早于 `f0c87b7`（14:46）**
  —— 即提示词那条修复**不在它加载的代码里**，必须重启才生效（已 kill 18052 → 起 39704 → 晚再起 26912）。
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

0b. **2026-09-21 晚仪器的两批工作在未提交状态**（同一工作区、同一会假待提交）：
   · **A 检索层修复**（`PLAN-20260921-instrument-query-fix.md`）：`resolve_instrument_query` 第三路
     方向反转 + 词表拼写对齐/补 DNBSEQ 别名 + 2 测试；
   · **B 系统性解耦**（`PLAN-20260921-instrument-kind-gate.md`）：`extract_three_modules.py` 解耦
     kind 门 + 9 类残渣规则 + 括号剥除修复 + `instrument_aliases.json` 短键；重跑落库
     `instrument_name` **168 → 1327 条 / 248 文档 / 68 变体**；`pytest 336 passed`；服务 PID 89924。
   **下一步：一起提交**（用户点头后，含两个计划文档/交接同步/CHANGELOG；是否打 tag 随用户）。
0a. ~~2026-09-21 晚仪器检索修复（A）~~ ✅ 与 0b 一并提交（见上）。
0. ~~2026-09-21 晚工作（文案 + 判据放宽 + 重跑）~~ ✅ **已提交（`9b5597e`）并打 tag `v1.5`、推双远程**
   （2026-09-21 晚，用户点头）。
1. ~~打 tag `v1.4`~~ ✅ **已打并推送双远程**（2026-09-21，打在 `bb94715`；用户当日点头）。
2. **请需求方实机复核第三轮（两批共六项）** —— 服务**已起**（PID 26912，§7），
   **本机已按接口逐项实测通过**（证据见 §3「六项接口实测」），剩下的是**人眼确认**：
   · 「25年的社保」应返回 35 条、首页不再有「期间未提取到」；
   · 查仪器时不再显示「期间未提取到」，改显示型号或「本类只表示有仪器材料」；
   · `Xenium` 不再返回 2015 条全表，而是明确说「没解析出条件」；
   · 生成「售后服务方案，必须包含服务周期和应急预案」→ 大标题「售后服务方案」+
     两个小标题，且不写结构外内容；
   · **引用写法**：输出里不再逐条「根据【E3】，…」开头，编号在**句末**、`validation` 无
     全角相关误报；
   · **标题结构自动填入**：在生成输入框敲那句话 → 结构框**自动**列出大标题与小标题
     （手改后不被覆盖）；敲一句拆不出结构的（如「帮我写个投标函」）→ 状态行**明说没拆出**；
   · **业绩/历史合同声明文案**（2026-09-21 晚新增）：卡片标记改「历史合同声明」、摘要行
     「含此类合同声明的响应文件」、`ledger_records` 408→494（放宽判据新增 12 份真表）。
   实机前提醒：**用户 Ctrl+F5 一次**（§7 已确认服务下发文件是新的，但浏览器手里那份要硬刷丢掉）。
3. **遗留（用户未要求，勿主动开工）**：
   · **Xenium 仪器查不到**（第三轮报告里需求方提的「仪器定位有问题」的深挖项）——
     根因链已诊断清楚（四个断点：路由档位 / 抽取门 `kind_of` 单标签把社保放首位 /
     句式只认「N台<名字>」 / **型号抽取面 0 条**），修法涉及**重跑抽取**，
     需单独提计划与用户确认（尤其是「仪器名抽取要不要放开 kind 门」这个口径）。
     ⚠️ **2026-09-21 实测更正 + 证据（需求方问「Xenium 是什么」时全库只读核查）**：
     `instrument_name` 型号表里确实 0 条 Xenium，但**文档面大量有，不是「库里没这仪器」**——
       · **已解析正文含 `Xenium` 的文档 75 份**（欧易响应文件多份：如北京大学第三医院
         「10X单细胞转录组测序与 10X xenium 空间原位检测及分析服务」响应文件等）；
       · **文件名含 Xenium 的项目/合同几十条，含具体合同金额**（`Xenium 组织原位分析5000`
         冰冻/FFPE 样本，合同价 9.9万~27万：山东第一医科大学、上海新华医院、中国医学科学院
         肿瘤医院、临港国家实验室、农科院上海兽医所…；`xenium 5K 空转芯片检测`天津市第一
         中心医院；`Xenium 5k 空间原位基因表达`诺禾/复旦肿瘤医院；`拟南芥 Xenium 空间转录组`联川）；
       · `material_facts` 15 条含 `Xenium` 的词，但**全部挂在 `social_security_month`/
         `finance_period`/`invoice`/`instrument`（存在性空壳，evidence 只挂文件名）**，
         **没有一条归到 `instrument_name`** —— 即：原始材料在、是**抽取层没把 Xenium 变成型号**。
      ⇒ 修正口径：不是「数据面没有」，是「**文档面有 75 份、型号抽取面 0 条**」。
        Xenium = 10x Genomics 的 Xenium Analyzer（**原位空间转录组**平台，探针杂交+荧光成像、
        亚细胞分辨率；与库内 Chromium 单细胞 / CytAssist 空间制样同属 10x 家，比 Visium 更细，
        是空间转录组最新一代）。
        ✅ **2026-09-21 晚已修复**（用户拍板「当仪器修」→ 系统性解耦，`PLAN-20260921-instrument-kind-gate.md`）：
        不再遗留。Xenium 现 **11 条**可检索（`Xenium` 接口 0→11）；更重要的是一并修掉了同类
        系统性问题 —— `instrument_name` 168→**1327 条 / 68 变体**（质谱家族 182、测序 38、流式 14、
        Olink/MobiNova/华大C4/SYNAPT/QTRAP 各 10~12）。
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
  **2026-09-21 核对结果**：本地 = `origin/main` = `github/main` = `ac5affb`（三处一致）。
  第三轮关键提交：`a5a519e` 四条修复 + 脚本守卫 · `fc7d81f` 三模块有值行排前 ·
  `b01dcfb` 标题结构自动填入（新端点） · `95fada2` 引用写法优化三处 · `f0c87b7` 提示词交叉引用 ·
  `7241ae6` 文档同步。第二轮关键提交：`faeebc8` 实现 · `0e4daf4` 展示修复 · `5944d0a` 两端点标注统一。
  2026-09-21 晚（业绩）：`9b5597e` 业绩去限定化（文案 + 判据放宽 + 重跑 + 清理脚本）。
  2026-09-21 晚（仪器）：`90d7f5e` 检索碎片反转 + 抽取解耦 · `ac5affb` 文档同步（两计划/交接）。
- **工作区**：干净（除禁提交项）。
- **tag**：`v1.0.0` / `v1.1` / `v1.1.1` / `v1.1.2` / `v1.2` / `v1.3`（打在 `fdab772`）/
  `v1.4`（2026-09-21，打在 `bb94715`）/ `v1.5`（2026-09-21 晚，打在 `9b5597e`）/
  **`v1.6`（2026-09-21 晚，仪器系统性修复，打在 `81a3452`）** /
  `minimal-rebuild-r1-20260907`。
  **两远程 `v1.3`/`v1.4`/`v1.5`/`v1.6` 均已推送**（轻量 tag）。
  ⚠️ `bb94715` 是本文件在 `v1.4` 后**再次更正服务状态**之前的那一版；其后的更正提交
  **不含在 `v1.4` 里**（产品代码未变，无需移 tag）。
- **禁提交**：`.env`、`*.db`（含 `*.bak-*.db` 备份）、`outputs/`（真实投标正文）、`tmp/`、`*.log`。
  ⚠️ **备份文件命名必须命中 `.gitignore` 的 `*.bak-*.db`**：写成 `<name>.db.bak-<标签>` 会
  逃过忽略规则（本轮实测踩到并已修脚本，见 `backfill_finance_amounts.py` 注释）。
- **`tmp/` 下有本轮 13 个探针脚本**（`probe_finance_amounts*.py` / `probe_snippet*.py` /
  `probe_shell_*.py` / `probe_fin_*.py`）—— 一次性产物，不入库；
  其中 `probe_fin_amt_impl.py` 是「29 条」这个数字的来源，`probe_snippet_strong.py` 是 636/645 的来源。

## 13. Recovery Command

> **当前状态**：需求方**第三轮实测反馈的两批共六项 + 2026-09-21 晚「业绩去限定化」均已实现、验收、提交并推送**
> （2026-09-21 核对：本地 = `origin/main` = `github/main` = `9b5597e`；`pytest` **336 passed** 实跑复核）；
> **服务在跑：PID 89924**（2026-09-21 晚第三次重启，含仪器检索修复 + 系统性解耦）；
> **tag `v1.3` / `v1.4` / `v1.5` 均已打并推送双远程**（`v1.5` 打在 `9b5597e`）。
> **2026-09-21 晚仪器的两批工作（A 检索层 + B 系统性解耦）仍在工作区未提交**（Next Actions 0b；两个计划文档）。
> 剩余动作：**① 提交仪器两批（待用户点头）② 请需求方实机复核**（§9 第 2 条）。
> 第二阶段（表格结构化 / 金额汇总 / 固定模板）用户均未要求，**勿主动开工**。

`Invoke $resume-work in this repository, verify AGENTS.md, docs/index.md, linked authoritative documents, Git state, and docs/agent-handoff.md, then continue from Next Actions item 0b.`