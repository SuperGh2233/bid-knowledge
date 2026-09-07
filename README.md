# bid-ai-clean

投标材料智能检索与方案生成系统（最小重建版）。执行路线见 `../bid-ai/MINIMAL_REBUILD_PLAN.md`（R0 已完成冻结）；决策覆盖层见 `../bid-ai/MINIMAL_REBUILD_PLAN-DECISIONS.md`。

本项目只交付两个能力：
1. **历史材料定位**：自然语言查询 → 我方响应文件 + 内部业务记录（合同/财务社保/仪器/方案章节），可打开源文件。
2. **模块级方案生成**：九类方案章节检索历史我方证据 → 受证据约束生成新方案。

架构 = 三阶段解耦（L14）：`scan`(只登记) → `classify`(台账分类) → `ingest`(解析/提取/索引)；`rebuild --stage` 按版本重算；不引入 LangChain/图数据库/新架构层。

## 状态
- R1 骨架完成：`bid_ai_clean.db` 四张空表（documents/contracts/contract_items/material_facts）+ ES `bid_scheme_sections_v1` 空映射 + 连通自检。
- 未扫描 NAS，未解析文件，未 OCR，未迁移旧业务数据。

## 运行
```bash
# 环境变量（密钥不落库）
set LLM_API_KEY=… & set EMBEDDING_API_KEY=…
set SOURCE_ROOTS=Z:\01 投标项目文件\2025年;Z:\01 投标项目文件\2026年

python cli.py                       # health/自检（建空库+空索引）
```
测试：R1 阶段以 `tests/` 内的 smoke 自检代表（不依赖外网，仅验证建表/映射/连通）。