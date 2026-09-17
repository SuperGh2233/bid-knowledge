# AGENTS.md — bid-ai-clean

> 本仓库的**稳定 Agent 指令**：环境、命令、不变量、禁止事项、文档导航。
> **不放**：进度（见 `docs/agent-handoff.md`）、密钥、任何会变的东西。
> 工作区级拓扑（新旧两版本、归档位置）见仓库外的 `../CLAUDE.md`。

## 这个仓库是什么

**投标材料智能检索与方案生成系统**（最小重建版），只交付两个能力，**不得扩第三条主链路**：

1. **历史材料定位**：自然语言查询 → 满足条件的**我方响应文件** + 内部业务记录
   （合同产品金额 / 财务社保月份 / 仪器设备 / 方案章节），卡片可直接打开源文件。
2. **模块级方案生成**：按 8 个方案模块检索历史我方证据 → 受证据约束生成带出处的草稿。

数据源：`Z:\01 投标项目文件\{2025年,2026年}`（**只读** NAS 共享盘）。
架构取向：**确定性规则 + SQLite 台账为主**；ES 只用 **BM25**（不引入 embedding/kNN，避免正文外发）；
LLM 只用在两处已授权的地方（方案起草、意图识别兜底）。**不引入 LangChain/图数据库/新架构层。**

## 环境与命令

必须用 conda 环境 `langchain-dev` 的解释器（PATH 上的 python 是别的 venv，没有 pytest）：

```bash
CONDA="C:\Users\hao.guo\AppData\Local\miniconda3\envs\langchain-dev\python.exe"
export BID_AI_CLEAN_DB="$PWD/bid_ai_clean_reg.db"      # 演示/测试库；正式库禁写（见红线）

"$CONDA" -m app.api                                    # 启动服务 → http://127.0.0.1:8000
"$CONDA" -m pytest tests -q                            # 回归测试（mock 外部依赖，无需 ES/key）
"$CONDA" scripts/eval_gold_recall.py                   # 金标准召回评测
"$CONDA" scripts/eval_success_precision.py             # Success@5 / Precision@10
"$CONDA" scripts/refresh_catalog.py --dry-run          # 增量登记 Z 盘新文件（只读预览）
"$CONDA" scripts/refresh_ocr_whitelist.py --dry-run    # 白名单 OCR 预览（真跑会外发，需授权）
"$CONDA" scripts/refresh_index_whitelist.py --dry-run  # 白名单索引（零外发）
```

依赖：`pip install -r requirements.txt`（版本已钉）；Elasticsearch 8.x 在 `http://localhost:9200`
（索引 `bid_scheme_sections_v1`）；`.env` 从 `.env.example` 复制（密钥只从环境变量读）。

**三个必踩的坑**：
- 改完代码**必须重启服务**再验证；重启前 `netstat -ano | grep :8000` 核对 PID 与启动时间
  —— 旧进程残留时新进程 bind 失败但 curl 照样 200（**旧代码的 200**）。
- **前端改动后，浏览器可能仍在用缓存的旧 JS**（服务端按请求实时读取，但浏览器会启发式缓存）——
  实测用户看到"数据是新的、标签是旧的"的混合页面。服务端已加 `Cache-Control: no-cache`
  （`app/api.py::_NoCacheStatic`）；**首次仍需用户硬刷新一次**（Ctrl+F5）才会拿到新 JS。
  验证方法是直接读服务下发的文件：`GET /static/app.js` 里搜关键字，别假设用户看到的就是新的。
- Windows 上 curl 传中文参数会被 GBK 编码 → 验接口用 Python `urllib.parse.urlencode`。

## 不可破坏的红线

**数据安全**
- `Z:\` / NAS **只读**：禁止在共享盘创建、改名、移动、删除、解压、写 OCR 结果；派生物一律写本地。
- **正式库 `bid_ai_clean.db` 禁写**（脚本对 `--db bid_ai_clean.db` 一律拒绝，退出码 2）；
  测试/演示一律 `bid_ai_clean_reg.db`。
- `scripts/refresh_catalog.py` **只登记不删**（刻意无 `--apply-deletes`）；任一根目录不可访问 → 整轮中止。
- 文件身份唯一键 = `source_root_id + relative_path`（绝对路径只用于打开原文件）。

**数据合规（外发）**
- LLM/OCR 网关（`newapi.oebiotech.com`）是**外部网关，会外发正文**。每一份外发都有独立授权记录
  （`docs/authorizations/`，共 **6** 份），**范围不可自行扩大**；新数据源/新角色须先取得用户书面授权。
- 服务端硬开关默认全 `false`：`OCR_ENABLED`、`PROPOSAL_GEN_ENABLED`、`INTENT_LLM_ENABLED`、
  `OPEN_EXTERNAL_ENABLED`。未授权路径必须**明确报错（403），不静默降级、不外发**。
- 日志与错误信息**不得**打印 API key 与敏感正文。

**业务语义**
- 九类文档角色；**方案生成证据只允许 `our_response` / `final_signed`**（招标要求、竞品、空模板、
  未知文件一律不得作为我方承诺）。
- **合同金额口径**：产品金额 = 同产品明细行 `contract_items.line_amount` 之和；
  **`contracts.total_amount` 不得参与产品金额判断**。提不到就不参与金额过滤，宁缺毋滥。
- 数字/时限冲突**只并列不择一**；证据不足明确标记；`[E1]` 等引用编号由系统生成，模型不得编造。
- 新别名 / 新角色规则**必须人工确认**后落库，不自动写入。
- **`data/approved_documents.json`（已核合同白名单）在 import 期载入** → 改它必须重启服务。

**禁止提交**：`.env`（真实 key）、`*.db` 与 `bid_ai_clean_reg.bak-*.db`、`outputs/`（**真实投标正文**）、
`tmp/`、`tmp_probe/`、`data/*.bak-*.json`、`*.log`。**不得把项目目录打包外发**（`.git/config` 内含令牌）。

## 代码地图

```
app/
├── api.py             # 应用组装 + 共享辅助（唯一常量所在地：产品别名/事实类型/白名单）
├── routes_search.py   # 需求一：/api/{ask, material-search, material-facts, scheme-search, three-modules}
├── routes_proposal.py # 需求二：/api/{modules, module-kb, proposal-generate} + /api/tender-check（已暂停）
├── routes_status.py   # /api/status
├── routes_open.py     # /api/open（唯一会启动外部程序的端点，默认关闭）
├── search.py          # 资格门槛（顺序不得改）、排序、行级负向过滤
├── extract.py         # 确定性抽取（合同头/金额/材料期间/仪器）
├── parser.py / ocr.py # 原生解析 / 本地 RapidOCR
├── proposal.py        # 证据包 + 受约束生成（唯一外发正文处）
├── intent.py          # 查询意图识别（本地优先，判不出才问模型）
├── classifier.py      # 九类角色判定
├── index.py           # ES 章节索引（BM25-only）
└── config.py          # DB 路径与全部外发开关
static/                # 纯 HTML/CSS/JS（无构建步骤；禁止 Python 专有方法，有护栏测试）
scripts/               # refresh_*（增量/白名单）、eval_*（评测）、backfill_*、r4_*/r5_*（历史批处理）
tests/                 # pytest 回归（含前端渲染冒烟）
```

## 文档导航

入口是 **`docs/index.md`**（文档地图 + 状态登记）。常用：

| 要做什么 | 读什么 |
|---|---|
| 恢复上次会话 / 看当前进度与下一步 | `docs/agent-handoff.md`（可替换检查点，固定路径） |
| 看**已知问题与风险** | `docs/agent-handoff.md` 的「Problems and Risks」；各计划自己的 Risks/遗留段 |
| 看**持久决策**（为什么这么做） | `docs/plans/legacy/MINIMAL_REBUILD_PLAN-DECISIONS.md`（D1–D15）与各计划文档；本仓库暂无 ADR 目录，**新决策写进对应计划文档** |
| 查接口契约 | `docs/specs/api.md` |
| 看**已发布**了什么（版本变更） | `CHANGELOG.md`（仓库根；每条对应一个 tag） |
| 查需求与验收口径 | `docs/plans/active/`（活跃计划）；`docs/plans/legacy/`（旧系统路线，已冻结） |
| 确认某类外发是否已授权 | `docs/authorizations/`（**6 份**，范围不可扩大） |
| 查评测结论 | `docs/evals/`（现役）；`docs/evals/archive/`（R5/D1/D2a 历史证据，只查不改） |
| 给业务/需求方的材料 | `docs/business/` |

**文档纪律**（新增文档前必读）：类型决定目录（计划→`plans/active/`、规格→`specs/`、授权→`authorizations/`、
评测→`evals/`、业务→`business/`）；命名 `类型前缀-YYYYMMDD-主题`（阶段代号不进文件名）；
被修订的文档**在原文改 + 文末修订记录**，不留 `-corrected` 副本；阶段结束把评测移入 `evals/archive/`；
一次性探查笔记进 `tmp/`（不进 docs）；真实投标正文**永不进 docs**。
