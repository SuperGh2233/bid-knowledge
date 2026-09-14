# bid-ai-clean

投标材料智能检索与方案生成系统（最小重建版）。执行路线见 `../bid-ai/MINIMAL_REBUILD_PLAN.md`（R0 已完成冻结）；决策覆盖层见 `../bid-ai/MINIMAL_REBUILD_PLAN-DECISIONS.md`。

本项目只交付两个能力：
1. **历史材料定位**：自然语言查询 → 我方响应文件 + 内部业务记录（合同/财务社保/仪器/方案章节），可打开源文件。
2. **模块级方案生成**：按方案模块检索历史我方证据 → 受证据约束生成新方案。

架构 = 三阶段解耦（L14）：`scan`(只登记) → `classify`(台账分类) → `ingest`(解析/提取/索引)；`rebuild --stage` 按版本重算；不引入 LangChain/图数据库/新架构层。

## 状态
- 精选真实样本只读预览：合同产品金额定位 + 单来源售后证据/示范稿。
- 正式库、全量解析、OCR、动态方案生成与 ES 方案索引仍未启用。

## 运行
```bash
# 环境变量（密钥不落库）
set LLM_API_KEY=… & set EMBEDDING_API_KEY=…
set SOURCE_ROOTS=Z:\01 投标项目文件\2025年;Z:\01 投标项目文件\2026年

python cli.py                       # health/自检（建空库+空索引）
python -m app.api                  # http://127.0.0.1:8000 只读预览
```

预览默认读取 `bid_ai_clean_reg.db`，可用 `BID_AI_DEMO_DB` 指向另一份只读测试库。当前合同定位只覆盖两份已核真实合同，方案为冻结的单来源示范稿，页面会明确展示这一边界。
