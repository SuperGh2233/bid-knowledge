const $ = (selector) => document.querySelector(selector);
const esc = (value = "") => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// 面向业务同事的说法：内部枚举值 / 技术原因一律不直接上屏
// ⚠️ 全库实测还有 native_pdf_text(216) 与 native_xlsx_text(6) 两个取值，
// 原表缺这两项 → 222 份文件在页面上被显示成「格式未登记」。已补。
const FORMAT_LABELS = {
  native_text: "文字版原文（可直接检索）",
  native_pdf_text: "PDF 自带文字层（可直接检索）",
  native_xlsx_text: "Excel 表格（可直接读取）",
  mixed: "文字+图片（图片已识别）",
  scanned_ocr: "扫描件（已识别文字）",
  unsupported: "暂无法读取",
  failed: "读取失败",
};
const REASON_RULES = [
  [/角色无效/, "这份文件在库里的用途还没定下来，本次不参与"],
  [/待复核|暂停/, "这份文件还在人工复核中，暂不参与"],
  [/框架协议|样稿子类/, "这是框架协议/供应商库样稿，不是具体成交合同"],
  [/artifact\.sha256|内容版本失配/, "文件内容更新过，产品明细还没跟着更新，本次不采用"],
  [/明细来源指纹缺失/, "这份合同的产品明细还没提取成功，本次不采用"],
  [/明细来源SHA|可能过时/, "产品明细可能已过时（文件更新过），本次不采用"],
  [/缺少canonical|产物/, "缺少可核对的解析结果，本次不采用"],
  [/无服务明细/, "这份合同里没有可识别的产品明细"],
];
const formatLabel = (value) => FORMAT_LABELS[value] || "格式未登记";
/** 文案限长：卡片上只给**够定位的片段**（完整原文在 CSV 导出与接口响应里）。
 *  2026-09-16 用户反馈「前端显示的字段太多了」—— 那条原文曾把整张后续表格吞进来（抽取 bug，
 *  已另行修复），但**展示层必须自带限长**：不能指望上游永远不长。 */
const clip = (text, n = 160) => {
  const s = String(text ?? "");
  return s.length > n ? s.slice(0, n) + "…" : s;
};
/** 取路径末段（文件名）。**必须用 JS 写法** —— 我在 2026-09-14 这里写成过 Python 的
 *  `str.rsplit`，JS 的 String 没有这个方法 → `TypeError` 抛在 map 里 →
 *  「项目业绩」整块（98 条合同）渲染不出来，屏上只有一句英文异常（B2B 评审 P0）。 */
const baseName = (p) => (p ? String(p).split("/").pop() : "");
const plainReason = (reason) => {
  const hit = REASON_RULES.find(([pattern]) => pattern.test(reason || ""));
  return hit ? hit[1] : "不满足本次查询的核对条件";
};

document.querySelectorAll(".tab").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".tab,.panel").forEach(node => node.classList.remove("active"));
  button.classList.add("active");
  $("#" + button.dataset.tab).classList.add("active");
}));

// —— 批量取用（需求：一次查几十份合同，不能逐条点复制）——
// 路径可能因同一合同多产品而重复 → 按出现顺序去重，保留首次出现的位置。
const uniquePaths = (rows) => [...new Set(rows.map(r => r.source_path).filter(Boolean))];

const csvCell = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;

/** 通用 CSV 导出：headers 定列，rowOf 把一条结果映射成同长度的数组。 */
function downloadCsv(filename, rows, headers, rowOf) {
  const lines = [headers.map(csvCell).join(",")];
  for (const r of rows) lines.push(rowOf(r).map(csvCell).join(","));
  // 加 BOM：否则 Excel 打开中文会乱码
  const blob = new Blob(["﻿" + lines.join("\r\n")], {type: "text/csv;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  URL.revokeObjectURL(url);
}

// 两类结果的列不同：合同定位带编号/金额/甲乙方；三类材料定位另有一套（见 MODULE_CSV）
// 列序 = 卡片序 = **文件优先**（2026-09-14 业务评审：用户先要知道"哪个项目、哪份文件"）
const CONTRACT_CSV = {
  headers: ["项目", "文件", "合同编号", "产品", "金额", "金额来源", "合同日期", "甲乙方",
            "命中", "文件位置"],
  rowOf: (r) => [r.project_folder, r.file_name, r.contract_number, r.product, r.amount,
                 r.amount_source === "derived_qty_x_price" ? "由数量×单价推算" : "合同明写",
                 r.contract_date, [r.party_a, r.party_b].filter(Boolean).join(" / "),
                 r.hit ? "符合条件" : "未采用", r.source_path],
};
// 需求一另两类问法的导出列（与卡片上显示的一致）
const FACT_CSV = {
  headers: ["材料类别", "期间", "项目", "文件", "文件内文字", "文件位置"],
  // 期间用 `factValuesText`（折叠后多值一并导出）—— 与卡片上显示的**同一口径**
  rowOf: (r) => [FACT_LABEL[r.fact_type] || r.fact_type, factValuesText(r),
                 r.project_folder, r.file_name, r.evidence_text, r.source_path],
};
const SCHEME_CSV = {
  headers: ["章节标题", "相关度", "格式", "项目", "文件", "原文摘录", "文件位置"],
  rowOf: (r) => [r.heading || "（无标题章节）", r.score,
                 formatLabel(r.content_format), r.project_folder, r.file_name,
                 (r.text_excerpt || "").slice(0, 200), r.source_path],
};

/** 结果区顶部的批量操作条：条数 + 复制全部路径 + 导出 CSV。 */
function batchBar(rows, spec, label = "条结果") {
  const paths = uniquePaths(rows);
  if (!paths.length) return "";
  return `<div class="batch-bar">
    <span>共 ${rows.length} ${label}，来自 ${paths.length} 个不重复文件</span>
    <div>
      <button class="copy" data-copy-all="${esc(paths.join("\n"))}">复制全部文件位置（${paths.length} 个）</button>
      <button class="copy" data-csv>导出 CSV（可交 IT 或存档）</button>
    </div>
  </div>`;
}

/** 复制到剪贴板，**如实反馈成败**。
 *  评审 P1：原先是 `writeText(...)` 后**同步**改文案，Promise 既不 await 也不 catch ——
 *  剪贴板被拒（权限/非安全上下文）时按钮照样显示「已复制」，用户粘出来是空的、反复点也没提示。 */
async function copyText(button, text, okLabel) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = okLabel;
  } catch (e) {
    button.textContent = "复制失败 —— 请手动选中下方路径复制";
    console.warn("clipboard write failed:", e);
  }
  setTimeout(() => { button.textContent = original; }, 2600);
}

/** 绑定批量操作条上的按钮（每次重渲染结果后都要调）。 */
function bindBatchBar(target, rows, filename, spec) {
  const copyAll = target.querySelector("[data-copy-all]");
  if (copyAll) copyAll.addEventListener("click", () => {
    copyText(copyAll, copyAll.dataset.copyAll, "已复制，可直接粘进资源管理器地址栏");
  });
  const csv = target.querySelector("[data-csv]");
  if (csv) csv.addEventListener("click", () => downloadCsv(filename, rows, spec.headers, spec.rowOf));
}

/** 结果卡片上的文件操作三件套：打开所在文件夹 / 打开文件 / 复制路径。
 *  ⚠️ **只带 `document_id`**（服务端按它反查已登记路径）—— 前端永不提交路径，
 *     这是"不能让前端提交任意路径"的落地形式。
 *  ⚠️ 缺 `document_id` 时**不渲染打开按钮**（否则点了必然失败），只留复制路径。 */
function fileActions(row) {
  const path = row.source_path || "";
  const copy = `<button class="copy" data-copy="${esc(path)}">复制路径</button>`;
  if (!row.document_id) return copy;
  return `<span class="open-actions">
      <button class="copy" data-open="folder" data-doc="${esc(row.document_id)}"
              title="在资源管理器里定位这份原始文件（只读预览，不会改动它）">打开所在文件夹</button>
      <button class="copy" data-open="file" data-doc="${esc(row.document_id)}"
              title="用系统默认程序打开这份原始文件（只读预览，不会改动它）">打开文件</button>
      ${copy}<span class="open-hint"></span>
    </span>`;
}

/** 绑定文件操作按钮。失败时**如实反馈**，且任何失败路径都保留「复制路径」这条出路。 */
function bindOpenButtons(target) {
  target.querySelectorAll("[data-open]").forEach(button => {
    button.addEventListener("click", async () => {
      const original = button.textContent;
      const hint = button.parentElement ? button.parentElement.querySelector(".open-hint") : null;
      button.disabled = true;
      button.textContent = "正在打开…";
      if (hint) hint.textContent = "";
      try {
        const data = await request("/api/open", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({document_id: button.dataset.doc, target: button.dataset.open}),
        });
        if (hint) hint.textContent = data.message || "已打开";
      } catch (error) {
        // 服务端的理由已是中文并带降级指引（如"共享盘不可达…可用复制路径"），原样上屏
        if (hint) hint.textContent = error.message;
      }
      button.disabled = false;
      button.textContent = original;
    });
  });
}

/** 把服务端解析出的检索条件转成**可点标签**：点一下把该条件填回搜索框。
 *
 *  ⚠️ `fill` 必须是**后端能解析回去**的写法 —— 否则用户"点了标签再点查询"必然 400。
 *     这条有**跨语言一致性测试**钉住：本函数产出的 fill 会被逐个喂给
 *     `app.api.parse_demo_query()`（tests/test_frontend_syntax.py）。
 *  ⚠️ 点标签是**整体替换**搜索框内容 → 其余条件会丢。界面上必须明说（不静默丢条件）。
 */
function conditionTags(parsed) {
  const p = parsed || {};
  const tags = [];
  if (p.product) tags.push({label: `产品：${p.product}`, fill: p.product});
  if (p.minimum_amount > 0) {
    const v = Number(p.minimum_amount);
    tags.push({label: `金额：≥ ${v.toLocaleString("zh-CN")}`,
               // 万位整倍写成「2万元」（更贴近业务说法）；否则写「26400元」。两种都能被解析。
               fill: v % 10000 === 0 ? `${v / 10000}万元` : `${v}元`});
  } else {
    tags.push({label: "金额：不限", fill: null});
  }
  const ym = (s) => `${String(s).slice(0, 7).replace("-", "年")}月`;
  if (p.date_from) tags.push({label: `签订：${p.date_from} 之后`, fill: `${ym(p.date_from)}以后`});
  if (p.date_to) tags.push({label: `签订：${p.date_to} 之前`, fill: `${ym(p.date_to)}以前`});
  (p.party_include || []).forEach(o => tags.push({label: `乙方含：${o}`, fill: `乙方是${o}`}));
  (p.party_exclude || []).forEach(o => tags.push({label: `排除乙方：${o}`, fill: `不要${o}`}));
  (p.product_exclude || []).forEach(o => tags.push({label: `排除产品：${o}`, fill: `不要${o}`}));
  return tags;
}

/** 把一组条件拼成**一句可以直接查询**的文本。
 *  顺序有讲究：**排除类放最后** —— 后端的排除捕获窗口会一直吃到句末标点，
 *  排除项夹在中间会把后面的条件一起吞掉。这条由跨语言一致性测试钉住。 */
function conditionQuery(parsed) {
  return conditionTags(parsed).map(t => t.fill).filter(Boolean).join("，");
}

/** 条件标签：可点的（带 fill）可回填；不可点的（如"金额：不限"）只展示。 */
function conditionTagBar(parsed) {
  const tags = conditionTags(parsed);
  if (!tags.length) return "";
  const items = tags.map(t => t.fill
    ? `<button type="button" class="tag cond-tag" data-cond-fill="${esc(t.fill)}"
         title="点一下：把全部条件填回搜索框，并**选中**这一条，直接改写即可">${esc(t.label)}</button>`
    : `<span class="tag cond-tag-static">${esc(t.label)}</span>`).join("");
  return `<div class="cond-tags">
    <span class="cond-hint">系统识别到的条件（点标签 = 把全部条件填回搜索框并选中该条，<strong>直接改写</strong>即可，不会丢其它条件）：</span>
    ${items}</div>`;
}

/** 点标签 → 回填**全部条件**（不丢条件）+ 选中被点的那一段（可直接覆盖改写）。
 *  ⚠️ 只回填单个条件会让用户"点完就查"时因缺产品/金额而 400 —— 那是给用户挖坑。 */
function bindConditionTags(target, parsed) {
  const full = conditionQuery(parsed);
  target.querySelectorAll("[data-cond-fill]").forEach(button => {
    button.addEventListener("click", () => {
      const input = $("#query");
      input.value = full;
      input.focus();
      const at = full.indexOf(button.dataset.condFill);
      if (at >= 0 && input.setSelectionRange) {
        input.setSelectionRange(at, at + button.dataset.condFill.length);
      }
    });
  });
}

document.querySelectorAll("[data-query]").forEach(button => button.addEventListener("click", () => {
  $("#query").value = button.dataset.query;
  $("#search-form").requestSubmit();
}));

async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "请求失败");
  return data;
}

request("/api/status").then(data => {
  // 数据规模移到**页脚一行**（2026-09-14 业务评审：左侧栏占五分之一屏，却不帮用户完成任务）。
  // 保留防误读的那句：数字小是"只加载了精选样本"，不代表全部家底。
  const scope = data.scope || {};
  const c = data.counts || {};
  const q = scope.queryable_contracts ?? c.contracts ?? 0;
  const lg = Number(scope.ledger_records ?? 0);
  $("#footer-stats").textContent =
    `${Number(q).toLocaleString()} 份合同已完成核对、可以查询 · `
    + (lg ? `${lg.toLocaleString()} 条业绩清单声明（来自我方响应文件）· ` : "")
    + `${Number(c.parse_artifacts ?? 0).toLocaleString()} 份文件已读出正文 · `
    + `${Number(c.documents ?? 0).toLocaleString()} 份文件已登记`;
  $("#footer-note").textContent =
    `本页只加载精选样本（${Number(scope.total_contracts ?? 0).toLocaleString()} 份合同里 `
    + `${Number(q).toLocaleString()} 份已完成核对）；数字小是正常的，不代表全部家底。`
    + "本页不会写入数据库，也不会改动任何原始文件。";
}).catch(error => $("#footer-stats").textContent = `数据总览读取失败：${error.message}`);

$("#search-form").addEventListener("submit", async event => {
  event.preventDefault();
  const target = $("#search-result");
  target.className = "result-area";
  target.innerHTML = "<div class='result-card'>正在核对产品明细与金额条件…</div>";
  try {
    // 走**唯一入口** `/api/ask`：服务端按自然语言判断该查四类中的哪一类，
    // 返回体带 `kind` 字段，前端据此渲染。产品/金额/日期的规则判断全在服务端一处。
    const data = await request("/api/ask?q=" + encodeURIComponent($("#query").value));
    if (data.kind === "fact") return renderFactAnswer(data, target);
    if (data.kind === "scheme") return renderSchemeAnswer(data, target);
    return renderContractAnswer(data, target);
  } catch (error) { target.innerHTML = `<div class="error">${esc(error.message)}</div>`; }
});

/** R6-06：折叠后，把这份文件内部命中的**每条业务记录**列出来（默认收起，一条时不显示）。 */
function renderInnerRecords(row) {
  const recs = row.records || [];
  if (recs.length <= 1) return "";
  const items = recs.map(r => `<li><b>${esc(r.product)}</b> · ${
    r.amount == null ? "金额未确认" : `¥ ${Number(r.amount).toLocaleString("zh-CN")}`}${
    r.amount_source === "derived_qty_x_price" ? "<small class='derived'>（由数量×单价推算）</small>" : ""}</li>`).join("");
  return `<details class="inner-records"><summary>这份文件里命中的 ${recs.length} 条业务记录，点击展开</summary>
    <ul class="fact-list">${items}</ul></details>`;
}

/** ① 合同：**文件优先** —— 先答「哪个项目、哪份文件」，再答「里面是哪份合同、命中什么、为什么」。
 *
 *  2026-09-14 业务评审：原先把**产品名**当主标题、金额当大数字，看起来像"产品价格库"，
 *  而用户真正要的是「这份材料在哪个我方响应文件里」。字段顺序据此重排（**后端响应不变**）：
 *  CTL 合同与 document 实测严格 1:1，所以这只是渲染层的事，不必改折叠分组。
 */
/**
 * 业绩清单行 → **与合同原件同一张卡片、同一个列表**（2026-09-16 用户口径：
 * 「把这些文档和 pdf 走一样的召回路径，不用这样区分」）。
 *
 * 保留的只有**每张卡片上的一个小标记**：`业绩声明` + （有金额条件时）`金额未参与核验`。
 * 为什么标记不能省：实测「单细胞」相关业绩行 61 条里 **45 条没有任何金额** ——
 * 若与 PDF 合同完全同形，用户会以为它们也达到了金额门槛（那正是需求方第一条反馈的形态）。
 */
function ledgerCardOf(r, amountCondition) {
  // 无金额时**说清为什么**：这张表压根没设金额列 vs 有金额列但本行没取到
  const amt = r.amount == null
    ? `<small class='derived'>${r.amount_absent ? "金额未记载（" + esc(r.amount_absent) + "）" : "金额未记载"}</small>`
    : `¥ ${Number(r.amount).toLocaleString("zh-CN")} <small class='derived'>（业绩清单所列合同总额）</small>`;
  return `<article class="result-card ledger-card">
    <div class="card-top">
      <div class="file-head">
        <p class="file-project">${esc(r.project_folder || "（项目未登记）")}</p>
        <h3>${esc((r.relative_path || "").split("/").pop() || "（文件名缺失）")}</h3>
      </div>
      <span class="amount">${amt}</span>
    </div>
    <span class="tag tag-ledger">业绩声明</span>
    <span class="tag">采购人：${esc(r.party_a || "未识别")}</span>
    <span class="tag">${esc(formatLabel(r.content_format))}</span>
    ${amountCondition ? "<span class='tag'>金额为合同总额</span>" : ""}
    ${r.also_in && r.also_in.length ? `<div class="boundary-note">同一份业绩声明另存于 ${r.also_in.length} 处`
      + `（共 ${r.copy_count} 份副本，多为同一标的不同批次/项目文件夹各存一份）—— `
      + `**只展示一次**，不重复计入结果。</div>` : ""}
    <dl class="metadata">
      <dt>项目</dt><dd>${esc(r.project || "（项目名未识别）")}</dd>
      <dt>来源</dt><dd>我方响应文件里的业绩清单 —— <b>不是合同原件</b>；金额是<b>业绩表所列合同总额</b>
        （非产品明细金额）${amountCondition ? "，已按你给的金额门槛筛选" : ""}</dd>
      <dt>原文</dt><dd class="ledger-evidence">${esc(clip(r.evidence_text, 160))}</dd>
      <dt>打开文件</dt><dd>${fileActions(r)}</dd>
    </dl>
  </article>`;
}

/** 业绩行卡片组（同一列表用；不再单独成段） */
function ledgerCards(data) {
  const l = data && (data.ledger || (data.ledger_records
    ? { records: data.ledger_records, count: data.ledger_count, amount_condition: false }
    : null));
  if (!l || !l.records || !l.records.length) return "";
  const CAP = 20;
  const cards = l.records.slice(0, CAP).map(r => ledgerCardOf(r, !!l.amount_condition)).join("");
  // **未入选的两个去向都要说出来**（否则用户以为"就这么多"）：未达门槛的只报数，
  // 金额未记载的给出可打开的文件（业务能自己核对）—— 与全站"不静默丢结果"一致。
  const below = Number(l.excluded_below_amount || 0);
  const naCount = Number(l.excluded_no_amount_count || 0);
  const notes = [];
  if (below) notes.push(`另有 ${below} 条业绩声明的合同总额**未达你给的门槛**，已排除`);
  if (naCount) notes.push(`另有 ${naCount} 条**金额未记载**（业绩表未列金额），无法参与金额筛选 —— 见下方折叠，可打开文件自行核对`);
  const noteHtml = notes.length ? `<div class="boundary-note">${notes.join("；")}。</div>` : "";
  const naList = (l.excluded_no_amount || []).slice(0, 10).map(r =>
    `<li>${esc(r.party_a || "（采购人未识别）")} · ${esc((r.relative_path || "").split("/").pop())}
       ${fileActions(r)}</li>`).join("");
  const naBlock = naList
    ? `<details class="excluded-block"><summary>金额未记载的 ${naCount} 条，查看文件</summary><ul class="ledger-list">${naList}</ul></details>`
    : "";
  return cards + noteHtml + naBlock;
}

function renderContractAnswer(data, target) {
  const p = data.parsed || {};
  const threshold = p.minimum_amount > 0 ? p.minimum_amount : 0;
  const yuan = (v) => `¥ ${Number(v).toLocaleString("zh-CN")}`;

  const cardOf = (row) => {
    const status = row.hit ? "符合条件的文件"
      : row.amount_status === "conflict" ? "金额前后对不上，本次不列为符合项"
      : row.amount_status === "ok" ? "金额未达到你给的门槛"
      : "这份文件本次不参与筛选";
    // 推算出来的金额**必须标注**，不能与合同上明写的金额看起来一样
    const amount = row.amount == null ? "金额待核对"
      : yuan(row.amount) + (row.amount_source === "derived_qty_x_price"
          ? "<small class='derived'>（由数量×单价推算）</small>" : "");
    // 「为什么符合条件」：让结果自证，用户不必自己去比金额
    let why;
    if (row.hit && threshold > 0 && row.amount != null) {
      why = `命中「${row.product}」，该产品金额 ${yuan(row.amount)} ≥ 门槛 ${yuan(threshold)}`;
    } else if (row.hit) {
      why = `命中「${row.product}」`;
    } else if (row.skipped_reason) {
      why = plainReason(row.skipped_reason);
    } else {
      why = status;
    }
    // 文件优先的隐含前提是"一张卡 = 一份文件"。今天 CTL 合同与文件严格 1:1，但不把这条钉死：
    // 真出现"一文件多合同"时，让页面**自己说出来**，而不是静默给一个错标题。
    const docIds = new Set((row.records || []).map(r => r.document_id).filter(Boolean));
    const multiDoc = docIds.size > 1
      ? `<div class="boundary-note">⚠️ 这条结果合并了 ${docIds.size} 份文件，标题只代表其中第一份 —— 请用「打开文件」逐份确认。</div>` : "";
    return `<article class="result-card ${row.hit ? "hit" : "warning"}">
      <div class="card-top">
        <div class="file-head">
          <p class="file-project">${esc(row.project_folder || "（项目未登记）")}</p>
          <h3>${esc(row.file_name)}</h3>
        </div>
        <span class="amount">${amount}</span>
      </div>
      <span class="tag">${status}</span><span class="tag">命中：${esc(row.product || "—")}</span>
      <span class="tag">${esc(formatLabel(row.content_format))}</span>
      ${multiDoc}
      <dl class="metadata">
        <dt>合同编号</dt><dd>${esc(row.contract_number || "（未登记）")}</dd>
        <dt>签订日期</dt><dd>${esc(row.contract_date || "（未登记）")}</dd>
        <dt>甲乙方</dt><dd>${esc([row.party_a, row.party_b].filter(Boolean).join(" / ") || "（未登记）")}</dd>
        <dt>合同总额</dt><dd>${row.total_amount == null ? "（未登记）" : yuan(row.total_amount)}</dd>
        <dt>为什么</dt><dd>${esc(why)}</dd>
        <dt>打开文件</dt><dd>${fileActions(row)}</dd>
      </dl>
      ${row.detail_evidence ? `<div class="evidence">合同里对应产品的明细：${esc(row.detail_evidence)}</div>` : ""}
      ${renderInnerRecords(row)}
    </article>`;
  };

  // **命中与未命中分开**（2026-09-14 业务评审：查"2万以上"却看到 1.25 万的在列表里，用户会问"为什么它在这"）。
  // 未命中不是没有价值（它解释了"为什么不采用"），但**默认不该占主位** → 收进折叠。
  const hitCards = data.hits.map(cardOf).join("");
  const excludedCards = data.excluded.map(cardOf).join("");
  const excludedBlock = excludedCards
    ? `<details class="excluded-block"><summary>另有 ${data.excluded.length} 条被排除，查看原因</summary>
         ${excludedCards}</details>`
    : "";
  const filterNote = data.filter_note ? `<div class="boundary-note">${esc(data.filter_note)}</div>` : "";
  const ledgerN = (data.ledger && data.ledger.records ? data.ledger.records.length : 0);
  const hitLine = data.hits.length
    ? `找到 ${data.hits.length} 份符合条件的文件`
      + (data.hit_records > data.hits.length ? `（共 ${data.hit_records} 条业务记录）` : "")
      + (ledgerN ? ` ＋ ${ledgerN} 份含此类合同（业绩）的响应文件（见带「业绩声明」标记的卡片）` : "")
    : (ledgerN ? `没有符合金额条件的合同原件 ＋ ${ledgerN} 份含此类合同（业绩）的响应文件`
               : "没有同时满足「产品对得上」和「金额达标」的文件");
  // 「系统理解成了什么」用**可点标签**呈现（替换原先那行纯文本）：
  // 业务评审要的是"查看系统理解了哪些条件"，且每个条件允许用户改了重查。
  target.innerHTML = `<div class="summary"><h3>${hitLine}</h3></div>
    ${conditionTagBar(p)}
    ${filterNote}
    <div class="boundary-note">${esc(data.scope_note)}</div>
    ${batchBar(data.hits, CONTRACT_CSV)}
    ${hitCards || "<div class='result-card'>当前已准备的样本里没有这个产品的记录。</div>"}
    ${ledgerCards(data)}
    ${excludedBlock}`;
  bindCopyButtons(target);
  bindOpenButtons(target);
  bindConditionTags(target, p);
  // ⚠️ 导出**只含命中**：让"条上的数字 = 复制全部的个数 = CSV 行数"三者同源。
  // 原先导出含未采用项、而数字与复制不含 —— 代码与自己的注释相反，也违反 docs/specs/api.md
  // 「hits 与 excluded 要分开看，做导出/统计时不要混用」。要看未采用的原因，展开上面那个折叠即可。
  bindBatchBar(target, data.hits, `材料定位-${data.parsed.product || "查询"}.csv`, CONTRACT_CSV);
}

/** ②③ 材料存在性（财务社保 / 仪器设备 / 发票 / 照片 / 资质）→ 哪些文件里有这类材料 */
function renderFactAnswer(data, target) {
  // ⚠️ 与「按类浏览」同一口径：优先用**按文件折叠后**的 `files`
  // （2026-09-16 需求方反馈「同一份文件按每月一条出现多次」）—— 这条路径原先只用未折叠的 `facts`，
  // 于是同一份社保缴费记录表会重复出现好几次。缺 `files` 时回退到 `facts`。
  const facts = (data.files && data.files.length) ? data.files : (data.facts || []);
  const related = data.related || [];
  const cards = facts.map(f => `<article class="result-card ${f.role_scope === "tender" ? "warning" : ""}">
    <div class="card-top"><h3>${esc(factLabel(f.fact_type))}</h3>
      <span class="amount">${esc(factValuesText(f))}</span></div>
    <span class="tag">${esc(f.role_label || ROLE_LABEL[f.role_scope] || "")}</span>
    ${(f.record_count || 1) > 1 ? `<span class="tag">本文件 ${f.record_count} 条</span>` : ""}
    ${metaBlock(f)}
    ${f.evidence_text ? `<div class="evidence">文件中对应文字：${esc(f.evidence_text)}</div>`
      : `<div class="evidence">这份文件本身只登记了文件名，正文里没有可引用的原句 —— 请打开文件确认。</div>`}
  </article>`).join("");
  // `related` 是**期间语义为「起始」**的条目（如「自2021年起」），不能混进主列表 —— 它只能说明
  // 「至该时点可能仍有效」，不能断言某月一定有材料。分开列，如实标注。
  const rel = related.length ? `<div class="boundary-note">另有 ${related.length} 条只记了**起始**期间（如「自2021年起缴纳」），
    只能说明至该时点可能仍有效，**不能断言某月一定有材料**，故单列于下：</div>`
    + related.slice(0, 20).map(f => `<article class="result-card warning">
        <div class="card-top"><h3>${esc(FACT_LABEL[f.fact_type] || f.fact_type)}</h3>
          <span class="amount">${esc(f.fact_value || "起始期间")}</span></div>
        ${metaBlock(f)}
      </article>`).join("") : "";
  const byRole = {our: 0, tender: 0, other: 0};
  facts.forEach(f => { byRole[f.role_scope] = (byRole[f.role_scope] || 0) + 1; });
  const roleNote = facts.length ? `<div class="boundary-note">本次 ${facts.length} 条中，
    <strong>${byRole.our} 条来自我方响应/最终版文件</strong>（这才是需求一问的「响应文件里有没有」）；
    ${byRole.tender} 条来自<strong>招标要求</strong>（采购人要求提交的材料，
    <strong>不等于我方已附</strong>）；其余 ${byRole.other} 条来自资质附件 / 过程材料 / 待复核。</div>` : "";
  target.innerHTML = `<div class="summary"><h3>${facts.length ? `找到 ${facts.length} 条这类材料` : "没有找到这类材料"}</h3>
    <p>查询：${esc(data.query)}</p></div>
    ${roleNote}
    ${data.truncated ? `<div class="boundary-note">⚠️ 库内共 <strong>${data.total_available}</strong> 条，
      本次显示前 ${facts.length} 条（已截断）—— <strong>没显示出来的不代表没有</strong>。</div>` : ""}
    <div class="boundary-note">${esc(data.scope_note || "")}</div>
    ${batchBar(facts, FACT_CSV, "条材料")}
    ${cards || "<div class='result-card'>已入库的文件里没有这一类。</div>"}
    ${rel}`;
  bindCopyButtons(target);
  bindOpenButtons(target);
  bindBatchBar(target, facts, "材料存在性.csv", FACT_CSV);
}

/** ④ 方案章节：以前哪个响应文件写过某方案 */
function renderSchemeAnswer(data, target) {
  const sections = data.sections || [];
  const cards = sections.map(s => `<article class="result-card">
    <div class="card-top"><h3>${esc(s.heading || "（无标题章节）")}</h3>
      <span class="amount">相关度 ${Number(s.score || 0).toFixed(1)}</span></div>
    <span class="tag">${esc(formatLabel(s.content_format))}</span>
    ${metaBlock(s)}
    <div class="evidence">${esc((s.text_excerpt || "").slice(0, 300))}${(s.text_excerpt || "").length > 300 ? "…" : ""}</div>
  </article>`).join("");
  target.innerHTML = `<div class="summary"><h3>${sections.length ? `找到 ${sections.length} 个写过「${esc(data.query)}」的章节` : `没有找到写过「${esc(data.query)}」的章节`}</h3>
    <p>这是**需求一**的用法：找出历史上写过该小节的文件，供你取原文。</p></div>
    <div class="boundary-note">${esc(data.scope_note || "")}</div>
    ${batchBar(sections, SCHEME_CSV, "个章节")}
    ${cards || "<div class='result-card'>已入库的文件里没有写过这个小节。</div>"}`;
  bindCopyButtons(target);
  bindOpenButtons(target);
  bindBatchBar(target, sections, `方案章节-${data.query}.csv`, SCHEME_CSV);
}

function inline(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>");
}

/**
 * 「复制草稿全文」用的**净正文**：剥掉证据痕迹，只留可编辑的内容。
 *
 * 起因（2026-09-14 用户反馈）：原来复制的是 `data.markdown` 原始串 —— 每个 [E4] 引用编号、
 * 每段的「出处：」行、引用清单汇总全被一起复制走。用户要的是「方案正文」，不要「证据」。
 *
 * 处理规则（与渲染同一份源，勿另起一套）：
 *   - 删 `[En]` 引用编号、`  - 出处：` 行、`## 引用来源` 起的清单（出处靠页面引用清单与
 *     「复制全部来源文件位置」按钮单独承担）；
 *   - 标题 `#→` 去井号、列表 `*  **x**` → `- x`、粗体/行内码去符号、分隔线删。
 */
function markdownToPlainText(markdown) {
  const out = [];
  for (const line of (markdown || "").split(/\r?\n/)) {
    const raw = line.replace(/\[E\d+\]/g, "");
    if (/^ {2,}- 出处：/.test(raw)) continue;   // 出处行不进复制正文
    if (/^## 引用来源/.test(raw)) break;        // 引用清单汇总不复制（页面已单独提供复制来源）
    if (/^- `[E\d]+`/.test(raw)) continue;      // 引用清单行
    // 空行 → 段落分隔（复制到 Word 时保留分段，不要全挤成一行）
    if (!raw.trim()) {
      if (out.length && out[out.length - 1] !== "") out.push("");
      continue;
    }
    let t = raw.trim();
    if (/^#{1,4}\s/.test(t)) t = t.replace(/^#{1,4}\s*/, "");
    else if (/^>\s?/.test(t)) t = t.replace(/^>\s?/, "");
    t = t.replace(/^\*\*\s+/, "- ").replace(/^\*+\s*/, "- ").replace(/^-\s+/, "- ");
    t = t.replace(/^\s*[-*]+\s*$/, "");           // 纯分隔线（--- 或 ***）
    t = t.replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");
    // 编号被剥掉后常剩 `空格＋标点`（原文「…培训 [E4]。」→ 剥编号 →「…培训 。」）—— 收掉
    t = t.replace(/\s+([。！？；：,，.、）】])/g, "$1");
    if (!t.trim()) continue;
    out.push(t.trim());
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function renderMarkdown(markdown) {
  const lines = markdown.split(/\r?\n/); let html = "", list = false, quote = false, table = false, citeBlock = false;
  const close = () => { if (list) { html += "</ul>"; list = false; } if (quote) { html += "</blockquote>"; quote = false; } if (table) { html += "</tbody></table>"; table = false; } };
  // 证据条目的续行（长文本被拆成多行，首行 `- `，续行无前缀，`[E1]` 可能落在靠后行）
  // 必须**归并成一段**，否则一段证据会被渲染成十几个 `<p>`/`<li>` —— 页面碎（2026-09-14 实测样本碎裂由此来）。
  let evBlock = [];
  const flush = () => {
    if (!evBlock.length) return;
    const merged = evBlock.join("\n");
    const refs = (merged.match(/\[E\d+\]/g) || []).map(r => `<sup class="cite-ref">${r}</sup>`).join("");
    const body = merged.replace(/\[E\d+\]/g, "").replace(/^- /, "").trim();
    html += `<p class="draft-para">${inline(body)}${refs ? " " + refs : ""}</p>`;
    evBlock = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // 出处行：附属于当前证据块 → 先收束证据段，再输出出处小注
    if (/^ {2,}- 出处：/.test(line)) {
      flush();
      close();
      html += `<p class="draft-cite">出处：${inline(line.replace(/^ {2,}- 出处：/, ""))}</p>`;
      continue;
    }
    // 证据块开始：`- ` 开头且非引用清单行（引用清单是 `` - `E1` ``）
    const isCiteListRow = /^- `[E\d]+`/.test(line);
    if (/^- /.test(line) && !isCiteListRow && !/^(#|>|\|)/.test(line)) {
      close();                     // 收束之前的列表/表格
      evBlock = [line];            // 本行是证据首行，开新证据块
      continue;
    }
    // 证据块的续行（普通文本、非标记、非空行）→ 并入当前证据块
    if (evBlock.length && !/^(#|>|\|)/.test(line) && !/^---+\s*$/.test(line) && line.trim()) {
      evBlock.push(line);
      continue;
    }
    flush();                       // 任何其它标记出现 → 证据块到此为止
    // 引用清单汇总块收进可折叠清单（段内每段已有出处，这里只留核对用全文）
    if (/^## 引用来源/.test(line)) { close(); html += "<details class='cite-list'><summary><strong>引用来源（核对清单）</strong></summary>"; citeBlock = true; continue; }
    if (/^#{1,3} /.test(line)) { close(); const level = line.match(/^#+/)[0].length; html += `<h${level}>${inline(line.slice(level + 1))}</h${level}>`; continue; }
    if (/^>/.test(line)) { if (!quote) { close(); html += "<blockquote>"; quote = true; } html += `<p>${inline(line.replace(/^>\s?/, ""))}</p>`; continue; }
    if (/^- /.test(line)) { if (!list) { close(); html += "<ul>"; list = true; } html += `<li>${inline(line.slice(2))}</li>`; continue; }
    if (/^\|.*\|$/.test(line) && !/^\|[-: |]+\|$/.test(line)) {
      if (!table) { close(); html += "<table><tbody>"; table = true; }
      html += "<tr>" + line.slice(1,-1).split("|").map(cell => `<td>${inline(cell.trim())}</td>`).join("") + "</tr>"; continue;
    }
    if (/^\|[-: |]+\|$/.test(line)) continue;
    if (/^---+\s*$/.test(line)) continue;
    if (!line.trim()) { continue; }
    close(); html += `<p>${inline(line)}</p>`;
  }
  flush(); close(); if (citeBlock) html += "</details>";
  return html;
}

// —— 需求二：一个输入框 → 一份方案 ——
// ponytail: 原先这里是**两步**（先点「找证据」看一遍历史原文，再点按钮「生成草稿」）。
// 但两个生成按钮读的是**同一个输入框**，且 `/api/proposal-generate` 返回的 `citations`
// 已经逐条给出出处 —— 中间那一步不产生用户要的东西。按第一性原理删掉中间步骤与
// 冻结示范稿（原页签 02）：**需求二就是一个输入框 → 一份方案**。

// 注：原先这里有一个 `[data-proposal]`（3 个示例按钮「点一下直接生成」）的绑定 ——
// 那 3 个按钮已在 2026-09-14 的示例瘦身中移除（改由 `#proposal-picker` 的模块按钮填入输入框），
// 绑定成了死代码，已删。示例只保留在页签 01 的折叠里。

$("#proposal-form").addEventListener("submit", event => {
  event.preventDefault();
  // 需求二只保留**模型生成**路径（2026-09-14 用户指令）。
  // 未授权时接口明确 403、不会有任何外发 —— 该守卫在服务端；页面上不显示外发说明（用户要求）。
  generateDraft("llm");
});

// —— 03 生成草稿：只有模型起草一条路，无本地拼装 ——
// 注：页面上**不再显示外发说明**（2026-09-14 用户要求）。未授权时接口仍 403、不会外发 —— 安全网在服务端。
const MODE_LABEL = {
  llm: "模型起草",
};

let _draftInFlight = false;

async function generateDraft(mode) {
  const target = $("#gen-result"); target.className = "result-area";
  // ⚠️ 评审 P1：原先 25 秒里没有任何进度、按钮也不禁用 —— 业务同事以为卡死会再点一次，
  // **第二次同样把正文发往公司网关**（重复外发、重复计费）。现在同一时刻只允许一个在途请求。
  if (_draftInFlight) return;
  _draftInFlight = true;
  const btns = ["#proposal-form button[type=submit]"]
    .map(s => $(s)).filter(Boolean);
  btns.forEach(b => { b.disabled = true; });
  const t0 = Date.now();
  const timer = setInterval(() => {
    target.querySelectorAll(".elapsed").forEach(el => {
      el.textContent = `已用 ${Math.round((Date.now() - t0) / 1000)} 秒`;
    });
  }, 1000);
  const done = () => {
    clearInterval(timer); _draftInFlight = false;
    btns.forEach(b => { b.disabled = false; });
  };
  // 进度反馈：模型起草要 20~30 秒，必须让用户看到"还在跑"（否则会以为卡死、再点一次 ——
  // 那原本是评审 P1 的问题；`_draftInFlight` + 禁用按钮挡住了重复请求，这里补上"看得见的进度"）。
  // ⚠️ 之前这个计时器指向一个**永不存在**的 `.elapsed` 元素（空转）—— 现已把该元素放进加载卡。
  target.innerHTML = `<div class='result-card'>正在起草方案… <span class="elapsed"></span></div>`;
  try {
    const data = await request("/api/proposal-generate", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({query: $("#proposal-query").value, mode})
    });
    const warn = (data.warnings || []).map(w => `<li>⚠️ ${esc(w.message)}</li>`).join("");
    const gaps = (data.gaps || []).map(g => `<li>证据不足：${esc(g.module)}（${esc(g.status)}）</li>`).join("");
    const problems = (data.validation || []).map(p => `<li>校验未通过：${esc(p)}</li>`).join("");
    // ⚠️ 冲突/缺节是**必须一眼看见**的信息（关系到撤回风险与诚实边界），不折叠；
    // 但三条警示不要再各占一张黄卡片 —— 合成一张清单。
    const alerts = problems + warn + gaps;
    // 「系统理解成了什么 + 证据够不够」——2026-09-14 业务评审：方案生成原是个黑盒，
    // 用户看不到识别了哪些小节、证据够不够。**纯渲染**（响应里已有 modules/coverage/gaps），不新增接口。
    const MOD_STATUS = { ok: "证据充足", sparse: "证据不足", insufficient: "没有找到" };
    const modTags = (data.modules || []).map(m => {
      const n = (m.evidence || []).length;
      return `<span class="tag cond-tag-static">${esc(m.module)} · ${esc(MOD_STATUS[m.status] || m.status)}`
             + `${n ? ` · ${n} 条证据` : ""}</span>`;
    }).join("");
    const cov = data.coverage || {};
    const covLine = cov.requested
      ? `证据覆盖 ${cov.ok || 0}/${cov.requested} 个小节充足`
        + (cov.sparse ? `，${cov.sparse} 个不足` : "")
        + (cov.insufficient ? `，${cov.insufficient} 个没找到` : "")
      : "";
    const cites = (data.citations || []).map(c =>
      `<li><b>${esc(c.ref)}</b> · ${esc(c.heading || "（无标题）")} <span class="excerpt">${esc(c.file_name)}</span></li>`).join("");
    target.innerHTML = `<div class="summary"><h3>方案草稿（不是最终稿）</h3>
      <p><strong>本次产物：</strong>${esc(MODE_LABEL[data.mode] || data.mode)}</p></div>
      ${modTags ? `<div class="cond-tags"><span class="cond-hint">识别到的方案小节${covLine ? `（${esc(covLine)}）` : ""}：</span>${modTags}</div>` : ""}
      ${alerts ? `<div class="result-card warning"><ul class="fact-list">${alerts}</ul></div>` : ""}
      <article class="draft">${renderMarkdown(data.markdown || "")}</article>
      <details class="cite-list" style="margin-top:14px"><summary><strong>用稿须知</strong></summary>
        <p style="margin:8px 0 0;font-size:12px;color:var(--muted)">${esc(data.scope_note || "")}</p></details>
      ${cites ? `<details class="cite-list" style="margin-top:10px"><summary><strong>引用来源 ${(data.citations || []).length} 条（逐条可核对）</strong></summary>
        <ul class="fact-list">${cites}</ul></details>` : ""}
      <div class="scheme-actions" style="margin-top:14px">
        <button class="copy" id="copy-cites">复制全部来源文件位置（${(data.citations || []).length} 个）</button>
        <button type="button" id="copy-draft">复制这份草稿全文</button>
      </div>`;
    const copyBtn = $("#copy-cites");
    if (copyBtn) {
      const paths = [...new Set((data.citations || []).map(c => c.source_path).filter(Boolean))];
      copyBtn.addEventListener("click", () => copyText(copyBtn,
        paths.join(String.fromCharCode(10)), `已复制 ${paths.length} 个文件位置`));
    }
    const copyDraft = $("#copy-draft");
    if (copyDraft) {
      copyDraft.addEventListener("click", () => copyText(copyDraft,
        markdownToPlainText(data.markdown || ""), "已复制草稿正文（不含证据编号）"));
    }
  } catch (error) { target.innerHTML = `<div class="error">${esc(error.message)}</div>`; }
  finally { done(); }
}

// 需求二只保留模型生成一条路：生成按钮就一个（表单提交），无 `#gen-llm` 冗余按钮（2026-09-14）。
// 未授权时接口 403、页面不静默外发 —— 安全网在服务端（`app/proposal.py::_guard`），页面不显示该说明。

// —— 需求一：三类材料定位（项目业绩 / 财务社保数据 / 仪器设备清单）——
// 需求方 2026-09-13 明确**只做这三类**，括号里是每一类必须识别出来的源信息。
const MODULE_ORDER = ["项目业绩", "财务社保数据", "仪器设备清单"];
const MODULE_FIELDS = {
  "项目业绩": "哪个产品 · 合同金额 · 合同签订日期 · 是否有收款凭证",
  "财务社保数据": "包含什么月份",
  "仪器设备清单": "哪些仪器 · 是否有采购合同 · 发票 · 仪器照片",
};

// 收款凭证必须分清状态：**「没找到证据」不等于「没收钱」**，不能混着说。
const PAY_LABEL = {
  yes: "台账记载有付款时间",
  no: "台账明确写着「未到款」",
  unknown: "台账有这份合同，但未记付款",
  ledger_absent: "这份合同不在收款凭证台账里（≠ 没收钱，只是台账没覆盖它）",
  receipt_file: "有以该合同号命名的银行回单图片（未核内容）",
};
const ROLE_LABEL = {
  our: "我方响应/最终版",
  tender: "招标要求（采购人要求，非我方已附）",
  other: "其它来源文件",
};
const yuan = (v) => (v == null ? "金额未确认" : `¥ ${Number(v).toLocaleString("zh-CN")}`);
const FACT_LABEL = {
  finance_period: "财务期间", social_security_month: "社保月份", instrument: "列有仪器设备",
  instrument_name: "仪器", instrument_purchase_contract: "仪器采购合同",
  purchase_contract: "采购合同", invoice: "发票", instrument_photo: "仪器照片",
  qualification: "资质证书",
};
// 兜底也要是中文 —— 不能让新枚举（如日后新增的 fact_type）直接上屏（评审 P1）
const factLabel = (k) => FACT_LABEL[k] || "其他材料";
const csvSpec = (headers, rowOf) => ({headers, rowOf});

/** 三类结果共用：项目名 / 文件 / 格式 / 源路径 这四行是 §2 点名要的。
 *  ⚠️ 材料存在性接口（`/api/material-facts`）**不返回 `content_format`** ——
 *  缺字段时应整行省略，不能让 222 份文件之外的东西显示成「格式未登记」（那会被读成"格式异常"）。
 */
const metaBlock = (r) => `<dl class="metadata">
  <dt>项目</dt><dd>${esc(r.project_folder || "（未登记）")}</dd>
  <dt>文件</dt><dd>${esc(r.file_name)}</dd>
  ${r.content_format ? `<dt>格式</dt><dd>${esc(formatLabel(r.content_format))}</dd>` : ""}
  <dt>打开原文</dt><dd>${fileActions(r)}</dd>
</dl>`;

function renderProjectRows(rows) {
  return rows.map(r => {
    const products = (r.products || []);
    const list = products.length
      ? `<ul class="fact-list">${products.map(p => {
          const amt = p.amount_known ? yuan(p.amount) : "金额未确认";
          const derived = p.derived ? "<small class='derived'>（由数量×单价推算）</small>" : "";
          const zero = (p.amount === 0) ? "<small class='derived'>（文档明写 0.00 的赠送行，非缺失）</small>" : "";
          return `<li><b>${esc(p.product)}</b> · ${amt}${derived}${zero}
            <span class="muted"> · 同一产品 ${p.detail_rows} 行明细</span></li>`;
        }).join("")}</ul>`
      : `<div class="evidence">这份合同没有可识别的产品明细行 —— 产品金额未确认，不是 0。</div>`;
    return `<article class="result-card">
      <div class="card-top"><h3>${esc(r.contract_number || "（无合同编号）")}</h3>
        <span class="amount">合同总额 ${yuan(r.total_amount)}</span></div>
      <span class="tag">签订日期 ${esc(r.contract_date || "未提取到")}</span>
      <span class="tag">${esc([r.party_a, r.party_b].filter(Boolean).join(" / ") || "甲乙方未提取")}</span>
      <span class="tag">收款凭证：${esc(PAY_LABEL[r.payment_status] || r.payment_status)}</span>
      ${metaBlock(r)}
      ${(r.receipt_files || []).length ? `<div class="evidence">银行回单文件（${r.receipt_count} 张，来自
        ${esc(baseName(r.receipt_files[0].source_zip) || "压缩包")}）：
        ${esc(r.receipt_files.map(f => baseName(f.inner_path)).join("、"))}
        <br><strong>只读了文件名，没有核对图片内容</strong> —— 不能据此断定款项已到账。</div>` : ""}
      <div class="evidence">识别出的产品与产品级金额（按同产品明细行合计）：</div>
      ${list}
    </article>`;
  }).join("");
}

/** 材料事实的期间显示（**两处共用，勿各写一份**）：
 *  折叠后 `fact_values` 有多值就一并显示；期间未知（null）**不是缺失**，如实写「期间未提取到」。 */
function factValuesText(r) {
  const vals = (r.fact_values || [r.fact_value]).filter(v => v !== null && v !== undefined && v !== "");
  return vals.length ? vals.join(" / ") : "期间未提取到";
}

function renderFactRows(rows) {
  return rows.map(r => {
    const shown = factValuesText(r);
    const more = (r.record_count || 1) > 1 ? `<span class="tag">本文件 ${r.record_count} 条</span>` : "";
    return `<article class="result-card ${r.role_scope === "tender" ? "warning" : ""}">
    <div class="card-top"><h3>${esc(FACT_LABEL[r.fact_type] || r.fact_type)}</h3>
      <span class="amount">${esc(shown)}</span></div>
    <span class="tag">${esc(ROLE_LABEL[r.role_scope] || "")}</span>${more}
    ${metaBlock(r)}
    ${r.evidence_text ? `<div class="evidence">文件中对应文字：${esc(r.evidence_text)}</div>` : ""}
  </article>`; }).join("");
}

const MODULE_CSV = {
  "项目业绩": csvSpec(
    ["合同编号", "项目", "文件", "格式", "签订日期", "合同总额", "产品", "产品金额", "金额来源", "收款凭证", "文件位置"],
    (r) => {
      const p = (r.products || [])[0] || {};
      return [r.contract_number, r.project_folder, r.file_name, formatLabel(r.content_format),
              r.contract_date, r.total_amount, p.product || "", p.amount ?? "",
              p.derived ? "由数量×单价推算" : "合同明写",
              PAY_LABEL[r.payment_status] || r.payment_status, r.source_path];
    }),
  "财务社保数据": csvSpec(
    ["类别", "期间", "角色", "项目", "文件", "格式", "文件内文字", "文件位置"],
    (r) => [FACT_LABEL[r.fact_type] || r.fact_type, r.fact_value, ROLE_LABEL[r.role_scope],
            r.project_folder, r.file_name, formatLabel(r.content_format),
            r.evidence_text, r.source_path]),
  "仪器设备清单": csvSpec(
    ["类别", "期间", "角色", "项目", "文件", "格式", "文件内文字", "文件位置"],
    (r) => [FACT_LABEL[r.fact_type] || r.fact_type, r.fact_value, ROLE_LABEL[r.role_scope],
            r.project_folder, r.file_name, formatLabel(r.content_format),
            r.evidence_text, r.source_path]),
};

function bindCopyButtons(target) {
  target.querySelectorAll("[data-copy]").forEach(button => button.addEventListener("click", () => {
    copyText(button, button.dataset.copy, "已复制");
  }));
}

/** 三类快捷入口：**只回填卡片，不碰结果区**。
 *  01 页签里「按类浏览」与「直接提问」共用同一个结果区 —— 两者都是需求一，
 *  不该拆成两个页签（用户 2026-09-13 指出）。
 */
async function loadModulePicker() {
  const picker = $("#module-picker");
  if (!picker) return;
  try {
    const data = await request("/api/three-modules");
    picker.innerHTML = MODULE_ORDER.map(name => {
      const m = data.modules[name] || {};
      return `<button class="module-card" data-module="${esc(name)}">
        <b>${esc(name)}</b>
        <span class="count">${m.count ?? 0} ${esc(m.unit || "条")}</span>
        <span class="fields">${esc(MODULE_FIELDS[name] || "")}</span>
      </button>`;
    }).join("");
    picker.querySelectorAll("[data-module]").forEach(button =>
      button.addEventListener("click", () => loadModule(button.dataset.module)));
  } catch (error) {
    picker.innerHTML = `<div class="error">${esc(error.message)}</div>`;
  }
}

async function loadModule(name) {
  const target = $("#search-result");          // 与「直接提问」共用结果区
  target.className = "result-area";
  target.innerHTML = `<div class='result-card'>正在读取「${esc(name)}」…</div>`;
  try {
    // limit 取高值：`ORDER BY fact_type ... LIMIT` 会按类别顺序截断，
    // 排在后头的类别（如「仪器采购合同」）会被整类切掉，页面看起来像「这一类没有」。
    const data = await request(`/api/three-modules?module=${encodeURIComponent(name)}&limit=1000`);
    const rows = data.records || [];
    // ⚠️ 材料事实类走**按文件折叠后**的 `files`（2026-09-16 需求方反馈「检索还返回很多相同的文件」）：
    // 一条期间一行会让同一份文件重复出现（社保缴费记录表一份文件就有好几个月）。
    // `files` 由后端折叠（期间收进 `fact_values`）；缺该字段时回退到原始 records。
    const folded = data.files && data.files.length ? data.files : rows;
    const cards = name === "项目业绩" ? renderProjectRows(rows) : renderFactRows(folded);
    // 类别存量必须上屏：**没显示出来的类别 ≠ 没有这类材料**
    const tc = data.type_counts || {};
    const chips = Object.entries(tc)
      .map(([t, n]) => `<span class="tag">${esc(FACT_LABEL[t] || t)} ${n}</span>`).join("");
    const truncNote = data.truncated
      ? `<div class="boundary-note">⚠️ 本次显示 ${rows.length} 条，库内共 ${data.total_available} 条，已截断；
          上面每个类别的**存量**见右侧标签，未出现的类别不代表没有。</div>` : "";
    const roleNote = (data.our_count == null) ? ""
      : `<div class="boundary-note">本次 ${data.count} 条中，<strong>${data.our_count} 条来自我方响应/最终版文件</strong>`
        + `（这才是需求一问的「响应文件里有没有」）；${data.tender_count} 条来自<strong>招标要求</strong>`
        + `（采购人要求提交的材料，<strong>不等于我方已附</strong>）；其余 ${data.other_count} 条来自资质附件 / 过程材料 / 待复核。</div>`;
    // 产品族汇总：直接回答「哪个产品」。归一后 411 个原始串 → 58 个族；**不同产品不会被并**。
    const ps = data.product_summary || [];
    const prodChips = ps.length ? `<div class="type-chips">产品族（按合同数）：
      ${ps.slice(0, 14).map(p => `<span class="tag">${esc(p.canonical)} · ${p.contracts} 份合同${
        p.amount_known ? ` · ¥${Math.round(p.amount).toLocaleString("zh-CN")}` : ""}</span>`).join("")}
      ${ps.length > 14 ? `<span class="muted">…共 ${ps.length} 个产品族</span>` : ""}</div>` : "";
    // 单位跟卡上一致：项目业绩一条就是一份合同，别写「95 条」（卡上写「95 份合同」，
    // 同屏两个量词会被读成两个数）。
    const unit = name === "项目业绩" ? "份合同" : "条";
    target.innerHTML = `<div class="summary"><h3>${esc(name)} · ${rows.length} ${unit}</h3>
      <p>${esc(MODULE_FIELDS[name] || "")}</p></div>
      ${prodChips}
      ${chips ? `<div class="type-chips">各类别存量：${chips}</div>` : ""}
      ${truncNote}
      ${roleNote}
      <div class="boundary-note">${esc(data.scope_note || "")}</div>
      ${batchBar(rows, MODULE_CSV[name], "条记录")}
      ${cards || "<div class='result-card'>这一类暂无可展示的记录。</div>"}
      ${name === "项目业绩" ? ledgerCards(data) : ""}`;
    bindCopyButtons(target);
    bindOpenButtons(target);
    bindBatchBar(target, rows, `三类材料定位-${name}.csv`, MODULE_CSV[name]);
  } catch (error) { target.innerHTML = `<div class="error">${esc(error.message)}</div>`; }
}

loadModulePicker();

// —— 需求二：方案模块入口（**只填输入框，不自动生成**）——
// 由来（B2B 评审 P2）：页签 02 原先只有 3 个示例按钮，多数模块在界面上没有入口。
// 词表**从后端取**（`/api/modules` 读的就是生成用的 `app/module_keywords.json`），
// 前端不另抄一份 —— 抄一份就会像「仪器设备 546 vs 722」那样两处分叉。
//
// 点击行为：**不覆盖用户已经写好的内容**，也不重复堆同一个模块；只填不生成，
// 用户可以接着补「必须包含…」再点生成。
function fillProposalQuery(name, input) {
  const cur = (input.value || "").trim();
  if (!cur) input.value = name;
  else if (!cur.includes(name)) input.value = `${cur}，${name}`;
  return input.value;
}

async function loadProposalPicker() {
  const picker = $("#proposal-picker");
  if (!picker) return;
  try {
    const data = await request("/api/modules");
    const mods = Array.isArray(data.modules) ? data.modules : [];
    picker.innerHTML = mods.map(m =>
      `<button type="button" data-fill="${esc(m.module)}" title="${esc((m.keywords || []).join(" / "))}">${esc(m.module)}</button>`
    ).join("");
    picker.querySelectorAll("[data-fill]").forEach(button => button.addEventListener("click", () => {
      fillProposalQuery(button.dataset.fill, $("#proposal-query"));
      $("#proposal-query").focus();
    }));
  } catch (error) {
    picker.innerHTML = `<span class="muted">模块词表读取失败：${esc(error.message)}</span>`;
  }
}

loadProposalPicker();

// —— 已下线（2026-09-14 业务评审）：招标要求核对 + 模块化经验从页面移除 ——
// 两者都**不是**用户要的两个需求之一。后端端点全部保留：
//   · `/api/tender-check` —— 用户已明确「暂停开发」，页面不再提供入口；
//   · `/api/module-kb` —— 是生成时**内部**用的跨项目归纳（`proposal-generate` 会自己调
//     `build_module_kb`），不该作为普通用户单独理解的工具。
// 想恢复：把 index.html 里的表单与这两个绑定加回来即可（函数体在 git 历史/交接文档里有记载）。
