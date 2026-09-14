# 授权记录：模块级方案生成（需求二 / 计划 R7）

> 2026-09-11 建立。**本记录只覆盖「方案生成」这一步**，不改动既有两份授权记录的范围。

## 1. 授权事项

把 **`our_response` / `final_signed` 的命中章节正文**（连同来源文件名、项目名、标题）
送往外发 LLM 网关，用于生成方案正文。

## 2. 授权依据

- **2026-09-11 用户明确指示**：「不再请求我的同意 我给你全部授权 全力推进我的项目 完成我的两个需求」。
- 该指示由**用户本人**（本系统全部授权记录的设置者）作出，
  故对「方案正文外发」这一此前未覆盖的范围构成有效授权。
- ⚠️ **性质说明（据实）**：本记录记录的是**对话中的授权**，
  不是用户在工作簿/邮件中签署的书面件。若贵司合规要求书面签署，
  请以此为基础补签；在此之前，本能力**默认仍为关闭**。

## 3. 范围（严格限定）

**允许**：
- 输入：`build_evidence_packs()` 产出的 Evidence Pack 中的章节正文，
  **仅限** `document_role ∈ {our_response, final_signed}`；
- 输出用途：生成方案草稿供人工编辑。

**不允许**：
- 竞品响应（`competitor_response`）、招标要求（`tender_requirement`）、
  未知文件、空模板 —— **代码层已过滤**（`VALID_ROLES`），不是靠约定；
- 扫描件 OCR（那是 `docs/ocr-authorization.md` 的范围，本记录不覆盖）；
- **MinerU 等公网第三方兜底** —— 本链路**不使用**任何公网兜底，只走 `.env` 配置的网关。

## 4. 网关

- `.env` 的 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（公司自有网关 `newapi.oebiotech.com`）。
- 超时 `PROPOSAL_GEN_TIMEOUT`（默认 300 秒）；`temperature=0`（可复现优先）。

## 5. 开关与默认值

- `config.PROPOSAL_GEN_ENABLED`，**默认 `false`**。
- 关闭时 `generate_proposal()` 抛 `ProposalGenNotAuthorized`，**不会**有任何外发。
- 这套「默认关闭、未授权即抛错」与 `app/ocr.py` 的 `_guard()` 完全一致，
  目的都是**避免"未授权却默默外发"**。

## 6. 计划要求对应

| 计划项 | 落点 |
|---|---|
| R7-05 只把命中的原文、来源和用户约束交给模型 | `build_gen_prompt()` |
| R7-06 输出方案、引用列表、冲突/缺口警告 | `generate_proposal()` 返回 `markdown` / `citations` / `warnings` / `gaps` |
| R7-07 生成后校验必要小节、引用存在、关键数字来自证据 | `validate_generation()` |
| D12 数字冲突只告警不自动择一 | `detect_conflicts()`（与项目红线一致） |

## 7. 未决

- 生成质量的**人工抽检**尚未做（首次生成后须由业务同事评审）。
- 本记录不覆盖后续新增的文件角色或新的数据源。

---

## 8. 补充说明：模块化经验段也进入提示词（2026-09-13）

用户 2026-09-13 明确需求二的做法：「在以往的投标文档中**整理出模块化的内容** …… 用户让生成方案时
是要把这些**模块化的内容当作提示词发给 llm**，让他根据这些模块化的经验去生成方案」。

**为此新增的内容**：提示词里多了一段【我司历史模块化经验】——由 `build_module_kb()` 产出，
内容是每个方案模块的**跨项目归纳**：常用小节标题、承诺时限槽位（含「历史不一致」标注）、覆盖度。

**为什么这不扩大外发范围**：
1. **归纳本身零外发**。`build_module_kb()` 全程本地（ES BM25 召回 + 确定性归纳：
   字符 shingle 聚类、`_slot_of`/`_UNIT_MINUTES` 单位归一），**不调用任何模型**。
   可在 `/api/module-kb` 单独查看，不触发生成。
2. **外发的原文范围一字未变**：仍然只有 `build_evidence_packs()` 产出的
   `our_response` / `final_signed` 命中章节正文（§3 的同一份白名单，代码层过滤）。
   归纳段是**这些同一批正文的再组织**，不引入任何新数据源。
3. **归纳段显式禁止被引用**：`kb_to_prompt_block()` 在段首写明「引用时必须用下面证据包里的编号，
   **不得引用本段**」，避免模型把归纳句当原文引用（那会破坏可追溯性）。

**开关与默认值不变**：仍由 `config.PROPOSAL_GEN_ENABLED` 控制，**默认 `false`**；
关闭时 `generate_proposal()` 抛 `ProposalGenNotAuthorized`，**不会有任何外发**。

⚠️ **部署前提**：`bid-ai-clean` 目录**没有 `.env`**，`app/config.py` 直接读环境变量、
不加载 `.env`。要真正启用生成，需运维在启动环境里提供
`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 并设 `PROPOSAL_GEN_ENABLED=true`。

**回退**：`PROPOSAL_GEN_ENABLED=false` 即完全回退；提示词里的经验段由 `generate_proposal(kb=None)`
单独回退（整理能力本身保留，因为它零外发）。

---

## 9. 首次真实生成记录（2026-09-14）

**用户指示**：「把 env 建起来，开一次真实的生成，就用我配置的 qwen3.7-flash 模型就行，我也配置了 key。」

**环境变更**：
- 新建 `bid-ai-clean/.env`（已被 `.gitignore` 排除），键值从 `../bid-ai/.env` 搬运：
  `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL=qwen3.7-flash` / `ELASTICSEARCH_URL`。
- `PROPOSAL_GEN_ENABLED=true`。
- ⚠️ **同时修了一个潜在缺陷**：`app/config.py` 原先**不加载 `.env`**（只读 `os.environ`），
  于是 README 说的「从 `.env.example` 复制」建出来的 `.env` **完全不生效**。
  已补 `load_dotenv(BASE_DIR/".env", override=False)`（真实环境变量优先）。

**实际生成**（`POST /api/proposal-generate`，`mode=llm`，查询「售后服务方案，必须包含服务周期和应急预案」）：

| 项 | 值 |
|---|---|
| 结果 | HTTP 200，**mode=llm**，`kb_used=true` |
| 提示词 | 13,655 字符（含模块化经验段 + 证据包） |
| 模型 | `qwen3.7-flash`（公司网关，temperature=0） |
| 耗时 / 产出 | 25.7 秒 / 6,393 字 |
| 引用 | **10 条**（`E1..E10`，全部可回溯到真实文件与标题） |
| 冲突告警 | **2 条**（应急预案的「响应」10分钟 vs 4小时；「完成」24小时 vs 72小时） |
| 缺口 / 校验问题 | 0 / 0 |

**质量观察（首次人工抽检）**：
- 正文明设了一节「**响应时间与完成时限（存在冲突说明）**」，把不同来源的时限**逐条并列、未择一**
  —— 与 D12 红线一致，这是最能说明"约束在起作用"的一处。
- 10 条引用均可对应到真实文件（`欧易响应文件.docx`、`02 商务技术部分.docx` …）。
- **未做**：业务同事的正式评审（§7 第 1 条仍未决）。

**同时修掉的缺陷**：`generate_proposal()` 原先**不返回 `mode`**（返回 `undefined`）→
前端 `MODE_LABEL[undefined]` 取不到标签、页面显示不出"这是模型起草的"。
产品红线要求「`mode` **必须展示给用户**」。该缺陷一直存在但**从未暴露**——
因为 LLM 路径此前从未被启用过，**首次真实生成才发现**。已补 `mode="llm"` 并加测试钉住。
