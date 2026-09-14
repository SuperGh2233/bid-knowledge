"""前端 JS 的**静态护栏**。

由来（2026-09-14 B2B 评审 P0）：`static/app.js` 里写成了 **Python 的 `str.rsplit`**（JS 没有），
`TypeError` 抛在 `Array.map` 里 → **「项目业绩」整块 98 条合同渲染不出来**，屏上只有一句英文异常。
`node --check` 抓不到它（语法合法），必须靠「用真实数据跑一遍渲染」或静态禁用表。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
APP_JS = BASE / "static" / "app.js"
INDEX_HTML = BASE / "static" / "index.html"

# 由 JS 在运行期用 innerHTML 现场创建、因此**不在** index.html 里的 id（且代码已判空）
_RUNTIME_CREATED_IDS = {"copy-cites", "copy-draft"}


def _dom_ids() -> tuple[set, set]:
    """(app.js 里 `$("#id")` 引用的 id, index.html 里声明了的 id)。"""
    js_ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', APP_JS.read_text(encoding="utf-8")))
    html_ids = set(re.findall(r'id="([^"]+)"', INDEX_HTML.read_text(encoding="utf-8")))
    return js_ids, html_ids


def test_app_js_only_references_ids_that_exist_in_html():
    """**JS 引用的 DOM id 必须在 index.html 里存在。**

    由来（2026-09-14）：删掉 index.html 里的元素却忘了删 JS 里的引用，会让顶层语句抛
    `TypeError` 并**中断整个脚本**（后面所有绑定一起废）。而渲染冒烟用的 stub
    `document.querySelector` 对**任意**选择器都返回 mock，物理上抓不到这个错 —— 实测踩过。
    """
    js_ids, html_ids = _dom_ids()
    missing = js_ids - html_ids - _RUNTIME_CREATED_IDS
    assert not missing, (
        "app.js 引用了 index.html 里不存在的 id（顶层会 TypeError 并中断整个脚本）："
        f"{sorted(missing)}")


def test_no_unguarded_top_level_dom_bindings():
    """**顶层不得有未判空的 `$("#id").…` 语句。**

    比上一条更窄更硬：它精确编码"删 HTML 忘删 JS"的失败模式。
    今天只有两处匹配（`#load-kb` / `#tender-form`），都在 HTML 里 —— 一旦删了 HTML 而没删语句，
    这条会立刻红。
    """
    html_ids = set(re.findall(r'id="([^"]+)"', INDEX_HTML.read_text(encoding="utf-8")))
    offenders = []
    for lineno, line in enumerate(APP_JS.read_text(encoding="utf-8").splitlines(), 1):
        m = re.match(r'^\$\("#([A-Za-z0-9_-]+)"\)\.', line)   # 列 0 = 顶层语句
        if m and m.group(1) not in html_ids:
            offenders.append(f"{APP_JS.name}:{lineno} → #{m.group(1)}")
    assert not offenders, (
        "顶层存在未判空的 DOM 绑定，但对应元素已不在 index.html："
        f"{offenders} —— 请连同该语句一起删除（或加 if 判空）")

# Python 专有、JS 里**不存在**的字符串/集合方法。写进去必然运行时 TypeError。
PYTHON_ONLY = (
    "rsplit", "startswith", "endswith", "lstrip", "rstrip", "encodeURIComponent(",  # 最后一个是 JS 的，不在禁用表
)
_FORBIDDEN = re.compile(r"\.(rsplit|startswith|endswith|lstrip|rstrip|iteritems|has_key)\s*\(")


def test_app_js_has_no_python_only_methods():
    """`app.js` 里不得出现 Python 专有的字符串方法 —— 它们在浏览器里必然抛错。"""
    src = APP_JS.read_text(encoding="utf-8")
    hits = [f"第 {src[:m.start()].count(chr(10)) + 1} 行: {m.group(0)}" for m in _FORBIDDEN.finditer(src)]
    assert not hits, "app.js 里出现 Python 专有方法（JS 没有）—— 会在浏览器抛 TypeError：\n  " + "\n  ".join(hits)


@pytest.mark.skipif(shutil.which("node") is None, reason="本机没有 node，跳过渲染冒烟")
def test_app_js_renders_with_real_shaped_data():
    """用**真实形状的数据**跑一遍渲染函数 —— 抓 `node --check` 抓不到的运行时错误。

    数据形状取自 `/api/three-modules?module=项目业绩` 的真实响应（含 `receipt_files`）。
    """
    probe = BASE / "tmp" / "_render_probe.js"
    probe.parent.mkdir(exist_ok=True)
    # 把 index.html 里**真实存在的 id** 注入探针：让 DOM 桩忠于真实 DOM ——
    # 引用不存在的 id 就返回 null，于是"删了 HTML 却忘了删 JS 引用"会在顶层 eval 阶段
    # 直接抛 TypeError，被这条冒烟抓住（旧的桩对任意选择器都返回 mock，物理上抓不到）。
    from_html = set(re.findall(r'id="([^"]+)"', INDEX_HTML.read_text(encoding="utf-8")))
    probe_src = """
const fs=require('fs');const store={};
const REAL_IDS=new Set(__REAL_IDS__);
const mk=()=>({innerHTML:'',textContent:'',className:'',dataset:{},value:'',files:[],
  classList:{add(){},remove(){},contains(){return false}},addEventListener(){},
  querySelector(){return null},querySelectorAll(){return []},appendChild(){},click(){},remove(){}});
global.document={querySelector:s=>{
    if(/^#[A-Za-z0-9_-]+$/.test(s) && !REAL_IDS.has(s.slice(1))) return null;   // 忠于真实 DOM
    return store[s]=store[s]||mk();
  },querySelectorAll:()=>[],
  createElement:mk,body:{appendChild(){}}};
global.window={};global.navigator={clipboard:{writeText:async()=>{}}};
global.fetch=()=>Promise.resolve({ok:true,json:async()=>({counts:{},scope:{},modules:{}})});
global.URL.createObjectURL=()=>'b';global.URL.revokeObjectURL=()=>{};global.Blob=function(){};
const src=fs.readFileSync(process.argv[2],'utf8');
eval(src);
eval('globalThis.__R={renderProjectRows,renderFactRows,renderSchemeAnswer,renderContractAnswer,fillProposalQuery,loadProposalPicker,renderMarkdown,generateDraft,conditionTags,conditionQuery};');
const R=globalThis.__R;
// 形状 = /api/three-modules?module=项目业绩 的真实响应（含 5 条带 receipt_files 的记录）
const perf={records:[{contract_id:'c1',contract_number:'YOE1',party_a:'A',party_b:'B',
  contract_date:'2024-01-02',total_amount:100,payment_status:'receipt_file',receipt_count:2,
  receipt_files:[{inner_path:'银行回单/X/a.png',source_zip:'proj/合同/银行回单.zip'}],
  content_format:'native_text',project_folder:'P',file_name:'f.docx',source_path:'s',
  product_count:1,products:[{product:'p',canonical:'p',amount:10,amount_known:true,
  detail_rows:2,zero_amount_rows:0,derived:false}]}]};
const cases=[
  ['renderProjectRows',()=>R.renderProjectRows(perf.records)],
  ['renderFactRows',()=>R.renderFactRows([{fact_type:'qualification',fact_value:null,
    evidence_text:'ISO',project_folder:'P',relative_path:'P/a.docx',source_path:'s',
    file_name:'a.docx',role_scope:'our'}])],
  ['renderSchemeAnswer',()=>R.renderSchemeAnswer({query:'q',sections:[{heading:'h',score:1,
    content_format:'native_text',project_folder:'P',file_name:'f',source_path:'s',
    text_excerpt:'t'}],scope_note:''},mk())],
  ['renderContractAnswer',()=>R.renderContractAnswer({parsed:{product:'p',minimum_amount:0},
    hits:[{hit:true,product:'p',amount:1,contract_id:'c',file_name:'f',project_folder:'P',
    source_path:'s',records:[{product:'p',amount:1}],record_count:2}],excluded:[],
    filter_note:'',scope_note:''},mk())],
];
for(const [name,fn] of cases){ try{ fn(); }catch(e){ console.log('FAIL '+name+': '+e.message); process.exit(1);} }

// —— 「文件优先」card 的**行为断言**（2026-09-14 业务评审）——
// 上面那条 renderContractAnswer 用例只断言"不抛异常"，改标题它**不会红** → 等于空网。
// 这里钉住真正的要求：主标题是**文件名**（不是产品名）+ 三件套按钮 + 缺 document_id 时的降级。
const cardBox = mk();
R.renderContractAnswer({
  parsed:{product:'代谢组', minimum_amount:20000},
  hits:[{hit:true, product:'代谢组', amount:30000, contract_id:'c', document_id:'d',
         file_name:'投标文件 正文.docx', project_folder:'项目甲',
         source_path:'\\\\nas\\项目甲\\投标文件 正文.docx', contract_number:'YOE1',
         contract_date:'2024-01-02', party_a:'甲方', party_b:'乙方', total_amount:50000,
         records:[{product:'代谢组', amount:30000}], record_count:1}],
  excluded:[], filter_note:'', scope_note:'', hit_records:1,
}, cardBox);
const cardHtml = cardBox.innerHTML || '';
// 注意：摘要区也有 <h3>，必须**精确定位卡片内**的标题（`.file-head` 里那个）
const firstH3 = (cardHtml.match(/<div class="file-head">[\\s\\S]*?<h3>(.*?)<\\/h3>/) || [])[1] || '';
if (firstH3 !== '投标文件 正文.docx') {
  console.log('FAIL 文件优先：主标题应为文件名，实际="' + firstH3 + '"'); process.exit(1);
}
if (!cardHtml.includes('data-open="folder"') || !cardHtml.includes('data-open="file"')) {
  console.log('FAIL 文件优先：缺「打开所在文件夹/打开文件」按钮'); process.exit(1);
}
if (!cardHtml.includes('data-copy')) { console.log('FAIL 文件优先：缺「复制路径」按钮'); process.exit(1); }
if (!cardHtml.includes('≥ 门槛')) { console.log('FAIL 文件优先：缺「为什么符合条件」'); process.exit(1); }

// 缺 document_id（老数据/其他来源）→ **不得**渲染打开按钮（点了必然失败），但复制路径要在
const noDocBox = mk();
R.renderContractAnswer({parsed:{product:'p', minimum_amount:0},
  hits:[{hit:true, product:'p', amount:1, file_name:'f.docx', project_folder:'P', source_path:'s'}],
  excluded:[], filter_note:'', scope_note:''}, noDocBox);
const noDocHtml = noDocBox.innerHTML || '';
if (noDocHtml.includes('data-open=')) {
  console.log('FAIL 无 document_id 时仍渲染了打开按钮（点了必然失败）'); process.exit(1);
}
if (!noDocHtml.includes('data-copy')) {
  console.log('FAIL 无 document_id 时应保留「复制路径」'); process.exit(1);
}

// —— 草稿渲染（renderMarkdown）冒烟：用**真实形状**的草稿文本跑一遍 ——
// 2026-09-14 整理草稿渲染后补。旧渲染把出处行/正文/引用清单渲染成三四套样式且出处重复，
// 新渲染必须能处理：1) 证据行带 [E1][E2] 上标；2) 出处行降为小注；3) 引用来源收进折叠块；
// 4) 冲突并列行（含数字、无编号）。这些在真实产物里都出现。
const draftMd = [
  '# 方案草稿（本地抽取式装配）', '',
  '## 培训方案', '',
  '- 制定年度培训计划，涵盖专业技能、政策法规、行业动态等内容。[E1][E2]',
  '  - 出处：投标文件 正文.docx（职业培训制度）',
  '- 「培训方案」的「响应」在不同历史文件里不一致：24h（…）；48h（…）—— 请人工确认，系统不自动择一',
  '',
  '## 引用来源（逐条可打开核对）', '',
  '- `E1` 职业培训制度 — 投标文件 正文.docx',
  '- `E2` 3.4项目管理及培训方案 — 欧易响应文件.docx',
].join(String.fromCharCode(10));
try {
  const html = R.renderMarkdown(draftMd);
  if (!html.includes('draft-para')) { console.log('FAIL 证据行未解析为段落'); process.exit(1); }
  if (!html.includes('cite-ref')) { console.log('FAIL 引用未解析为上标'); process.exit(1); }
  if (!html.includes('draft-cite')) { console.log('FAIL 出处行未降为小注'); process.exit(1); }
  if (!html.includes('cite-list')) { console.log('FAIL 引用来源未收进折叠块'); process.exit(1); }
  if (!html.includes('不一致')) { console.log('FAIL 冲突并列行丢失'); process.exit(1); }
} catch (e) { console.log('FAIL renderMarkdown: ' + e.message); process.exit(1); }

// —— generateDraft 集成冒烟：真实形状的生成响应 → 结果区 HTML 必须包含新结构 ——
// 2026-09-14 整理草稿结果区后补。断言：1) 警示合成一张卡（不再三张黄条叠堆）；
// 2) 「用稿须知」折叠块在；3) 引用来源收进 details；4) 复制草稿全文按钮在。
const draftResp = { mode:'llm', kb_used:false, scope_note:'这是草稿，不是最终稿…校验需逐条处理。',
  markdown: ['# 方案草稿（本地抽取式装配）','','## 售后方案','',
    '- 服务周期 24 个月内响应。[E1]','  - 出处：投标文件 正文.docx（售后服务承诺 1 项）','',
    '---','','## 引用来源（逐条可打开核对）','','- `E1` 售后服务承诺 1 项 — 投标文件 正文.docx']
    .join(String.fromCharCode(10)),
  citations:[{ref:'E1',heading:'售后服务承诺 1 项',file_name:'投标文件 正文.docx',source_path:'\\\\nas\\01 投标项目文件\\X\\投标文件 正文.docx'}],
  warnings:[{message:'「售后方案」的「响应」在不同历史文件里不一致：24h（…）；48h（…）'}],
  gaps:[], validation:[], modules:['售后方案'], coverage:{requested:1,ok:1,sparse:0,insufficient:0} };
const prevFetch = global.fetch;
global.fetch = (url, opts) => String(url).includes('/api/proposal-generate')
  ? Promise.resolve({ok:true, json: async () => draftResp})
  : Promise.resolve({ok:true, json: async () => ({counts:{},scope:{},modules:{}})});
R.generateDraft('llm').then(
  () => {
    global.fetch = prevFetch;
    const out = store['#gen-result'].innerHTML || '';
    if (!out.includes('draft-para')) { console.log('FAIL 草稿正文未渲染'); process.exit(1); }
    if (!out.includes('用稿须知')) { console.log('FAIL 用稿须知折叠块缺失'); process.exit(1); }
    if (!out.includes('引用来源 1 条')) { console.log('FAIL 引用来源未折叠'); process.exit(1); }
    if (!out.includes('copy-draft')) { console.log('FAIL 复制草稿按钮缺失'); process.exit(1); }
    if (!out.includes('result-card warning')) { console.log('FAIL 警示卡缺失'); process.exit(1); }
    // 警示只应有一张卡（原来三条警示各占一张黄卡 → 现在合成一张清单）
    const alertCards = (out.match(/<div class="result-card warning">/g) || []).length;
    if (alertCards !== 1) { console.log('FAIL 警示卡数量='+alertCards+'，应合并为一张'); process.exit(1); }
    console.log('OK');
  },
  e => { console.log('FAIL generateDraft: ' + e.message); process.exit(1); }
);

// —— 方案模块入口的「点一下填进输入框」（B2B 评审 P2：多数模块原先没有入口）——
// 断言的是**行为**，不是「能跑」：空输入就填、已有别的内容就追加、同名的**不重复堆**。
const box={value:''};
if(R.fillProposalQuery('售后方案',box)!=='售后方案'){console.log('FAIL 空输入未填入');process.exit(1);}
R.fillProposalQuery('应急预案',box);
if(box.value!=='售后方案，应急预案'){console.log('FAIL 追加结果异常: '+box.value);process.exit(1);}
R.fillProposalQuery('应急预案',box);
if(box.value!=='售后方案，应急预案'){console.log('FAIL 同名模块被重复堆: '+box.value);process.exit(1);}
// 已有自由文本时**不得覆盖**（用户写了半天的条件不能被一次点击吃掉）
const keep={value:'售后服务方案，必须包含服务周期'};
R.fillProposalQuery('培训方案',keep);
if(!keep.value.includes('售后')||!keep.value.includes('培训方案')){
  console.log('FAIL 覆盖了用户已有输入: '+keep.value);process.exit(1);}
// 词表入口：/api/modules 的桩返回 `modules:{}`（非数组）时也**不得抛错**
// —— 跨语言一致性：条件标签拼出的查询，后端必须能解析回去 ——
// 只回填**单个**条件会让用户"点完标签就查"时报 400（缺产品/金额）——那是给用户挖坑。
// 所以回填的是**拼接后的完整查询**；这里把它交给 Python 侧喂给 app.api.parse_demo_query 核对。
const tagCases = [
  {product:'代谢组', minimum_amount:20000, date_from:'2024-12'},
  {product:'蛋白组', minimum_amount:26400, date_to:'2025-06'},
  {product:'单细胞', minimum_amount:10000, party_include:['欧易']},
  {product:'多组学', minimum_amount:10000, product_exclude:['宏基因组']},
  {product:'脂质组', minimum_amount:0, party_exclude:['鹿明']},
];
console.log('__QUERIES__' + JSON.stringify(tagCases.map(c => R.conditionQuery(c))));

Promise.resolve(R.loadProposalPicker()).then(()=>console.log('OK'),
  e=>{console.log('FAIL loadProposalPicker: '+e.message);process.exit(1);});
"""
    import json as _json
    probe.write_text(probe_src.replace("__REAL_IDS__", _json.dumps(sorted(from_html))),
                     encoding="utf-8")
    r = subprocess.run(["node", str(probe), str(APP_JS)],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, f"渲染冒烟失败：{r.stdout}\n{r.stderr}"
    assert "OK" in r.stdout

    # —— 把前端拼出的查询喂给**后端**解析（跨语言一致性，见探针里的说明）——
    from app.api import parse_demo_query, parse_scope_conditions
    lines = [l for l in r.stdout.splitlines() if l.startswith("__QUERIES__")]
    assert lines, f"探针未输出 __QUERIES__；stdout={r.stdout!r}"
    queries = _json.loads(lines[0][len("__QUERIES__"):])
    assert len(queries) == 5, queries
    for q in queries:      # ① 不抛错：点了标签再查不会 400
        try:
            parse_demo_query(q)
        except ValueError as exc:
            raise AssertionError(
                f"前端拼出的查询后端解析不了 → 用户点了标签再查会 400：{q!r}（{exc}）")
    # ② 关键字段逐一核对（不只是"不抛错"）
    assert parse_demo_query(queries[0])[0] == "代谢组"
    assert parse_demo_query(queries[0])[2] == 20000
    assert parse_demo_query(queries[0])[3] == "2024-12"
    assert parse_demo_query(queries[1])[2] == 26400 and parse_demo_query(queries[1])[4] == "2025-06"
    assert "欧易" in parse_scope_conditions(queries[2])[0]        # 机构包含
    assert "宏基因组" in parse_scope_conditions(queries[3])[2]     # 产品排除
    assert "鹿明" in parse_scope_conditions(queries[4])[1]        # 机构排除
