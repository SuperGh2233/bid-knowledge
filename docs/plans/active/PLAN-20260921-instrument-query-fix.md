# PLAN-20260921-instrument-query-fix — 仪器「型号碎片」检索修复（纯检索层）

> 类型：PLAN（`docs/plans/active/`）· 建立 2026-09-21 · 状态 **active** · 关联 `PLAN-20260915-demo-feedback-issues.md`（R1-3 通称映射）
> 来源：需求方 2026-09-21「仪器清单收纳不少，但检索返回很少」→ 数字诊断见 §1。
> 红线：只改 `app/api.py::resolve_instrument_query` + 词表 `instrument_aliases.json` + 测试；
> **不动库、不动数据、不重跑、不扩锚点**（Xenium 等数据面缺失属遗留项，§3 明示不在此范围）。

## 1. 诊断（2026-09-21 实测，reg 库 + 服务接口）

**数据面**：`instrument_name` 168 条 / 去重 **16 种**型号；`instrument` 211 条是**存在性空壳**
（无 `fact_value`）；发票/采购/照片另计。名义「645 条」，可支撑「按型号检索」的只有 168 条 / 16 种。

**检索面（断点实测）**：

| 用户输入 | 库里实有 | 现状返回 | 根因 |
|---|---|---|---|
| `HBH192` | `192通道HBH192`（19条） | **0** | 第三路方向反：`型号名 in 查询词`，用户输的是库内存的子串 |
| `Chromium` | `10X单细胞Genomics Chromium仪器`（14条） | **0** | 同上 |
| `QE质谱` | `QE质谱仪`（2条） | **0** | 同上 |
| `10X Genomics` | `10X单细胞Genomics Chromium仪器` + `10X CytAssist`（24条） | **0** | 同上 |
| `DNBSEQ` / `DNBSEQ-T7` | `DNBSEO-T7`（1条，**拼写是 SEO 非 SEQ**） | **0** | 词表 `instrument_aliases.json` 写 `DNBSEQ-T7` → LIKE 匹配不到 `DNBSEO-T7` |
| `质谱仪`/`测序仪`/`生物分析仪` 等通称 | — | 正常（34/1/66…） | ✅ 通称映射没错 |

**三处根因**：
1. **`resolve_instrument_query` 第三路（库内型号兜底）方向写反**（`app/api.py:613`）：
   `hit = [n for n in names if n in t or any(w in t ...)]` —— 判「库内完整型号名是否出现在查询里」，
   而业务是**用型号碎片查**（`HBH192` ⊆ `192通道HBH192`）。应反转为「查询的某碎片 ⊆ 库内型号名」。
2. **词表拼写错**：`instrument_aliases.json` 里 `DNBSEQ-T7`（SEQ），实体正文是 `DNBSEO-T7`（SEO）。
3. 代码用 `re.split(r"[\s\-_]+", n)` 拆型号名，`\s` 已含 `\xa0`（新发现：`Agilent\xa07890B` 含 NBSP），拆分路径可用，但反向匹配时若拿**查询词**做碎片也要先归一空白。

**非目标（明确不在此次）**：`Xenium` 等库内 16 种之外的型号查不到 —— 数据面抽取缺漏，既有遗留项
（交接 §9「Xenium 仪器查不到」，涉及重跑抽取 + 口径确认），单独提计划；本次检索层修复对它们无效、也不假装覆盖。

## 2. 修复（纯检索层，不碰库与数据）

**A. `resolve_instrument_query` 第三路方向反转**（`app/api.py`）
- 现：`names` 里选 「型号名 或 型号名碎片 ∈ 查询词」
- 改：`names` 里选「**查询词中的某个碎片 ⊆ 型号名**」（先对查询词 `t` 做与 `_norm` 等价的空白归一，
  再按 `[\s\-_ ]+` 拆出碎片 `tok`；凡 `len(tok)>=4` 且 `tok in 型号名_归一` → 收集该型号名）。
- ⚠️ 为防止误扩散（用户输 `质谱仪` 被拆成 `质谱仪`/`质 谱 仪`…），拆碎片的**最小长度**定为 4（`len>=4`，
  与现 `len(w)>=4` 一致）；且**通称命中（第一路）优先语义不变** —— 有通称时先返回通称、不进第三路。
- 返回值仍是「可作 LIKE 关键词的碎片/型号名元组」—— **接口契约不变**（`material-facts` 的 `LIKE %m%` 用法不动），
  路由与前端零改动。

**B. 词表拼写对齐**（`instrument_aliases.json`）
- `DNBSEQ-T7` → `DNBSEO-T7`（与库内/正文一致；保留 `Novaseq` 键不变）。
- 顺带核对 `_note` 的型号清单不需要改动（词不变），仅改映射值这一处。

**C. 新测试**（`tests/test_material_facts.py`，在既有 `test_resolve_instrument_query_prefers_category_then_model` 附近追加）
- 碎片命中：`HBH192` → 含 `192通道HBH192`（或等价）；`Chromium` → 含 `10X单细胞Genomics Chromium仪器`；
  `QE质谱` → 含 `QE质谱仪`；`10X Genomics` → 含 `10X … Chromium` 与 `10X CytAssist`（`CytAssist` 碎片 ≥4 命中）。
- 拼写：`DNBSEQ` / `DNBSEQ-T7` / `DNBSEO-T7` → 均返回含 `DNBSEO-T7` 的元组（对齐后 LIKE 能命中）。
- 护栏不放松：`找仪器` 仍返回 `()`（既有断言保留）；`完全不相干的东西` 仍 `()`。
- 现有 3 条断言全部需保持绿（通称命中、无关返回空、泛问不解析）。

## 3. 验证

| 项 | 门槛 | 实测（2026-09-21） |
|---|---|---|
| pytest | 全绿（323 + 新增 ≥4 → ≥327） | ✅ **329 passed**（新增 2 条：碎片命中 + 拼写别名） |
| 实测接口 | `HBH192` 19 条；`Chromium` 14 条；`QE质谱` 2 条；`DNBSEQ-T7` 1 条；`质谱仪` 34 条（不回归） | ✅ 服务重启（PID 39656）实测：`HBH192` **22**、`Chromium` **14**、`QE质谱` **2**、`10X Genomics` **14**、`DNBSEQ-T7` **1**、`测序仪` 1→**2**（补 DNBSEO）、`质谱仪` 32（不回归）；`Xenium`/`自动核酸提取` 仍 0（护栏） |
| 契约 | `resolve_instrument_query` 返回仍为 LIKE 可用的关键词元组；`material-facts` 行为仅新增可命中碎片 | ✅ 调用点（`routes_search.py` 的 `LIKE %m%`）与前端零改动 |
| 红线 | 零库写、零重跑、零扩锚点；正式库未触碰 | ✅ 全程只改 `api.py` + 词表 + 测试 |

## 4. 遗留（不入本次）

- `Xenium` 及 16 种之外型号：数据面抽取，见交接 §9（重跑抽取 + kind 门口径确认），单独计划。

## 修订记录

- 2026-09-21：建立（需求方「仪器检索返回很少」诊断 + 修复立项）。
- 2026-09-21（同日实施）：§2 三处修改完成 —— 第三路方向反转（先切分后归一，`\xa0` 已并入）、
  词表 `DNBSEQ-T7`→`DNBSEO-T7` + 补 `DNBSEQ`/`DNBSEQ-T7` 两个正确拼写别名；`pytest 329 passed`；
  服务已重启（PID 39656）、接口实测通过。**未打 tag**（随下次发布或用户指示）。