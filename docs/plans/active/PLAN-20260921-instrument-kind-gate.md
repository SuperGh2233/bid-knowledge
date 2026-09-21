# PLAN-20260921-instrument-kind-gate — 仪器名抽取与文档分类「解耦」

> 类型：PLAN（`docs/plans/active/`）· 建立 2026-09-21 · 状态 **active**
> 来源：需求方「Xenium 查不到」→ 全库摸底证实是**系统性缺陷**（80 种可抽、仅 16 种入库），
> 非个例。用户已拍板：**完全解耦，拿掉 kind 门**（2026-09-21 AskUserQuestion）。
> 红线：只改 `scripts/extract_three_modules.py` + 测试；重跑只写 reg 库（脚本断言被拒正式库）；
> 不动 `kind_of` 分类器、不动社保/财务期间抽取、不动 photo/purchase 门。

## 1. 诊断（2026-09-21 全库摸底）

**现状**：`instrument_name` 168 条 / 去重 **16 种**型号。

**摸底**（全库已解析 our_response/final_signed 只读扫描）：

| 指标 | 数值 |
|---|---|
| 正文能抽出 `N台<仪器名>` 的文档 | **199 份** |
| 去重可入库仪器名 | **80 种** |
| 其中被 kind 门挡掉的文档 | **182 / 199（92%）** |

挡掉 182 份的机制：
`extract_three_modules.py:232` 的
`if kind in ("instrument", "instrument_photo"):` —— 而 `kind_of` 规则表把 **social_security_month 排第 1 位**
且正文扫描是**全文**（`extract_three_modules.py:85`：`(("完税证明","社保",…), "social_security_month")`），
几乎每份完整响应文件都含「社保/完税/医疗」等词（附件含缴纳证明/完税证明）→ **整份文档被判成社保类** →
`instrument_names_in` 整体跳过。

**本质**：`instrument_names_in` 是独立纯函数，判别不依赖文档类别（自带 `NAME_BAD_WORDS`/前缀残渣/
去重护栏）；「抽不抽仪器」被错误的「文档属于哪类」门挡住 —— 设计缺陷，一次影响几十种仪器。

被漏掉的真实型号（非 Xenium 个例）：`AB SCIEX QTRAP`、`Waters SYNAPT XS 质谱成像仪`、
`Olink Signature Q100`、`Illumina NovaSeq X Plus`、`96通道自动化建库设备`、`华大T7`、`10x Genomics Xenium`…（约 64 种）。

## 2. 修改点（只动 `scripts/extract_three_modules.py`）

**把 `instrument_name` 抽取从 kind 门中解耦**：
```python
# 现状（232 行附近）：
if kind in ("instrument", "instrument_photo"):
    for iname, iev in instrument_names_in(text):
        con.execute(INSERT instrument_name ...)

# 改为：无条件执行（候选文档只要正文有 N台<名字> 且过护栏就抽）
for iname, iev in instrument_names_in(text):
    con.execute(INSERT instrument_name ...)
```

- **保留不动**：`kind_of` 分类器本身、社保/财务期间抽取（265–271 行，仍依赖 kind）、
  `instrument_photo` 与 `purchase_contracts_in` 门（241 行，**不在本次范围**，避免扩大风险）。
- 用户批注 scope：只解耦 `instrument_name`；photo/purchase 保持现状。
- 幂等语义不受影响：221–223 行 `with con:` 内先 DELETE 后 INSERT，重跑安全。

## 3. 护栏（复用既有 + 新增「残渣规则」—— 2026-09-21 dry-run 实测修订）

`instrument_names_in` 自带（保留）：`NUM_UNIT` 只认 `N台<名字>`；`NAME_STOP_PREFIX` 挡
「的/和/同/…」开头；`NAME_NOISE`；`NAME_BAD_WORDS`；`NAME_MANY_DIGITS`；`len` 2–30；前缀残渣去重。

**dry-run 实测新增**（对 87 种候选模拟，规则化后保留 60 种真信号、拒绝 27 种噪声、误伤 0）：
解耦后候选里出现 **9 类噪声**（招标评分条款 /「设备序列号为」粘连 / 括号未闭合残渣 / 短非设备词等），
**证明需要新增护栏而不只是拿掉 kind 门**。新增规则（`_REJECT_NAME` 集中判定，可测）：

| 规则 | 判据 | 实例（拒） |
|---|---|---|
| ① 比较符 | 名含 `≤ ＜ ≥ ＞ > <` | `≤设备数＜10台的` |
| ② 序列号粘连 | 名含「序列号」 | `Bruker timsTOF HT设备序列号为` |
| ③ 括号未闭合 | 名含 `（(` 但无 `）)` | `液质联用仪器（LC-MS/MS`（**截断残渣**；括号闭合的真型号如 `…（GC-MS/MS）` 保留） |
| ④ 评分条款 | 名含「得N分 / 套得 / 以下得 / 得1分」 | `流式细胞仪的`（拟投入…得3分）、`/套得1分` |
| ⑤ 圈序号 | 名含 `①②③…` | `③联系维修工程师4小时内到场④若故障时间>2小时` |
| ⑥ 非设备词 | `预备 / 备用 / 计160` 等 | `1台预备`、`1台备用` |
| ⑦ 号码粘连 | 名以 `1分析 / 1织 / 1 13%` 开头 | `1分析物转移系统` |
| ⑧ 长残渣 | 含「完成文库的测序/空载待命/UPS不间断/驻外办事处」 | `空载待命）④UPS不间断电源` |
| ⑨ 的尾 | 名以「的」结尾（大部分被 ①④ 覆盖，单独兜底） | `自动化组织解离仪器（包括相应仪器试剂）的` |

**本次不新增白名单/词表展开**（防漂移）；`_REJECT_NAME` 是**通用残渣规则**，
新增噪声形态走既有 `NAME_BAD_WORDS` 或补规则，不入白名单。dry-run 全程只读。

## 4. 执行步骤（每步一个提交）

1. 改 `extract_three_modules.py` 解耦 instrument_name 抽取；
2. 补测试（`tests/`）：候选文档 kind=social_security_month 但正文含 `N台Xenium` → 仍产出
   instrument_name；kind=instrument 原路径照常；护栏测试（噪声名仍被拒）不回归；
3. `pytest` 全绿；
4. **dry-run 预演**（只读探针）：对全库跑 `instrument_names_in` 统计理论新增 —— 确认 80 种里
   护栏收敛后净增量；提前发现 >5% 噪声档位；
5. **重跑** `"$CONDA" scripts/extract_three_modules.py`（写 reg 库，幂等）；
6. 抽验新增型号质量（采样 20 条人工核对）；
7. 更新计划验收、`agent-handoff.md`、`CHANGELOG.md`（不单独打 tag，随下次发布）。

## 5. 验收（2026-09-21 实跑）

| 项 | 门槛 | 实测 |
|---|---|---|
| instrument_name | 168/16 种 → 显著增加 | ✅ **1327 条 / 248 文档 / 68 去重变体**（重跑落库）|
| 被解耦前的 182 份 | 社保/财务/发票/采购类文档也产出仪器名 | ✅ `Xenium` 0 → **11 条**；`液质联用` 12 / `流式细胞仪` 14 / `Olink` 12 / `MobiNova` 10 / `华大C4` 10 / `Waters SYNAPT` 12 / `QTRAP` 12（接口实测）|
| 误抽 | 噪声 ≤5% | ✅ 评分条款/序列号/括号截断/预备备用全部 0 条；`instrument_name` 含「备用」= 0 |
| 其他类别 | 社保/发票/photo/purchase 不回归 | ✅ 990 社保持平、211 instrument 保持平、发票/采购/照片全平、qualification 87 未动 |
| 财表 | pytest 全绿；正式库/NAS 零触碰 | ✅ **336 passed**（+7 测试）；重跑只写 reg 库，脚本断言拒正式库 |

**实施中的两处偏差（如实记录）**：
1. **括号剥除 bug**：原 `strip(" ：:（(）)")` 把 `液质联用仪器（LC-MS/MS）` 的右括号剥掉 →
   被「未闭合」规则拒 → 该真型号 0 条。改为 `strip(" ：:\t\n")` 保留括号；同时补
   `_RE_WORDS_PAREN` 拒带收尾括号的短噪声词（`备用）`）。
2. **`色谱质谱联用` 词表缺短键**（R1-3 遗留）：词表只有 `色谱质谱联用仪`，用户输
   `色谱质谱联用` 不命中 → 加短键（人工确认后落库）。

## 修订记录

- 2026-09-21：建立（需求方 Xenium 提问 → 系统性摸底 → 用户拍板完全解耦）。
- 2026-09-21（同日实施）：解耦 + 残渣规则（§3）+ 括号剥除修复 + 词表短键；重跑落库
  `instrument_name 168→1327 条`；`pytest 336 passed`；服务已重启（PID 89924）、接口实测通过。
  **未打 tag**（随下次发布或用户指示）。