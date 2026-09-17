# Agent Handoff — bid-ai-clean

> 更新 **2026-09-17（下午，第二轮）**（按本技能 §1–§13；旧版见 `git show HEAD~1:docs/agent-handoff.md`）。
> 权威：仓库根 `AGENTS.md`、`docs/index.md`、§2 列出的文档。**冲突时以仓库与实测为准**。

## 1. Current Goal

交付两个能力（**只这两个**）：**需求一** 历史材料定位；**需求二** 模块级方案生成。

**当前任务**：「正文提及」组本轮**全部收口** —— 行为上线（用户已拿去演示）+ 代码提交 `de4c0d8` +
文档同步提交 `4168be4`（均已推 origin/github）+ **tag `v1.2` 已打并推送**。
剩余唯一未决：`/api/three-modules` 业绩段是否也加提及组（用户 2026-09-17 **裁定「暂不决定」**，
保留在 §8 待确认清单，**勿主动开工**）。

退出门槛：**Recall 37/37 = 100%（新分母，明细 28 + 提及 9；口径与证据见 EVAL §7）**／真实路径 100%／
金额条件 100%／覆盖率 8/8。误返 **0**（表面 1 条系金标准瑕疵，EVAL §7.3）。

## 2. Linked Authoritative Documents

| 文档 | 控制什么 |
|---|---|
| `AGENTS.md` | 环境、命令、红线（**服务端硬开关默认全 false**、外发授权 6 份、NAS 只读、正式库禁写） |
| `docs/index.md` | 文档地图 + 状态列 |
| `docs/plans/completed/PLAN-20260916-track-record-search.md` | **completed（冻结）**：业绩清单链路 |
| `docs/plans/active/PLAN-20260917-contract-product-mention.md` | **本轮任务的权威计划**（12 条决策 + 四条硬约束 + 验收，已落档）|
| `docs/plans/active/PLAN-20260915-demo-feedback-issues.md` | 需求方 5 条反馈的权威记录 |
| `docs/evals/EVAL-20260917-gold-recall-regression.md` | **Recall 复测报告** —— ✅ 已更正：「92.3%→75.7% 退化」证伪（实为分母 26→37）；§7 记新基线 **37/37=100%** 与 G05 金标准瑕疵证据 |
| `docs/authorizations/llm-contract-mention-authorization.md` | 第 6 份授权，**状态：暂缓启用**（本地规则已达同等召回，无需外发） |
| `docs/specs/api.md` | HTTP 契约 —— ✅ 已同步 §2B（`mention_contracts` / `mention_note`）与 §0 外发声明（6 份授权口径）|

## 3. Current Progress

**Completed + Verified（2026-09-17 实测）**
- **正文提及组已上线**（`app/search.py::locate_mention_contracts` + `routes_search.py` 接线 + `static/app.js::mentionBlock`）：
  实测 `找50万以上的代谢组合同` → 明细 3 + 提及 **7**；`代谢合同，不要蛋白组和宏基因组合同` → 明细 14 + 提及 **2**。
- **判据**：产品词出现在**业务上下文**（±12 字内有 测序/检测/分析/服务/组学/样本/项目/实验/建库/上机/合同/委托）。
  复验（`tmp/verify_rules_g05.py` 第二轮实跑）：**Recall 37/37 = 100%、误返 0**（表面 1 条 = 金标准瑕疵），**零外发**。
- **四条硬约束**（已写进代码注释）：① 不改 `amount`/门槛通路、不参与金额筛选；② **永不进 `hits`**；
  ③ 排除条件**按合同级作用于正文**（实测 G05 误返 10→1）；④ 带正文依据上屏 + 可打开文件。
- `pytest tests -q` → **298 passed**；服务 PID **102300** 在跑（含新代码）。

**In progress**：无（行为 + 文档 + 发布均已收口）。
**Not started**：`/api/three-modules` 业绩段加提及组（用户**暂不决定**，见 §8）。
**Rejected（勿重开）**：LLM 外发复核（本地规则已达同等召回，代价不值，授权已标暂缓）；
位置规则（前 1/3）；"邻近词过滤无效"这一结论（**我口径算错**，已证伪）。

## 4. Changes Made（本轮）

**已提交推送**：`de4c0d8`「feat(检索): 「正文提及」合同组上线（本地规则，零外发）」
（代码 6 文件 + 本交接文档第一轮版；origin/GitLab 与 github 均已推成功）。

**第二轮已提交推送**：`4168be4`「docs: 「正文提及」轮文档同步」（tag `v1.2` 打在此提交上）：

| 文件 | 改了什么 |
|---|---|
| `docs/plans/active/PLAN-20260917-contract-product-mention.md` | **新建**：本轮权威计划（12 条决策 + 四条硬约束 + 验收 + 已知局限）|
| `docs/evals/EVAL-20260917-gold-recall-regression.md` | 标题/导语更正（「退化」证伪 → 分母 26→37）；新增 §7 修法落地 + 复测 37/37 + G05 瑕疵证据 + 新基线；文末修订记录 |
| `docs/specs/api.md` | 新增 §2B「正文提及」组契约（三条红线 + 字段 + 排除语义）；§0 外发声明按 6 份授权更正 |
| `CHANGELOG.md` | 新增 v1.2 条目（**tag 未打**，待用户点头）|
| `AGENTS.md` | 业务语义补「正文提及」组三条铁律；代码地图 search.py 注释补 `locate_mention_contracts` |
| `.env.example` | **新建**（README/AGENTS.md 一直引用但仓库缺失）；全部开关按安全默认值 |
| `docs/index.md` | Plans/Evals/Authorizations 三处状态行同步 |
| `docs/agent-handoff.md` | 本文件（第二轮重写）|

## 5. Technical Decisions and Documentation Debt

**已定（用户 2026-09-17 逐条拍板）**：信号只在检索层（不写库）／**不参与金额筛选**（避免撞「产品金额=明细行之和」）／
单独一组放在明细命中下面／本地业务上下文规则（**不走 LLM**）／排除条件合同级作用／开关默认开／
G05 那条误返认定为**金标准瑕疵**（标注自相矛盾）。

**文档债**：~~5 项~~ → **已全部清完**（2026-09-17 第二轮）：计划文档已建、评测报告已更正、
`api.md` 已加 §2B、CHANGELOG 已记 v1.2（**tag 未打**）、`AGENTS.md` 已补概念、`.env.example` 已新建。

## 6. Contracts and Constraints

- **库**：活库 `bid_ai_clean_reg.db`；正式库 `bid_ai_clean.db` **禁写**。服务**纯只读**（12 端点全走 `mode=ro`）。
- **端点 12 个**（本轮**未新增端点**，只在既有响应里加字段 → 只读契约未变）。
- **`mention_contracts` 字段**：`{contract_id, contract_number, party_a/b, contract_date, total_amount,
  document_id, relative_path, project_folder, content_format, document_role, file_name, source_path,
  product, mention_source, amount_note, evidence_snippet}`。
- **红线不变**：产品金额 = 同产品明细行之和；`contracts.total_amount` **不得**参与产品金额判断 ——
  正文提及组是「**展示线索**，不参与筛选」，故不触碰该红线。
- 外发：**6 份授权**，第 6 份**暂缓启用**；本轮**零外发**。

## 7. Tests and Verification

```bash
CONDA="C:\Users\hao.guo\AppData\Local\miniconda3\envs\langchain-dev\python.exe"
export BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db"
"$CONDA" -m pytest tests -q     # 298 passed（本文件写作时实测）
"$CONDA" -m app.api             # → http://127.0.0.1:8000
```
- **服务**：PID **102300** 在跑；`GET /api/status` → `合同 136 / 可查 136 / 业绩行 408`。
- **前端改动后需 Ctrl+F5 一次**（静态文件已加 `no-cache`，但浏览器手里那份旧 JS 要硬刷丢掉）。
- ⚠️ 改代码必须重启并核 PID/启动时间（旧进程照样返回 200 = 旧代码）。

## 8. Problems and Risks

### Confirmed problems（均已处置）
- ~~评测报告「退化」结论错了~~ → **已更正**（EVAL §7 + 标题/导语 + index.md 状态行）。
- ~~G05 那 1 条"误返"是金标准自相矛盾~~ → 用户裁定为金标准瑕疵、不计入误返；**证据已落 EVAL §7.3**
  （实测复核：G03/G04 里两条**都是** relevant，仅 G05 把 `YOE2024080476` 标 irrelevant；其
  `product_normalized` 自述「代谢组」）。金标准文件**未改**。
- ~~仓库混进非项目文件 `tests/标书文库 - 快捷方式.lnk`~~ → **用户确认后已删除**（2026-09-17 第二轮）。

### Unverified risks / assumptions
- 提及组的产品键用**传入的 keywords 元组**做子串匹配；窄优先顺序由调用方保证（与 `parse_demo_query` 同源）。
- `_mention_in_context` 的上下文词表是手写的（12 词）—— 与"手写词表会漂移"的历史教训同源；
  将来若发现漏挡，**应改成按句判定，而不是往表里加词**。
- `/api/three-modules` 的「项目业绩」段**未加**提及组（该段无产品维度）。用户此前希望三处都有，
  与实现不一致 —— **用户 2026-09-17 裁定「暂不决定」**：维持现状，勿主动开工；将来若做，
  按 `PLAN-20260917-contract-product-mention.md` §6 走。

## 9. Next Actions

1. ~~提交并推送文档同步~~ → **已完成**（`4168be4`，origin + github 均已推）。
2. ~~打 tag `v1.2`~~ → **已完成**（打在 `4168be4`，两个远程均已推）。
3. **唯一未决（用户裁定「暂不决定」，勿主动开工）**：`/api/three-modules` 业绩段是否也加提及组（§8 第 3 条）。
   若将来要做，按 `PLAN-20260917-contract-product-mention.md` §6 走。
4. 本轮**无剩余动作**；下一个任务从需求方反馈或用户指示开始。

## 10. Do Not Repeat / Do Not Change

- **不重开**：LLM 外发复核（授权已标暂缓，代码里**无任何触发路径**）；位置规则（前 1/3）；
  「邻近词过滤无效」这一结论（**我口径算错**）；业绩清单第二步三项候选（已用证据结案）。
- **不改**：正式库；资格门槛**顺序**；产品金额口径（提及组**不参与筛选**，不触碰此红线）；
  `_FORMAT_RANK` 的 `mixed` 权重位；**不得把提及组混进 `hits`**。
- **不删**：ES `bid_chunks_v2`；`tests/test_contract_items.py::test_amount_unknown_stays_unknown_not_zero`。
- **需新授权才能做**：任何把合同/响应正文送往外发网关的动作（第 6 份授权明确「用户指示后方可执行」）。

## 11. Minimum Recovery Context

1. 本文件 + `AGENTS.md` + `docs/index.md`
2. `docs/plans/active/PLAN-20260917-contract-product-mention.md`（本轮权威计划）+
   `docs/evals/EVAL-20260917-gold-recall-regression.md` §7（最新结论）
3. 本轮实现：`app/search.py::locate_mention_contracts`、`app/routes_search.py::_contract_search_body`、`static/app.js::mentionBlock`
4. `docs/plans/completed/PLAN-20260916-track-record-search.md`（业绩清单链路，冻结参考）

## 12. Git State

- **分支** `main`；**HEAD** `4168be4`（文档同步，**已推 origin + github**）；工作区**干净**（除禁提交项）。
- **tag**：`v1.0.0` / `v1.1` / `v1.1.1` / `v1.1.2` / **`v1.2`（本轮，打在 `4168be4`）** / `minimal-rebuild-r1-20260907`。
- **禁提交**：`.env`、`*.db`、`bid_ai_clean_reg.bak-*.db`（26 个）、`outputs/`（真实投标正文）、`tmp/`、`*.log`。
  **`tmp/` 下有本轮 20 余个探针脚本**（`probe_*.py` / `verify_*.py` / `diagnose_*.py`）—— 一次性产物，不入库
  （其中 `verify_rules_g05.py` 是 EVAL §7.2 复测数字的来源，报告里已注明）。

## 13. Recovery Command

> **当前状态**：正文提及组本轮**全部收口** —— 上线验证（Recall 37/37=100% / 误返 0 / 零外发）、
> 代码 `de4c0d8` + 文档 `4168be4` 均已提交推送、tag `v1.2` 已打。**无剩余动作**；
> 唯一未决 = three-modules 业绩段是否加提及组（用户暂不决定，勿主动开工）。

`Invoke $resume-work in this repository, verify AGENTS.md, docs/index.md, linked authoritative documents, Git state, and docs/agent-handoff.md, then continue from Next Actions item 1.`