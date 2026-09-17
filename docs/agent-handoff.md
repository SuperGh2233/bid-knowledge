# Agent Handoff — bid-ai-clean

> 更新 **2026-09-17**（按本技能 §1–§13；旧版见 `git show HEAD:docs/agent-handoff.md`）。
> 权威：仓库根 `AGENTS.md`、`docs/index.md`、§2 列出的文档。**冲突时以仓库与实测为准**。

## 1. Current Goal

交付两个能力（**只这两个**）：**需求一** 历史材料定位；**需求二** 模块级方案生成。

**当前任务**：修「金标准 Recall 未达标」—— 根因是 5 份合同「明细里没有目标产品、**合同正文写着**」，
检索一条都不产出。**「正文提及」组已实现并上线（用户 2026-09-17 已拿去演示）**；
剩余**全是口径更正与文档同步**，不动已上线的行为。

退出门槛：Recall（口径见 §8，数字需按新分母重述）／真实路径 100%／金额条件 100%／覆盖率 8/8。
**新增口径**：正文提及组实测 **Recall 37/37 = 100%、误返 1（该 1 条经查为金标准瑕疵）**。

## 2. Linked Authoritative Documents

| 文档 | 控制什么 |
|---|---|
| `AGENTS.md` | 环境、命令、红线（**服务端硬开关默认全 false**、外发授权 6 份、NAS 只读、正式库禁写） |
| `docs/index.md` | 文档地图 + 状态列 |
| `docs/plans/completed/PLAN-20260916-track-record-search.md` | **completed（冻结）**：业绩清单链路 |
| `docs/plans/active/PLAN-20260915-demo-feedback-issues.md` | 需求方 5 条反馈的权威记录 |
| `docs/evals/EVAL-20260917-gold-recall-regression.md` | **Recall 复测报告** —— ⚠️ 其中「92.3%→75.7% 是退化」**已证伪**（实为**分母从 26 扩到 37**），待更正 |
| `docs/authorizations/llm-contract-mention-authorization.md` | 第 6 份授权，**状态：暂缓启用**（本地规则已达同等召回，无需外发） |
| `docs/specs/api.md` | HTTP 契约 —— ️ **尚未同步本轮新字段**（见 §5 文档债） |

**尚无**：`docs/plans/active/PLAN-20260917-contract-product-mention.md`（本轮任务的权威计划）—— **未建，属文档债**。

## 3. Current Progress

**Completed + Verified（2026-09-17 实测）**
- **正文提及组已上线**（`app/search.py::locate_mention_contracts` + `routes_search.py` 接线 + `static/app.js::mentionBlock`）：
  实测 `找50万以上的代谢组合同` → 明细 3 + 提及 **7**；`代谢合同，不要蛋白组和宏基因组合同` → 明细 14 + 提及 **2**。
- **判据**：产品词出现在**业务上下文**（±12 字内有 测序/检测/分析/服务/组学/样本/项目/实验/建库/上机/合同/委托）。
  复验：**Recall 37/37 = 100%、误返 1**，**零外发**。
- **四条硬约束**（已写进代码注释）：① 不改 `amount`/门槛通路、不参与金额筛选；② **永不进 `hits`**；
  ③ 排除条件**按合同级作用于正文**（实测 G05 误返 10→1）；④ 带正文依据上屏 + 可打开文件。
- `pytest tests -q` → **298 passed**；服务 PID **102300** 在跑（含新代码）。

**In progress**：无（行为部分已收口）。
**Not started**：本轮计划文档、评测报告口径更正、CHANGELOG、`api.md` 同步、tag。
**Rejected（勿重开）**：LLM 外发复核（本地规则已达同等召回，代价不值，授权已标暂缓）；
位置规则（前 1/3）；"邻近词过滤无效"这一结论（**我口径算错**，已证伪）。

## 4. Changes Made（本轮，**均未提交**）

| 文件 | 改了什么 |
|---|---|
| `app/search.py` | 新增 `locate_mention_contracts()` + `_mention_in_context()` + `MENTION_CTX_*` 常量；**刻意不接受金额门槛参数**（守「不参与筛选」） |
| `app/routes_search.py` | `_contract_search_body` 加 `mention_contracts` + `mention_note`；排除集用已命中的 `document_id` |
| `static/app.js` | 新增 `mentionBlock()`（独立分组、琥珀色边、`正文提及` 标记、依据原文、合同总额口径），插在明细命中之后 |
| `static/style.css` | `.mention-block` / `.mention-card` / `.mention-evidence` / `.tag-warn` |
| `tests/test_track_records.py` | 两条接线测试补 `locate_mention_contracts` stub（新接线是 additive） |
| `docs/authorizations/llm-contract-mention-authorization.md` | 头部标「**暂缓启用**」；§三失效结论就地更正；§七补"将来启用前必须补脱敏" |

## 5. Technical Decisions and Documentation Debt

**已定（用户 2026-09-17 逐条拍板）**：信号只在检索层（不写库）／**不参与金额筛选**（避免撞「产品金额=明细行之和」）／
单独一组放在明细命中下面／本地业务上下文规则（**不走 LLM**）／排除条件合同级作用／开关默认开／
G05 那条误返认定为**金标准瑕疵**（标注自相矛盾）。

**文档债（下一动作）**：
1. **本轮无权威计划文档** —— 12 条决策只在对话里，须落 `docs/plans/active/PLAN-20260917-contract-product-mention.md`。
2. `docs/evals/EVAL-20260917-*` 的「退化」结论须更正为「**分母口径变化**（26→37）」；G05 误返须注明金标准矛盾。
3. `docs/specs/api.md` 未同步 `mention_contracts` / `mention_note`；§2A 也未提这一组。
4. `CHANGELOG.md` 未记本轮；`AGENTS.md` 需补「正文提及」这一概念。
5. 审计查出的既有缺口：仓库**无 `.env.example`**（README 与 AGENTS.md 都写"从它复制"）；`api.md §0` 仍写"仅两处会外发"。

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

### Confirmed problems
- **`docs/evals/EVAL-20260917-*` 的结论错了**：写成"Recall 92.3%→75.7% 是退化"，实为**分母从 26 扩到 37**
  （新纳入的 11 条可检索记录只召回了 4 条）。**照该报告对外讲会讲错方向。**
- **G05 那 1 条"误返"是金标准自相矛盾**：`YOE2024080476` 与 `YOE2024082739` 正文产品词几乎相同、
  G05 措辞未排除空间转录组/单细胞，却一个判 relevant、一个判 irrelevant；且前者的 `product_normalized`
  自己写的就是「代谢组」。**已按用户裁定认定为金标准瑕疵、不计入误返**（须在评测报告里如实记录）。
- **仓库混进一个非项目文件**：`tests/标书文库 - 快捷方式.lnk`（795B，2026-09-17 13:23 出现，未被 gitignore）
  —— 疑似误拖入，**不要提交**（处置见 §9 第 1 条）。

### Unverified risks / assumptions
- 提及组的产品键用**传入的 keywords 元组**做子串匹配；窄优先顺序由调用方保证（与 `parse_demo_query` 同源）。
- `_mention_in_context` 的上下文词表是手写的（12 词）—— 与"手写词表会漂移"的历史教训同源；
  将来若发现漏挡，**应改成按句判定，而不是往表里加词**。
- `/api/three-modules` 的「项目业绩」段**未加**提及组（该段无产品维度）。用户此前希望三处都有，
  **这一条与实现不一致，待用户确认**。

## 9. Next Actions

1. **处置误入的非项目文件**（先问用户，勿擅自删）：
   `tests/标书文库 - 快捷方式.lnk` —— 确认是否误拖入；确认后再删或加 `.gitignore`。
2. **提交并推送本轮改动**（待第 1 条处理干净后）：
   ```bash
   cd "C:/Users/hao.guo/Desktop/标书文库/bid-ai-clean"
   git add app/search.py app/routes_search.py static/app.js static/style.css tests/test_track_records.py \
           docs/authorizations/llm-contract-mention-authorization.md
   git commit -m "feat(检索): 「正文提及」合同组上线（本地规则，零外发）"
   git push origin main && git push github main
   ```
3. **更正 `docs/evals/EVAL-20260917-gold-recall-regression.md`**：把"退化"改为"**分母口径变化**"；
   补 G05 金标准矛盾的证据（两条对照 + `product_normalized` 自述）；写明新基线 Recall 100% / 误返 0（附瑕疵说明）。
4. **建本轮权威计划** `docs/plans/active/PLAN-20260917-contract-product-mention.md`：落 12 条决策 + 四条硬约束 + 验收口径。
5. **同步 `docs/specs/api.md`**（`mention_contracts`/`mention_note`）、`CHANGELOG.md`、`AGENTS.md`（补概念）、
   新建 `.env.example`；完成后按用户意见打 tag（建议 `v1.2`）。

## 10. Do Not Repeat / Do Not Change

- **不重开**：LLM 外发复核（授权已标暂缓，代码里**无任何触发路径**）；位置规则（前 1/3）；
  「邻近词过滤无效」这一结论（**我口径算错**）；业绩清单第二步三项候选（已用证据结案）。
- **不改**：正式库；资格门槛**顺序**；产品金额口径（提及组**不参与筛选**，不触碰此红线）；
  `_FORMAT_RANK` 的 `mixed` 权重位；**不得把提及组混进 `hits`**。
- **不删**：ES `bid_chunks_v2`；`tests/test_contract_items.py::test_amount_unknown_stays_unknown_not_zero`。
- **需新授权才能做**：任何把合同/响应正文送往外发网关的动作（第 6 份授权明确「用户指示后方可执行」）。

## 11. Minimum Recovery Context

1. 本文件 + `AGENTS.md` + `docs/index.md`
2. `docs/evals/EVAL-20260917-gold-recall-regression.md`（⚠️ 结论待更正）+ `docs/authorizations/llm-contract-mention-authorization.md`
3. 本轮实现：`app/search.py::locate_mention_contracts`、`app/routes_search.py::_contract_search_body`、`static/app.js::mentionBlock`
4. `docs/plans/completed/PLAN-20260916-track-record-search.md`（业绩清单链路，冻结参考）

## 12. Git State

- **分支** `main`；**HEAD** `531542b`（`docs(auth): 第 6 份外发授权`）。
- **未提交**：6 个修改文件 + 1 个**误入的非项目文件**（`tests/标书文库 - 快捷方式.lnk`）。
- **未推送**：无（`531542b` 已推 GitLab；GitHub 上次网络故障 —— 见 §9 第 2 条一并推）。
- **tag**：`v1.0.0` / `v1.1` / `v1.1.1` / `v1.1.2` / `minimal-rebuild-r1-20260907`。
- **禁提交**：`.env`、`*.db`、`bid_ai_clean_reg.bak-*.db`（26 个）、`outputs/`（真实投标正文）、`tmp/`、`*.log`。
  **`tmp/` 下有本轮 20 余个探针脚本**（`probe_*.py` / `verify_*.py` / `diagnose_*.py`）—— 一次性产物，不入库。

## 13. Recovery Command

> **当前状态**：正文提及组**已上线并验证**（Recall 100% / 误返 0（含 1 条金标准瑕疵说明）/ 零外发），
> 用户已拿去演示；**行为部分无需再改**，剩余全是**口径更正与文档同步**（§9）。

`Invoke $resume-work in this repository, verify AGENTS.md, docs/index.md, linked authoritative documents, Git state, and docs/agent-handoff.md, then continue from Next Actions item 1.`