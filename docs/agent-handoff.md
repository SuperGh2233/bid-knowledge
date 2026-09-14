# Agent Handoff — bid-ai-clean

> 更新 2026-09-14（**全面重写**：旧版累积到 696 行/64KB，已按本技能结构压缩并剔除过时内容；
> 同日晚些时候补：`app/tender.py` 实装内容、§4.11–4.14 四条实测教训。
> **再补（本轮）**：§8.1 第 2 条「条数口径」已做完（§4.15）、第 5 条已入表；
> **需求二只保留模型生成（2026-09-14 用户指令）** —— local 已从 API 下线，见 §1/§2/§7。
> **并更正上一版的两处错判** —— ① 旧版说「B2B 轮未入计划 §10」**是错的**（§10 早有该行），
> 真正漏的是**前端 P0 `rsplit` 修复那一轮**，现已补记；② 旧版 §7 的「同一屏数字自相矛盾」已修。）
> 权威路线见 `../bid-ai/MINIMAL_REBUILD_PLAN.md`（§2 需求定义与金额口径修订 / §10 执行记录 / §11A 门槛）。
> **冲突时以仓库与实测为准**；本文件只装「代码看不出来的东西」，不是进度流水账。
> ⚠️ **本文件 2026-09-14 一天内被多次修订**，「Next Actions」可能已被同日后续工作消化 ——
> 照它执行前先核 mtime、`git status` 与实测数字。**本版已把已完成的条目直接划掉，别照旧版重做。**

## 1. Current Goal

交付两个能力（**只有这两个**，不得扩第三条主链路）：

- **需求一 · 历史材料定位**：自然语言条件 → 满足条件的历史**我方响应文件**（业务记录 + 所在文件）。
- **需求二 · 模块级方案生成**：方案名 + 必须包含的内容 → 带出处的方案草稿。

**退出门槛 vs 实测**：

| 门槛 | 实测 | 判定 |
|---|---|---|
| 一 · 应召回文件 Recall ≥90% | **92.3%**（26 条可判定 → 24） | ✓ |
| 一 · 真实路径正确率 100% | **100%**（98/98、1000/1000、626/626 逐条 Test-Path） | ✓ |
| 一 · 金额条件准确率 100% | **100%**（53/53 命中均 ≥ 门槛） | ✓ |
| 一 · Success@5 ≥95% | **100%**（3/3） | ✓ 样本少 |
| 一 · Precision@10 ≥95% | **93.3% 下界 / 100% 上界** | ⚠️ 仅 3 个可判定查询，口径见 `docs/success5-precision10-clarification.md` |
| 一 · 不放宽条件造命中 | 误返 **0** | ✓ |
| 二 · 必要小节覆盖率 100% | **8/8 = 100%**（2026-09-14 复测；模块数由 9 改 8 见 §4.17） | ✓ |
| 二 · 引用覆盖率 100% / 竞品 0 / 不可追溯数字 0 | **100% / 0 / 0**（`validation` 空） | ✓ |

**当前任务范围**：B2B 四维评审已完成并修掉 P0/P1（§7）；**招标要求核对功能用户已明确暂停开发**。
**§8.1 五条全部完成**；**方案质量业务人工评审的前置已做掉**（样本包 + 指引就绪，见 §2「Not started」），
**下一步是让业务同事照 `docs/proposal-quality-review.md` 评审** —— 这一步必须用户/业务发起。候选见 §12。

## 2. Current Progress

### Completed + Verified
- 需求一四场景可用：合同定位（产品/金额/日期/机构/排除）、材料存在性、三类定位、方案章节。
- 需求二**方案模块 8 个**全部 `ok`，覆盖率 **8/8 = 100%**（2026-09-14 用户裁定：只保留"方案"类模块，
  已摘除「对项目的理解与需求分析」——它不是方案，见 §4.17）；**只保留模型生成一条路**
  （`local` 已从 API 下线，`assemble_proposal` 实现与其单元测试保留待命，见 §2/§7）。
- `pytest tests -q` → **200 passed**（必须用 conda `langchain-dev` 解释器，见 §6）。
- **「条数 vs 文件数」口径已统一**（§4.15）：卡上数字与点进去的条数同源，HTTP 实测
  **95 / 1564 / 722**，项目业绩卡 == 侧栏可查数 == 95。
- 库规模（实测复核）：`documents 5,913 / contracts 127`（其中 CTL 98、**已核对可查 95**）
  `/ parse_artifacts 2,790 / contract_items 597 / material_facts 2,373`。
- 语料零外发扩容已完成两轮（+631、+828 份），**尚未耗尽**（还能再挖，见 §9）。

### In progress
- 无。B2B 评审 P2 项**已全部处理**（页签 02 模块入口已做 → 见 §7；`requirements.txt` 已钉版本）。

### Not started
- R9（切换与旧 ES `bid_chunks_v2` 清理）；Success@5/Precision@10 口径澄清（需求方未回）。
- ~~方案质量的业务人工评审~~ → **前置已完成，只差业务照做**（2026-09-14：`docs/proposal-quality-review.md`
  指引 + `outputs/proposal-quality-review-<ts>/` 样本包 + `scripts/make_quality_review_samples.py`；
  样本覆盖单模块 / 必须包含 / 时限冲突 / 未识别告警 / 大方案，6 项判定标准含一项机器保证不了的「成文质量」）。
  ⚠️ **评审必须用户/业务发起**（读草稿、填 `review_record.md`）。**样本全部为 llm 模型起草产物**
  （2026-09-14 后 local 已下线，取样默认外发，运行前用 `--dry-run` 确认；评审指引已更新）。

### Rejected / abandoned（勿重开）
- 用**确定性规则**替代 LLM 语义分类材料来源（实测精确率 100%、**召回仅 7.4%**）。
- 给「收款凭证」再开「文件名含合同号+付款词」「正文合同号±120字含付款语义」两条通道（实测只覆盖 **1/93**）。
- 拿 OCR 去凑「项目风险识别与措施」（已证 OCR 命中 0 条）。

## 3. Changes Made

全部**未提交**（35 `??` + 4 `M`，见 §11）。按用途：

| 区域 | 文件与关键改动 |
|---|---|
| 接口 | `app/api.py`（组装 + 共享辅助）+ `app/routes_search.py` / `app/routes_proposal.py` / `app/routes_status.py`（APIRouter 拆分，2026-09-14）—— 11 个端点；`PRODUCT_ALIASES`(14 类)、`PRODUCT_MATCH_EXCLUDE`、`_fold_by_contract`(R6-06 折叠)、`_annotate_fact_role`(角色分层)、`/api/ask`(需求一唯一入口)；`_FINANCE_FACT_TYPES`/`_INSTRUMENT_FACT_TYPES`/`_count_material_facts`/`_approved_contract_ids`（三处共用的唯一口径，§4.15）、`GET /api/modules`（**方案模块**词表，8 个，不碰 ES） |
| 检索 | `app/search.py` —— 资格门槛、`locate_sort_key`(R6-05 排序)、`match_exclude`(行级负向过滤) |
| 抽取 | `app/extract.py`(合同头/金额/材料事实)、`app/parser.py`(解析、`PARSE_SET_ROLES` 7 类) |
| 需求二 | `app/proposal.py` —— 证据包、`build_module_kb`(模块化经验)、`_slot_of`/`_time_mentions`、冲突检测、生成 |
| 前端 | `static/{index.html,app.js,style.css}` —— 2 个页签；`baseName()`、`copyText()`、`renderInnerRecords()`；**本轮新增** `fillProposalQuery()` + `loadProposalPicker()`（**方案模块**入口，词表从 `/api/modules` 取，前端不另抄）；**本轮前端整理**（2026-09-14）：`renderMarkdown` 重写（证据多行**归并成一段**、出处行降为小注 `draft-cite`、引用上标 `cite-ref`、`引用来源`收进 `<details>` 折叠）；`generateDraft` 警示合成一张卡、`scope_note` 收进「用稿须知」折叠、新增「复制草稿全文」按钮；**修复 `done()` 从未被调用的真 bug**（见下）；index.html 把 23 条检索示例、页签 02 的招标核对/模块化经验收进 `details`（功能未动） |
| **暂停** | `app/tender.py`（招标要求核对）—— **代码完整保留，用户 2026-09-14 明确暂停开发**。里面已实现：`extract_time_requirements`（时限类要求 + 历史同类承诺对照，三态 `covered`/`stricter`/`uncovered`）、`extract_hard_requirements`（**★/▲ 实质性条款**，两种排版：表格式 `1 \| ★ \| 交货时间 \| 值` 与小节式 `★服务内容`——后者**正文在下面几行**）、`classify_clause`（8 类归类）、`material_pools`（指向我司材料池）、`build_check_table`；端点 `POST /api/tender-check`（收 multipart 文件或 JSON 文本，**文件不落库**）。**要重启时从这里接着做，别重写。** |
| 测试 | `tests/*.py` 19 个文件；**新增** `tests/test_frontend_syntax.py`（前端护栏）、`tests/test_module_counts.py`（本轮，口径一致性护栏，含**变异验证**：类型表改窄即失败） |
| 文档 | `docs/{api.md,agent-handoff.md,llm-generation-authorization.md}` |

**已删除**（清理轮，用户确认）：`scheme-preview`/`proposal-evidence` 端点、冻结示范稿常量、4 个死函数、6 个悬空常量、2 个一次性脚本、3 个一次性数据产物、11 处死 CSS、空目录 `prompts/`。

## 4. Technical Decisions（**最容易被我重犯的**）

1. **合同金额口径**：产品金额 = 该合同**同产品 detail 行 `line_amount` 之和**；`contracts.total_amount` **不得**参与产品金额判断（D9）。`数量×单价` 推导**仅在整表每条明细都有非零数量与单价**时启用（防串列：`数量=500 单价=80000` → 4000 万，而合同总额 14.4 万）。
2. **产品别名是子串关键词、首个命中即返回** → **窄产品必须写在宽的前面**（否则「空间代谢组」被「代谢组」抢走）；每个条目的别名里**要含自己的规范名**（否则 `不要X` 归不了类）。`PRODUCT_MATCH_EXCLUDE` 是**行级**负向过滤——「转录组」会吞「单细胞转录组」、「靶向」会命中反义词「**非**靶向」。
3. **R6-05 排序层**：角色 → 格式 → 命中 → 金额大 → 签订日新 → 合同号。**日期排序不能对字符串按位取补**（`YYYY-MM` 与 `YYYY-MM-DD` 长度不同会排错），必须换算整数。
4. **R6-06 折叠**：同一合同多条命中折成一条文件结果，内部业务记录进 `records`；**保序**（按输入顺序取首次出现建组，不打乱排序层）。
5. **冲突只并列不择一**（需求二原文：「多份材料冲突时明确提示，**不能自动选择一个数字**」）。槽位判定的坑：**后向优先**，且距离要用**真实距离**（首版把方向编码成负数 → 任意靠后的槽位都赢过最近的）。
6. **`build_module_kb` 零外发**：归纳只用确定性能力（聚簇/时限抽取），不调模型。它进提示词时**必须显式禁止被引用**，否则模型把归纳句当原文引用。
7. **`app/config.py` 要 `load_dotenv(override=False)`**（真实环境变量优先）。**原先不加载** → README 说的「从 `.env.example` 复制」完全不生效。
8. **前端不得出现 Python 专有方法**（`rsplit`/`startswith`…）—— `node --check` 抓不到（语法合法），必须靠 `tests/test_frontend_syntax.py` 的渲染冒烟。
9. **时限槽位 `解决` 归一为 `完成`**，且归一要放在 `_slot_of` 的**两个返回点**（早返回分支曾绕过它）。
10. **`material_facts.fact_value` 装的是「期间」不是材料名**（对 `instrument` 类曾因此在库里查不到任何仪器名）。仪器名另存 `instrument_name`。
11. **服务时限的「依据」是招标文件，不是历史响应文件**（2026-09-14 逐项目实测 43 个项目 / 111 条招标时限句得出）：
   招标文件写了时限的，响应文件里 **56% 逐字照抄**、38% 同类改写、6% 未覆盖。
   → **对一份新标书，时限该从新招标文件取**；历史材料只当模板（「响应/到场/解决」的结构）
   与底账（我们最多承诺过什么）。**拿 A 项目的 48h 填 B 项目，若 B 要求 24h 就是废标风险。**
   另：同一份响应文件里的多个时限值**大多是同一套体系的不同环节**（响应/到场/解决），
   以及**文档自己的分级设计**（Ⅰ/Ⅱ/Ⅲ级各不同），**不是互相矛盾** —— 冲突检测的假阳性多出于此。
12. **冲突检测的槽位必须"后向优先 + 真实距离"**：`1小时内响应，4小时内到场` 里 4 小时与前后两个槽位**等距**，
   首版用严格小于保留先找到的 → 归成「响应」，与紧跟的「到场」打架。**距离必须用真实值比较**：
   首版把方向编码成负数，结果**任意靠后的槽位都赢过最近的**（曾有测试当场抓住）。
13. **`_NUM_UNIT` 单位要齐**：漏 `h` 会让 `48h内响应` vs `24h内响应` 这条**真冲突一直不报**（模型读原文发现了、系统漏了）；
   漏 `日` 会让招标文件的 **★硬性条款**「自合同签订之日起60日内交付」整条漏掉。`日` 需加**日期防误判**（`2020年1月1日` 的 `1日` 不是时限）。
14. **槽位词的假朋友**：招标文件里「响应」绝大多数是「**响应文件**」「响应截止时间」——
   「首次**响应文件**递交截止前**六个月**内税收凭据」会被抽成「响应 六个月」（那是**资格要求**）。已加 `_SLOT_BLOCK_AFTER`。
15. **「有多少」的数字必须同源**（本轮实测）：概览卡与明细页各写一份筛选条件 → 卡上 546、点进去 722；
   概览按 `LIKE 'CTL-%'`（98）而侧栏/查询按白名单（95）→ 浏览与查询落在**不同批文件**上。
   现收敛为**唯一常量/函数**：`_FINANCE_FACT_TYPES` / `_INSTRUMENT_FACT_TYPES`（概览与明细共用）、
   `_approved_contract_ids()`（概览卡、`live_scope`、项目业绩明细共用）。
   ⚠️ **白名单过滤必须在 `LIMIT` 之前** —— 原写法先 `LIMIT n` 再过滤，库里合同一超 n 就原样复发。
   ⚠️ 概览卡「财务社保 1564」vs 明细页显示 1000 是**显式截断**（页面写明「库内共 1564 条」），**不是矛盾**，
   别把它当 bug 一起「修」掉。
16. **前端整理后的三条（页面只留重要信息，2026-09-14）**：
   ① **`renderMarkdown` 必须把证据的多行文本归并成一段** —— 证据原文本身带换行（一段长文被拆成
   `- ` 首行 + 若干无前缀续行，`[E1]` 落在靠后行）。旧实现逐行渲染 → 一段证据碎成一串 `<p>`/`<li>`。
   现用 `evBlock` 收集 + `flush()` 归并；**续行识别必须限定在「上一个证据块之后」**，否则正文里的
   普通首列文本也会被误并。护栏（`test_frontend_syntax.py` 渲染冒烟）用**真实生成产物**钉住。
   ② **警示/冲突/缺节是「必须一眼看见」的信息，绝不能折叠**；折叠只用于「演示道具」（示例按钮、
   招标核对、模块化经验、用稿须知、引用清单）。**这是整理的红线**：只收敛版面，不藏诚实性提示。
   ⚠️ **该红线的例外（2026-09-14 用户要求，见 §4.18）**：**外发类事前告知文案**已按用户要求
   从页面上撤除（安全网在服务端 403，未授权不外发）。红线仍适用于结果里的冲突/缺口/校验提示。
   ③ **`generateDraft` 的 `done()` 必须真被调用**（`try/catch/finally`）—— 旧代码 `done` 定义了但从没跑：
   `_draftInFlight` 永不复位、`setInterval` 永不清理 → **第一次生成后按钮永远禁用、后续生成全部被静默吞掉**。
   这是评审工作结束后仍在线上的真 bug，被渲染冒烟（node 60s 超时）当场抓到。
17. **需求二只保留"方案"类模块**（2026-09-14 用户裁定）：`对项目的理解与需求分析`（关键词
   项目理解／对项目的理解／需求分析／项目背景／需求理解／项目概况）**不是方案**，已从
   `app/module_keywords.json` 摘除 → **9 个模块变 8 个**（项目管理与实施方案、售后方案、
   质量控制方案、应急预案、保密方案、项目风险识别与措施、样本接收及物流方案、培训方案）。
   **依据**：用户原话「需求2 你做的有点跑偏了 我的目标只需要生成方案 对项目的理解 和需求分析
   你是弄来干嘛的」。**影响面**：`/api/modules`（词表入口）、`/api/module-kb`（跨项目归纳）、
   `extract_required_sections`（从 query 认小节）三处同源生效。**改后必做且已做**：重跑
   `tmp/check_coverage.py` 复测 → **8/8 = 100%**（各模块标题命中 5、召回池 25），pytest 200 passed。
   ⚠️ **仍保留的**：需求一的"查方案章节"（`/api/ask` 走 `_ASK_SCHEME_KW`，含「对项目的理解／需求分析」）
   是**有意保留**的 —— 那是 R1「以前哪个文件写过某小节」的合法问法，与 R2 的模块词表是两件事，
   别一起删。⚠️ 若还想再摘（如「项目风险识别与措施」也嫌不像方案），改词表 + 复测即可，成本很低。
18. **需求二页面上不显示外发提示**（2026-09-14 用户要求，**推翻 §4.16 的旧红线**）：
   用户原话「会外发 · 需授权 …… 这个需求2页面的消息不展示」，补一句「都去掉」。
   **已撤三处**：① `index.html` 常驻的 `.scheme-note` 块（"会外发 · 需授权 / 生成会把历史我司响应正文
   发往公司网关由模型成文；未授权会明确报错，不外发。"）+ 随之成死样式的 `.scheme-note`/`.status-pill`；
   ② `app.js` 生成中的进度提示（原"正在把证据交给模型起草（会外发，需已授权）…" → "正在起草方案…"）；
   ③ 结果区产物标注（原"模型起草（正文已发往公司网关）" → "模型起草"）。
   ⚠️ **安全网一字未动、且在服务端**：未授权时 `app/proposal.py::_guard()` 抛 `ProposalGenNotAuthorized`
   → HTTP **403、不会有任何外发**（有测试钉住）。**撤的只是"事前告知文案"，不是授权控制**。
   ⚠️ **别再"好心"加回来** —— 这是用户明确的产品决策；若将来要恢复披露，先问。
   ⚠️ **§4.16 ② 的红线据此收窄**：那条「不藏诚实性提示」仍适用于**冲突/缺节/证据不足**（结果里的
   `warnings`/`gaps`/`validation`，仍在首屏醒目显示），但**外发类事前文案不在此列**。
   ⚠️ **保留未动的**：`/api` 未授权时的 403 `detail` 文案（只在**失败时**出现，是功能反馈，不是常驻告知）、
   `docs/api.md` 的外发声明（接入方契约，该记就记）。
19. **信息层级重做 P0（2026-09-14 业务评审后）**：评审结论「问题不在配色，在于页面像技术验证台 ——
   展示"系统里有什么"，没围绕用户动作组织」（后端 75% / 界面 50%）。用户裁定**分两步**：P0 先落、再 P1。
   **P0-1 文件优先卡片**：卡主标题从 `row.product` 改成 **`row.file_name`**，项目名升为标题上方一行，
   产品降为标签；新增「为什么符合条件」一行（自证：命中产品 + 金额 ≥ 门槛）；`CONTRACT_CSV` 列序同步。
   ⚠️ **后端响应一字未改** —— 实测 CTL 合同与 document **严格 1:1**（98→98，多合同文件 0），
   所以"文件优先"**只是渲染层**的事，改后端是纯风险无收益（`/api/ask` 同进程展开同一 dict）。
   ⚠️ 已加防御：若将来真出现"一文件多合同"，卡片自检 `records[].document_id` 数 >1 时**自己说出来**，
   而不是静默给错标题。
   **P0-2 `POST /api/open`**：新增 `app/routes_open.py`（独立文件 = 删一行 include 即下线）。
   **安全五道**：只收 `document_id`（不收路径）/ 未知 root **抛错**（抄 `parser.py::_doc_path`，
   不沿用全仓 `roots.get(id, Path(""))` 的静默降级）/ 路径**按 component** 体检（库里 2 个合法文件名
   含 `..`，子串判会误拒）/ 词法包含性断言（**不用 `resolve()`** —— 不可达 UNC 上会挂住）/
   后缀黑名单先判 + `target=file` 白名单（**排除宏格式**）。可达性(`503`)与存在性(`404`)**刻意分开**。
   **实测**：库里 6 个 `.exe`（投标客户端安装包），`os.startfile` 对 `.exe` 是**执行** —— 故黑名单是硬要求。
20. **信息层级重做 P1（2026-09-14）**：五项一起落地 ——
   ① **三类材料 / 八类方案入口直展**（原在折叠里，新用户根本不会打开；示例按钮仍折叠，它们是长尾）；
   ② **已识别条件改为可点标签**：`conditionTags()` / `conditionQuery()` / `conditionTagBar()` / `bindConditionTags()`；
   ③ **命中与未命中分离**：被排除的收进 `<details>「另有 N 条被排除，查看原因」`；
   ④ **移除「招标核对 + 模块化经验」UI**（两者都不是用户要的两个需求；**后端端点保留**，
      `build_module_kb` 仍是生成时的内部依赖）；
   ⑤ **移除左侧数据栏**，数字降页脚一行。
   ⚠️ **② 的关键设计**：点标签回填的是**全部条件拼成的查询**（"代谢组，2万元，2024年12月以后"）
   并**选中被点的那一段**，而不是只回填单个条件 —— 只回填单个条件会让用户"点完就查"因缺产品/金额
   报 400（那是给用户挖坑）。拼接顺序**排除类放最后**（后端的排除捕获窗口会吃到句末标点）。
   这条有**跨语言一致性测试**钉住：前端拼出的查询被逐个喂给 `app.api.parse_demo_query()`，
   既断言不抛错、也断言关键字段（产品/金额/日期/机构/排除）解析正确。
   ⚠️ **④ 的两处无保护绑定必须与 HTML 同批删**（`$("#load-kb")` / `$("#tender-form")` 是**顶层裸调用**，
   删 HTML 不删它们 → 整个脚本 TypeError 中断）。为此**先加了三道护栏**（见下），再动删除。
   ⚠️ **⑤ 连带**：`/api/status` 渲染原写死 `#metrics`/`#metrics-note`/`#scope-note-text`（在 `.then` 里，
   不会中断脚本、只会静默不显示）→ 已改指 `#footer-stats`/`#footer-note`。
21. **前端护栏三道新增（P1 前置，2026-09-14）**：都是"删 HTML 忘删 JS"这类**护栏原本抓不到**的错 ——
   ① `test_app_js_only_references_ids_that_exist_in_html`：`app.js` 里 `$("#id")` 必须在 html 里存在
     （例外白名单：`copy-cites`/`copy-draft`，运行期用 innerHTML 建、代码已判空）；
   ② `test_no_unguarded_top_level_dom_bindings`：**列 0 的 `$("#id").…`** 必须存在 —— 精确编码 ④ 的教训；
   ③ **node 探针的 DOM 桩改为"忠于真实 DOM"**：把 index.html 的真实 id 注入探针，引用不存在的 id 返回
     `null` → 顶层直接 TypeError。旧的桩对任意选择器都返回 mock，**物理上抓不到**这类错。
   **变异验证**：把 `id="metrics"` 改名为 `metricsX` → ①③ 双双失败（② 不响，因为它在 `.then` 里而非顶层）；
   还原后 4 passed。⚠️ 另修：探针 `subprocess.run(text=True)` 用本机 **GBK** 解码 stdout，
   探针一开始输出中文就崩 —— 已显式 `encoding="utf-8"`。

## 5. Contracts and Constraints

- **库**：活库（测试库）`bid_ai_clean_reg.db`；正式库 `bid_ai_clean.db` **禁写**。
- **端点 12 个**：`/`、`/api/{status, ask, material-search, material-facts, scheme-search, three-modules, modules, module-kb, proposal-generate, tender-check, open}`。**路由分布在 5 个文件**（§7；`routes_open.py` 是新加的），URL/行为不变。
  ⚠️ `POST /api/open` 是**唯一会在服务端机器上启动外部程序**的端点（默认关闭，见 §4.19）。
- **`app/parser.py::PARSE_SET_ROLES` = 7 类**（+`process_material`/`qualification_evidence`/`tender_requirement`/`unknown`）；**竞品/空模板/系统文件仍禁解析**（有测试钉住）。
- **检索资格门槛顺序不得改**（`app/search.py`）：角色 → 非 `BLOCKED_STATUSES` → 非 `framework_sample` → `art.sha256==canonical` → `contracts.source_sha256/parser_version` 一致 → `product_amount_status` ok。
- **外发授权三份，范围不可自行扩大**：`docs/ocr-authorization.md`、`docs/ocr-authorization-response-docs.md`、`docs/llm-generation-authorization.md`。`.env` 里 `PROPOSAL_GEN_ENABLED=true`、模型 `qwen3.7-flash`。
- **需求二只保留模型生成（2026-09-14 用户指令）**：`/api/proposal-generate` 只收 `mode=llm`（缺省即 llm）；`mode=local` → 400 明确下线提示。`assemble_proposal` **实现与单测保留待命**，只是不再从 API 暴露 —— 想恢复只消把 API 分支加回去。
- **NAS 只读**；派生物只写本地；上传文件**不落库**（内存解析一次即弃）。

## 6. Tests and Verification

```bash
CONDA="C:\Users\hao.guo\AppData\Local\miniconda3\envs\langchain-dev\python.exe"
export BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db"
"$CONDA" -m pytest tests -q              # 200 passed
"$CONDA" scripts/eval_gold_recall.py     # Recall 92.3% / 误返 0
"$CONDA" scripts/eval_success_precision.py
```
⚠️ **PATH 上的 `python` 是 hermes venv（3.13），没有 pytest** —— 必须用上面那个 conda 解释器。
⚠️ `eval_success_precision.py` 顶层 `from eval_gold_recall import QUERY_SPEC` 会连带执行整个评测；报告写入已加 `__main__` 守卫。
⚠️ 服务启动：`BID_AI_CLEAN_DB=<库> "$CONDA" -m app.api` → `http://127.0.0.1:8000`。
⚠️ **改完代码要重启服务**：端口 8000 上很可能还挂着**上一轮启动的旧进程**（实测 2026-09-14
  有一次旧进程从 10:38 一直占着端口，新进程 bind 失败但 curl 照样返回 200 —— **旧代码的 200**，
  差点据此下错结论）。重启前先 `netstat -ano | grep :8000` 看 PID 与启动时间。
⚠️ **Windows 上 curl 传中文参数会被 GBK 编码**（服务端收到乱码 → `未知模块`）。
  验接口用 Python 的 `urllib.parse.urlencode`，别用 `curl --data-urlencode`。

## 7. Problems and Risks

### Confirmed problems（**已修**，只留教训）
- **页签01「项目业绩」曾整块打不开**：`static/app.js` 写成 Python 的 `str.rsplit` → `TypeError` 抛在 `map` 里 → 98 条合同全渲染不出。已修 + 加护栏测试。**这是评审 P0。**
- **材料卡片曾把 `[our_response]` 当「对应文字」上屏**（2,286/2,373 条）→ 加 `_annotate_fact_role()` 剥枚举前缀；剥后**只剩文件名**则不作为证据。
- **`/api/material-facts` 曾无角色分层**：「哪些响应文件包含2025年的社保」460 条里只有 205 来自我方响应、**68 条其实是采购人磋商文件**，却写「找到 460 份」。已加 `role_scope`。
- **页签02 曾静默丢掉未识别的小节**（`必须包含质控要求` 无任何提示，接口 200）→ 加 `unrecognized_requirements()` 告警。

### Unverified risks / assumptions
- **方案草稿质量未经业务人工评审**；需求方试读反馈未回收。**且评审包现为 llm 产物**（local 下线后）——
  评审的实质是评「模型起草这条路的值不值得用」，衡量标准不变（§7 六项）。
- **需求二只保留模型生成 → 每次生成都外发真实正文**：这是用户 2026-09-14 的明确指令，但意味着
  「零外发也能出草稿」这条旧能力被关掉了 —— 以后每次生成都依赖网关可达 + 授权开关。
  若网关抖动，页签 02 会直接 502；**回退**：`assemble_proposal` 实现仍在，API 加回 local 分支即可。
- **~~`app/api.py` 1,085 行承载 8 个关注点，无 APIRouter 拆分~~ → 已拆（2026-09-14）**：
  `app/api.py`（组装 + 共享辅助，621 行）＋ `app/routes_search.py`（需求一检索，468 行）＋
  `app/routes_proposal.py`（需求二生成，228 行）＋ `app/routes_status.py`（健康状态，34 行）。
  ⚠️ **路由文件两步坑，别重犯**：① `python -m app.api` 下 `from app.api import ...` 会**重复加载**
  api.py 造成循环导入 —— api.py 顶部已加 `sys.modules.setdefault("app.api", sys.modules[__name__])`
  自注册修复；② 路由文件顶部 `from app.api import 共享辅助` 时全局 `__globals__` 指向 app.api，
  monkeypatch（`app.api.DEMO_DB` 等）**仍然生效** —— 这是有意为之，别把辅助函数搬去路由文件。

### 尚未修（B2B 评审 P2，见 §8.2）
- ~~页签02 **无模块选择入口**~~ → **已修**（新增 `GET /api/modules` + `#proposal-picker`；
  各模块**逐个实测可生成** → 全部 `200`、`status=ok`、各 5 条证据、`gaps=0`。
  注：该轮实测覆盖的是**当时的 9 个模块**；其中「对项目的理解与需求分析」已于同日按用户裁定摘除（§4.17），
  现为 8 个，复测 **8/8 = 100%**）。
- ~~**同一屏数字自相矛盾**~~ → **已修**（§4.15，`app/api.py` + `tests/test_module_counts.py`）。
- `requirements.txt` **已按实测环境钉版本**（12 个包；顺带补上原先漏登的 `rapidocr`/`onnxruntime`）。
  ⚠️ 以后加依赖**同时**钉版本 —— 不钉时「昨天 200 passed」不可复现。
- ~~`tmp_probe/`（他人建的 API 探测助手，内含 `get.py`）不在 `.gitignore` 覆盖范围~~ → **已修**
  （2026-09-14 加 `tmp_probe/` 进 `.gitignore`，`git check-ignore` 已生效、`git status` 0 命中）。
  ⚠️ 更正：旧版这里还写了 `_render_probe.js` —— 那个在 `tmp/` 下，**已被挡住**（实测确认），别误删护栏。
- ~~B2B 评审那一轮未记入 §10~~ → **已补记**（本轮把「前端 P0 `rsplit` + 护栏测试 + 195 passed」那一行
  补进 `../bid-ai/MINIMAL_REBUILD_PLAN.md` §10；旧版说「B2B 零命中」是**错判**，§10 早有 B2B 行）。

## 8. Next Actions

### 8.1 立即可做（不需授权、按优先级）
1. ~~跑 §6 三条命令确认基线未回退~~ → **已完成**（195 → 200 passed，Recall/误返/Success@5 未回退）。
2. ~~统一「条数 vs 文件数」口径~~ → **已完成**（§4.15，实测 95/1564/722 同源，+4 条护栏）。
3. ~~给页签02 补方案模块入口~~ → **已完成**（`GET /api/modules` + `#proposal-picker`，
   点一下填进输入框、不覆盖已有内容、不重复堆；各模块逐个实测可生成。同日按用户裁定
   只保留"方案"类 → 摘掉「对项目的理解与需求分析」，现 8 个模块、覆盖率 8/8，见 §4.17）。
4. ~~`requirements.txt` 除 `elasticsearch==8.19.3` 外也钉版本~~ → **已完成**（12 个包全部按
   实测跑通的环境钉死，`pip check` 无破损；**顺带补上原先漏登的 `rapidocr`/`onnxruntime`**
   —— `app/ocr.py` 实际 import 的就是 rapidocr，照旧清单装是跑不了本地 OCR 的）。

5. ~~在 `../bid-ai/MINIMAL_REBUILD_PLAN.md` §10 补记 B2B 评审那一轮~~ → **已完成**（补的是
   **前端 P0 `rsplit` 修复 + `tests/test_frontend_syntax.py` + 195 passed** 那一行 —— §10 里
   B2B 行本来就有，缺的是这一行；本轮的口径修复也已单独入表）。

**§8.1 至此全部完成**（含 `tmp_probe/` 已纳入 `.gitignore`，见 §7）。

### 8.2 已评估、暂不动
- ~~`app/api.py` 拆 APIRouter~~ → **已完成**（2026-09-14：`api.py` 621 行 + `routes_search/proposal/status` 三个路由文件，见 §7）。
- 「结果出不去系统」（无服务端导出/邮件/webhook）—— **评审提过，但属臆造需求**（本项目只声明两个需求）。

### 8.3 已定，勿再问
- **招标要求核对功能：用户 2026-09-14 明确暂停开发**。
- **OCR 阶段 2–4：维持不续跑**（内存阻塞未解除，用户自己的 java/WorkBuddy 占用 80%；阶段 1 已完成 346 份）。

## 9. Do Not Repeat / Do Not Change

- **不重开**：确定性规则替代 LLM 材料分类；收款凭证的两条补充通道；拿 OCR 凑覆盖率。
- **不改**：正式库 `bid_ai_clean.db`；`app/search.py` 资格门槛**顺序**；**合同级产品排除**语义（改行级会让 G05 的多组学合同误返）；`_FORMAT_RANK` 的 `mixed` 权重位（全库目前为 0，删了将来会当未知）。
- **不删**：ES `bid_chunks_v2`；`tests/test_contract_items.py::test_amount_unknown_stays_unknown_not_zero`（已改写口径，不是删掉）。
- ⚠️ **三条曾被写成本文件里的「已实测结论」、后来被推翻的**（**别照抄旧版**）：
  1. ~~「本地零外发扩容已挖尽」~~ → **错**。根因是解析驱动脚本写死角色 + 只认 `pending`；修好后 +631、再 +828 份。
  2. ~~「必要小节覆盖率 8/9 是语料限制，别为凑覆盖率放宽判据」~~ → **错**。是 `MODULE_KEYWORDS` 词形太窄（真风险小节写「风险**管理**/风险**点**/风险**预案**/突发风险」）；**这是修关键词覆盖面，不是放宽 `ok` 判据**（仍要求 ≥3 条标题命中）。修后 **9/9**。
  3. ~~「社保/财务条目是『起始期间』语义，按月查返回 0」~~ → **过时**。主体是具体年月（社保 70.2%、财务 75.9%）。
- **改 `app/extract.py` 前先查模块级常量重名**（已撞过一次 `_SIGN_LINE`）。
- ⚠️ **我犯过的错，别重犯**：① 先给结论、后补验证（多次，含指标预测被实测推翻）；② 测量工具本身有错（未施加产品排除 → 误报「误返 2」，实际 0）；③ 把「解析器丢了文档明写信息」误归因为「OCR 质量」（实为 4 个解析 bug）；④ 在 JS 里写 Python 方法。
  **归因之前先看原文；报数字之前先验口径。**

## 10. Minimum Recovery Context

1. 本文件
2. `../bid-ai/MINIMAL_REBUILD_PLAN.md` —— §2（需求定义 + 金额口径修订）、§10、§11A
3. `app/api.py`、`app/search.py`、`app/proposal.py`（三个最后活跃实现）
4. `scripts/eval_gold_recall.py`、`scripts/eval_success_precision.py`（两个常驻检查）
5. `docs/llm-generation-authorization.md`（外发范围）、`docs/api.md`（接口契约）

## 11. Git State

- **分支 `feat/info-architecture-rework`，HEAD `3b52af9`**（2026-09-14 用户指示「把他们纳入一次提交」）。
  这是**本项目的第一个正式回滚点**：106 个文件 / +28,912 行，含 `app/`、`static/`、`tests/`、
  `scripts/`、`docs/`、`data/`。**`main` 仍停在 `cc28082`（R1 骨架）**，未动。
  ⚠️ **在此之前 `static/` 与 `tests/` 从未进过 Git**（不是被忽略，是没 `add` 过）——
  所以那之前"用 `git checkout` 回滚前端"是**空话**（对未跟踪文件无效）。现已解决。
- **提交前做过密钥扫描**（`sk-*` / `api_key=` / `Bearer` / `password=`）—— 干净；`data/` 只有
  文件白名单与评测产物（无敏感内容）。
- `git status --short`：**0 行**（工作区干净）。
- **禁提交 / 保持忽略**：`.env`（**含真实 key**）、`bid_ai_clean_reg*.bak*.db`（26 个写前备份，441MB）、
  `*.db`、`tmp/`、`outputs/`、`tmp_probe/`、`data/ocr_batch_state.json`、`*.log`。
  ⚠️ `outputs/` **必须保持忽略** —— `r7-*/prompt.md` 与评审样本是**真实投标正文**。
  ⚠️ **`.gitignore` 不支持行尾注释**（注释必须独立成行，否则规则静默失效——已踩过）。
  ⚠️ 行尾：本机 `core.autocrlf` 会在提交时把 CRLF 归一为 LF（提交时有大量 warning，属正常）。
- 数据库备份**已按用户确认瘦身**：50 个 → **26 个**（保留被脚本具名读取的 + 最近回滚点）。
- **回滚方式**：`git checkout -- <path>` / `git revert 3b52af9` / `git checkout main` 回到骨架。
  另有文件级快照 `tmp/pre-p0-20260914/`（P0 改动前），**仅作双保险、不是主回滚手段**。

## 12. Recovery Command

> **当前状态**：需求一/二**全部退出门槛已达标**（§1 表）；B2B 四维评审 P0/P1/P2 已全部处理；
> **需求二只保留模型生成**（2026-09-14 用户指令，local 已下线）；招标核对功能**已由用户暂停**。
> **没有任何正在进行的任务。**
>
> **下一步需用户指定**，候选（都不是 bug，是决策）：
> ① **方案质量的业务人工评审**（corresponding §7 一直挂着；评审包已改 llm 产出，`--dry-run` 确认后取样）；
> ② 需求方试读反馈的回收与消化；
> ③ `app/api.py` 拆 APIRouter（结构改动，值得单独一轮）；
> ④ `tmp_probe/` 纳入 `.gitignore`（提交前的杂事，5 分钟）。
>
> 动手前：① 跑 §6 三条命令确认基线未回退（当前应为 **200 passed** / Recall **92.3%** / 误返 **0**）；
> ② 读 §9 的三条「曾被推翻的旧结论」，**不要照抄旧版交接文档的判断**；
> ③ 改完接口**重启 8000 端口**（旧进程残留会让你验证到旧代码，§6 有实测教训）。
