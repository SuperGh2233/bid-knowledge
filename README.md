# bid-ai-clean

投标材料智能检索与方案生成系统（最小重建版）。执行路线见 `docs/plans/legacy/MINIMAL_REBUILD_PLAN.md`，
决策记录见 `docs/plans/legacy/MINIMAL_REBUILD_PLAN-DECISIONS.md`（两份原属旧系统仓库，2026-09-16 复制入本仓库）。

**只交付两个能力**（不得扩第三条主链路）：

1. **历史材料定位** —— 自然语言查询 → 满足条件的**我方响应文件** + 内部业务记录
   （合同产品金额 / 财务社保月份 / 仪器设备 / 方案章节），卡片可直接打开源文件。
2. **模块级方案生成** —— 按 8 个方案模块检索历史我方证据 → 受证据约束生成带出处的草稿。

## 当前状态（2026-09-16，实测）

| 指标 | 值 |
|---|---|
| 退出门槛 | 全部达标：Recall **92.3%** / 真实路径 **100%** / 金额条件 **100%** / 误返 **0** / 覆盖率 **8/8** |
| Success@5 / Precision@10 | **100%** / **93.3% 下界**（可判定查询仅 3 个，样本少）|
| 测试 | `pytest tests -q` → **267 passed** |
| 语料 | `documents 6,043` / `contracts 165`（已核可查 **136**）/ `contract_items 649` / `material_facts 1,986` |

## 运行

**必须用 conda 环境 `langchain-dev` 的解释器**（PATH 上的 python 没有 pytest）：

```bash
CONDA="C:\Users\hao.guo\AppData\Local\miniconda3\envs\langchain-dev\python.exe"
export BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db"      # 演示库；正式库 bid_ai_clean.db 禁写

"$CONDA" -m app.api                  # → http://127.0.0.1:8000（只读预览）
"$CONDA" -m pytest tests -q          # 单元/回归（无需 ES、无需 key）
"$CONDA" scripts/eval_gold_recall.py # 金标准召回评测
"$CONDA" scripts/refresh_catalog.py --dry-run   # 增量登记 Z 盘新文件（只读预览）
```

`.env` 从 `.env.example` 复制（密钥只从环境变量读，不落库）。
Elasticsearch 8.x 需在 `http://localhost:9200`（方案章节索引 `bid_scheme_sections_v1`）。

## 仓库与远端

| 远端 | 地址 | 说明 |
|---|---|---|
| `origin` | `gitlab.oebiotech.com:ai-project/bid-ai` | 公司内网；**HTTPS + 访问令牌已配好**，`git push` 免交互 |
| `github` | `github.com/SuperGh2233/bid-knowledge` | 个人库，**已设为私有**（2026-09-16）|

两侧 `main` 与 tag `v1.0.0` 内容一致；本地 `main` 与远端同步。
旧系统（R0 冻结基线，含全部 12 个提交的 git 历史与 tag）**已于 2026-09-16 移出本工作区**，归档在
`C:\Users\hao.guo\Desktop\标书文库-旧系统归档\`（`bid-ai\` 旧代码 + `bid-ai-r0-snapshot\` R0 数据快照），
**不再往远端维护**；其两份权威计划文档已复制进本仓库 `docs/`（见文首）。

## 边界（红线）

- **共享盘只读**：`Z:\` / `\\192.168.10.188\…` 只读；派生物只写本地 `data/`、`tmp/`、`*.db`。
- **正文外发需授权**，现有四份授权记录（范围不可自行扩大）：
  `docs/authorizations/ocr-authorization.md`、`docs/authorizations/ocr-authorization-response-docs.md`、
  `docs/authorizations/llm-generation-authorization.md`、`docs/authorizations/llm-intent-authorization.md`。
  - OCR 走网关（扫描件）；方案生成走网关（我方响应正文）；
  - **查询意图识别只外发用户那一句查询**，且**本地优先、判不出才问模型**（默认关闭）。
- **方案生成证据边界**：只允许 `our_response` / `final_signed`；招标要求、竞品、空模板、未知
  一律不得作为我方承诺。数字冲突**只并列不择一**；引用 `[E1]` 编号由系统生成，模型不得编造。

## 目录

```
app/         api.py(组装) + routes_{search,proposal,status,open}.py(APIRouter)
             search.py(检索资格门槛) extract.py(确定性抽取) parser.py(解析)
             proposal.py(证据包+生成) intent.py(查询意图识别) classifier.py(角色判定)
static/      纯 HTML/CSS/JS 前端（无构建步骤）
scripts/     批量脚本：refresh_catalog(增量登记) / ocr_batch(OCR) / r5_*(解析索引) / backfill_missing_contracts …
tests/       267 passed
docs/        文档地图见 docs/index.md（含交接文档与需求记录）
data/        白名单与评测产物（*.bak-*.json 为脚本写前备份，不入库）
```

**新会话请先读 `AGENTS.md`（命令与红线）与 `docs/agent-handoff.md`（当前状态、下一步、不可重犯的坑）。**
