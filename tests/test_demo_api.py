from app.api import parse_demo_query, unsupported_condition


def test_parse_demo_query():
    assert parse_demo_query("查找2万元以上的代谢组合同")[:1] == ("代谢组",)
    assert parse_demo_query("5万以上的10x单细胞合同")[2] == 50_000
    assert parse_demo_query("蛋白组1000元以上")[2] == 1_000


def test_parse_demo_query_rejects_unsupported_bounds():
    # 第三种用**真正不在别名表里**的词。2026-09-13 之前这里写的是「转录组」——
    # 那时它不在 `PRODUCT_ALIASES`，是「未知产品」的合适例子；用户确认补入后它成了已知产品，
    # 故换成始终不在表里的词。**测试意图未变**：未支持的产品必须被拒，不能静默放行。
    for query in ("代谢组合同", "2万元以下代谢组合同", "2万元以上实验室装修合同"):
        try:
            parse_demo_query(query)
        except ValueError:
            pass
        else:
            raise AssertionError(query)


def test_unsupported_condition_detection():
    cases = {
        "找乙方为欧易的代谢组合同，2万元以上": "供应商／竞品范围条件",
        "找含2025年12月社保的代谢组资料，2万元以上": "财务／社保条件",
        "找高分辨质谱仪器的合同，2万元以上": "仪器／设备条件",
        "找带发票和照片的合同，2万元以上": "发票／照片材料条件",
        "找近三年的代谢组合同，2万元以上": "时间／日期条件",
    }
    for query, expected in cases.items():
        assert unsupported_condition(query) == expected, query


def test_date_condition_is_parsed_not_rejected():
    """日期条件现在**支持**：解析成 YYYY-MM / YYYY-MM-DD 区间。"""
    _, _, _, dfrom, dto = parse_demo_query("找2024年12月以后签的代谢组合同")
    assert dfrom == "2024-12" and dto is None
    _, _, _, dfrom, dto = parse_demo_query("找2025年6月以前的单细胞合同")
    assert dfrom is None and dto == "2025-06"
    # 带日期的精确表达
    _, _, _, dfrom, _ = parse_demo_query("找2024年12月1日以后的蛋白组合同")
    assert dfrom == "2024-12-01"
    # 日期 + 金额 同时给出
    _, _, amt, dfrom, _ = parse_demo_query("找2万元以上的代谢组合同，2024年12月以后")
    assert amt == 20_000 and dfrom == "2024-12"


def test_date_condition_without_direction_is_rejected_not_dropped():
    """有年月但没说「之后/以前」→ 语义不明，必须拒绝而非丢弃该条件。"""
    try:
        parse_demo_query("找2024年12月的代谢组合同")
    except ValueError as exc:
        assert "以后" in str(exc) or "以前" in str(exc)
    else:
        raise AssertionError("无方向的日期条件被静默放行")


def test_supported_queries_have_no_unsupported_condition():
    """支持范围内的查询不得被误判为不支持条件。"""
    for query in ("查找2万元以上的代谢组合同", "查找15万元以上的代谢组合同",
                  "查找5万元以上的单细胞合同", "查找1000元以上的蛋白组合同",
                  "找2024年12月以后签的代谢组合同"):
        assert unsupported_condition(query) is None, query


def test_negative_filter_with_known_product_is_now_supported():
    """负向过滤**已实现**：能解析出具体产品名的排除条件现在被执行（不再是拒绝）。

    行为变更（2026-09-11）：`排除/不要 + 已知产品名` → 真正过滤掉该产品类别，
    并在响应的 `filter_note` 里如实说明做了什么。
    """
    from app.api import parse_scope_conditions
    _, _, products, unresolved = parse_scope_conditions("找2万元以上的代谢组合同，排除单细胞")
    assert products == ("单细胞",) and unresolved == ()
    parse_demo_query("找2万元以上的代谢组合同，排除单细胞")     # 不再抛错


def test_negative_filter_with_unknown_term_is_still_rejected():
    """**归不了类的词一律拒绝** —— 绝不"执行一半、丢掉一半"。

    ⚠️ 2026-09-13：本例原先用 `宏基因组` 当「不在支持表里」的词。用户确认补入该产品后，
    它已能被归类，故换成**始终不在表里**的词（`土壤检测`）。**测试意图未变**：
    只要排除项里有一个词归不了类，整条查询就必须被拒、并指出是哪个词。
    """
    from app.api import parse_scope_conditions
    _, _, products, unresolved = parse_scope_conditions("找2万元以上的代谢组合同，不要蛋白组和土壤检测")
    assert products == ("蛋白组",)
    assert unresolved == ("土壤检测",)
    try:
        parse_demo_query("找2万元以上的代谢组合同，不要蛋白组和土壤检测")
    except ValueError as exc:
        assert "土壤检测" in str(exc)
    else:
        raise AssertionError("含无法归类词的排除条件被静默放行")


def test_known_product_exclusions_are_now_accepted():
    """补入的产品（宏基因组等）现在**可归入排除项**，不再被当成未知词拒绝。

    这是 2026-09-13 用户确认补别名后的**正向**验证 —— 与上面那条「未知词仍拒绝」配对：
    补齐的是**已知产品**，不是把「拒绝未知词」这条规则放松了。
    """
    from app.api import parse_scope_conditions
    _, _, products, unresolved = parse_scope_conditions("找2万元以上的代谢组合同，不要蛋白组和宏基因组")
    assert set(products) == {"蛋白组", "宏基因组"}, products
    assert unresolved == ()
    parse_demo_query("找2万元以上的代谢组合同，不要蛋白组和宏基因组")   # 不再抛错


def test_vendor_include_and_exclude_are_parsed():
    """「乙方是X」→ 机构包含；「排除Y」+机构 → 机构排除。简称与全称都归一到简称键。"""
    from app.api import parse_scope_conditions, scope_filter_note
    inc, exc, prod, un = parse_scope_conditions("找乙方是欧易或鹿明的2万元以上代谢组合同")
    assert inc == ("欧易", "鹿明") and exc == () and un == ()
    inc, exc, prod, un = parse_scope_conditions("找2万元以上的代谢组合同，排除华大和吉凯的")
    assert inc == () and exc == ("华大", "吉凯") and un == ()
    # 「乙方是代谢组」不成句：乙方必须是一家公司
    _, _, _, un = parse_scope_conditions("找乙方是代谢组的合同")
    assert un
    # 说明文案必须点破「排除公司 ≠ 排除竞品响应」
    note = scope_filter_note((), ("华大",), ())
    assert "排除甲乙方" in note and "竞品" in note


def test_scope_label_is_never_cleared_without_a_parsed_object():
    """**回归（对抗性复核发现，本轮引入）**：范围条件的词表是解析器的**超集**。

    只命中词表、却解析不出对象的写法，**绝不能**清掉「不支持」标签静默放行 ——
    它们会被当成"条件已满足"而实际按无条件返回。
    """
    for query in ("找2万元以上的代谢组合同，除了欧易以外",
                  "找2万元以上的代谢组合同，欧易以外的",
                  "找2万元以上的代谢组合同，乙方不是欧易"):
        # ⚠️ 2026-09-15（R1-4）**移出本用例**的两条：`找华大的2万元以上代谢组合同`、
        # `找2万元以上的代谢组合同，甲方是华大`、`…，乙方：欧易` —— 它们现在**能解析出对象**
        # （裸厂商名 → 「乙方包含」），不再属于「只命中词表、解析不出对象」的拒绝类。
        # **仍然拒绝**的这三条：否定/排除语境里厂商名解析不出对象，必须如实拒绝、不得静默放行。
        try:
            parse_demo_query(query)
        except ValueError:
            pass
        else:
            raise AssertionError(f"范围条件被静默丢弃：{query}")


def test_bare_vendor_name_is_a_supported_scope_condition():
    """裸厂商名 → 「乙方包含」条件（R1-4，2026-09-15 业务反馈）。

    由来：需求方问 `华大转录组 30w 合同`（业务最自然的「谁做的 + 什么产品 + 多少钱」）被**一律拒绝** ——
    旧实现把 `华大|吉凯|诺禾` 直接列进「供应商／竞品范围条件」的拦截正则。
    现改为识别为乙方包含条件；**排除语境里的厂商仍走排除**，不得被当包含。
    """
    from app.api import parse_scope_conditions
    # 无排除语境 → 包含
    inc, exc_org, exc_prod, _ = parse_scope_conditions("华大转录组30万以上的合同")
    assert inc == ("华大",) and exc_org == () and exc_prod == ()
    # 有排除语境 → 归排除，**不得**同时进包含
    inc2, exc_org2, _, _ = parse_scope_conditions("不要华大的转录组合同，2万元以上")
    assert inc2 == () and exc_org2 == ("华大",)
    # ⚠️ 否定词在厂商名**后面**（`欧易以外的`）同样不得被当成包含 —— 实测漏判过一次
    inc4, _, _, _ = parse_scope_conditions("找2万元以上的代谢组合同，欧易以外的")
    assert "欧易" not in inc4
    # 同时命中长短两个名字（`华大`/`华大基因`）→ 取短的（与 `_org_key` 同口径）
    inc3, _, _, _ = parse_scope_conditions("华大基因的转录组合同")
    assert inc3 == ("华大",)


def test_other_unsupported_condition_is_not_dropped_alongside_scope():
    """同一句里叠着**别的不支持条件**时，不得只放行范围条件那半句、把后面的丢掉。"""
    for query in ("找2万元以上的代谢组合同，排除蛋白组，还要有发票",
                  "找2万元以上的代谢组合同，不要蛋白组，找写过售后方案的"):
        try:
            parse_demo_query(query)
        except ValueError as exc:
            assert "发票" in str(exc) or "方案主题" in str(exc), query
        else:
            raise AssertionError(f"同句的其它条件被静默丢弃：{query}")


def test_split_fragment_carrying_alias_is_not_a_product():
    """`不要X和Y的Z` 按「和」切出的后半片含产品别名时，**不得**当成一个产品。

    对抗性复核实测：`不要蛋白组和宏基因组的2万元以上代谢组合同` 曾被解析为
    exc_prod=('蛋白组','代谢组') —— 用户没点名的「代谢组」被凭空排除，而真正要排的
    「宏基因组」既未排除也未举报。
    """
    from app.api import parse_scope_conditions
    _, _, prod, unresolved = parse_scope_conditions(
        "不要蛋白组和宏基因组的2万元以上代谢组合同")
    assert "代谢组" not in prod
    assert unresolved            # 宏基因组归不了类 → 必须举报


def test_service_name_matching_without_breaking_d9(monkeypatch, tmp_path):
    """类别通用时，**按服务名**也要能命中；且金额只能是**命中明细行的子集和**。

    背景（金标准实测）：98 份 CTL 里 30 份的类别不含任何产品词（如 `多组学检测（非范本合同）`），
    只匹配类别会让这些合同**任何产品查询都查不到**，尽管服务名里有 `LC-MS/MS 精准靶向代谢`。
    修法是让 `product_raw`（＝`类别/服务名`）**两段都参与匹配**，实测金标准 Recall 73.1% → 80.8%。

    ⚠️ **D9 红线**：金额必须是**命中明细行的子集和**，绝不能按类别把整类加总
    —— 那会等于合同总额、且混着非该产品的服务，正是 D9 禁止的「用合同总额替代产品金额」。
    """
    import app.config as app_config
    import app.db as db
    from app.search import locate_by_product_amount

    monkeypatch.setattr(app_config, "DB_PATH", str(tmp_path / "t2.db"))
    db.init_db(force=True)
    con = db.connect()
    # 类别通用（不含产品词），但服务名里有代谢；另有一行**非**代谢服务（必须不计入）
    table = ("服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
             "多组学检测（非范本合同） | 1 | LC-MS/MS 精准靶向代谢-短链脂肪酸 | 样本 | 100 | 1250.00 | 125000.00\n"
             "多组学检测（非范本合同） | 2 | 4D-DIA 蛋白组检测 | 样本 | 10 | 2520.00 | 25200.00\n"
             "多组学检测（非范本合同） | 3 | 有参转录组测序 | 样本 | 1 | 10800.00 | 10800.00\n"
             "合同总金额（元） | 合同总金额（元） | 合同总金额（元） | 161000.00\n")
    sha = "sha-svc"
    with con:
        con.execute("INSERT INTO documents (document_id, source_root_id, project_folder,"
                    " relative_path, document_role, parse_status, doc_subtype,"
                    " canonical_document_id) VALUES (?,?,?,?,?,?,?,?)",
                    ("d9x", "2026年", "P", "P/多组学.pdf", "final_signed", "done", None, sha))
        con.execute("INSERT INTO parse_artifacts (canonical_document_id, sha256, text,"
                    " parser_version) VALUES (?,?,?,?)", (sha, sha, table, "0.1.0"))
        con.execute("INSERT INTO contracts (contract_id, document_id, party_a, party_b,"
                    " source_sha256, parser_version) VALUES (?,?,?,?,?,?)",
                    ("CTL-d9x", "d9x", "某医院", "上海欧易生物医学科技有限公司", sha, "0.1.0"))
        for i, (cat, amt) in enumerate((("LC-MS/MS 精准靶向代谢-短链脂肪酸", 125000.0),
                                        ("4D-DIA 蛋白组检测", 25200.0),
                                        ("有参转录组测序", 10800.0))):
            con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw,"
                        " row_type, line_amount) VALUES (?,?,?,?,?)",
                        (f"d9x-{i}", "CTL-d9x",
                         f"多组学检测（非范本合同）/{cat}", "detail", amt))

    def run(kws, mn):
        rs = locate_by_product_amount(con, kws, mn, roots={}, approved_document_ids={"d9x"})
        return [(r.product, r.amount, r.hit) for r in rs if r.hit]

    meta = ("代谢", "代谢组", "全谱代谢", "LC-MS", "LC-MS/MS", "双平台代谢")
    got = run(meta, 1000)
    assert got, "类别通用合同按服务名应能命中"
    product, amount, hit = got[0]
    assert hit and amount == 125000.0, f"金额应为命中明细行的子集和 125000，实得 {amount}"
    assert amount != 161000.0, "**D9 红线**：不得把整类明细加总（那等于合同总额）"
    # 同一份合同对**另一个产品**的查询也要各自给出**自己的子集和**（25200，同样不是 161000）
    prot = run(("蛋白", "蛋白组", "蛋白质组", "DIA"), 1000)
    assert prot and prot[0][1] == 25200.0, f"蛋白组应命中并给出自己的子集和 25200，实得 {prot}"
    assert prot[0][1] != amount, "两个产品的金额必须各自独立，不能是同一个整类合计"
    con.close()


def test_derived_amount_is_always_labelled(monkeypatch, tmp_path):
    """**推算金额必须一路带到接口层并带标记** —— 不得与文档明写的金额长得一样。

    这是「金额推导开关」能安全存在的前提：打开 `CONTRACT_DERIVE_AMOUNT` 后，
    用户看到的必须是「¥64,000（由数量×单价推算）」，而不是一个冒充已确认值的数字。
    否则开开关就等于**悄悄**把「未确认」改写成「已确认」——那正是计划 §2 要防的。
    """
    import app.config as app_config
    import app.db as db
    from app.search import locate_by_product_amount

    monkeypatch.setattr(app_config, "DB_PATH", str(tmp_path / "t3.db"))
    db.init_db(force=True)
    con = db.connect()
    table = ("序号 | 测试项目 | 单价 | 数量 | 单位\n"
             "3 | Level One 500 全谱代谢组 | 320 | 200 | 样\n")
    sha = "sha-dv"
    with con:
        con.execute("INSERT INTO documents (document_id, source_root_id, project_folder,"
                    " relative_path, document_role, parse_status, doc_subtype,"
                    " canonical_document_id) VALUES (?,?,?,?,?,?,?,?)",
                    ("ddv", "2026年", "P", "P/多组学.pdf", "final_signed", "done", None, sha))
        con.execute("INSERT INTO parse_artifacts (canonical_document_id, sha256, text,"
                    " parser_version) VALUES (?,?,?,?)", (sha, sha, table, "0.1.0"))
        con.execute("INSERT INTO contracts (contract_id, document_id, party_a, party_b,"
                    " source_sha256, parser_version) VALUES (?,?,?,?,?,?)",
                    ("CTL-ddv", "ddv", "某医院", "上海欧易生物医学科技有限公司", sha, "0.1.0"))
        con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw,"
                    " row_type, line_amount) VALUES (?,?,?,?,?)",
                    ("ddv-0", "CTL-ddv", "Level One 500 全谱代谢组", "detail", None))
    meta = ("代谢", "代谢组", "全谱代谢", "LC-MS", "LC-MS/MS", "双平台代谢")

    # 开关关闭（默认）：金额未知，**没有**命中，也就没有金额来源
    monkeypatch.setattr(app_config, "CONTRACT_DERIVE_AMOUNT", False)
    assert [r for r in locate_by_product_amount(con, meta, 1, roots={},
                                                approved_document_ids={"ddv"}) if r.hit] == []

    # 开关开启：推算出金额，且**必须标为 derived**
    monkeypatch.setattr(app_config, "CONTRACT_DERIVE_AMOUNT", True)
    hits = [r for r in locate_by_product_amount(con, meta, 1, roots={},
                                                approved_document_ids={"ddv"}) if r.hit]
    assert len(hits) == 1
    assert hits[0].amount == 64000.0
    assert hits[0].amount_source == "derived_qty_x_price", "推算金额未被标记"
    con.close()


def test_scope_filters_actually_filter_results(monkeypatch, tmp_path):
    """**证明过滤真的生效**（而不只是被解析出来）。

    用内存库构造三份合同：华大客户 / 吉凯客户 / 其他客户，且各含代谢与单细胞明细。
    """
    import app.config as app_config
    import app.db as db
    from app.search import locate_by_product_amount

    monkeypatch.setattr(app_config, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db(force=True)
    con = db.connect()
    table = ("服务类别 | 序号 | 服务名称 | 单位 | 数量 | 单价（元） | 金额（元）\n"
             "代谢组 | 1 | 全谱代谢组检测 | 样本 | 10 | 5000.00 | 50000.00\n"
             "单细胞 | 2 | 单细胞转录组 | 样本 | 5 | 6000.00 | 30000.00\n")
    docs = [("d1", "华大客户"), ("d2", "吉凯客户"), ("d3", "其他客户")]
    with con:
        for did, party_a in docs:
            sha = f"sha-{did}"
            con.execute("INSERT INTO documents (document_id, source_root_id, project_folder,"
                        " relative_path, document_role, parse_status, doc_subtype,"
                        " canonical_document_id) VALUES (?,?,?,?,?,?,?,?)",
                        (did, "2026年", "P", f"P/{did}.docx", "final_signed", "done", None, sha))
            con.execute("INSERT INTO parse_artifacts (canonical_document_id, sha256, text,"
                        " parser_version) VALUES (?,?,?,?)", (sha, sha, table, "0.1.0"))
            con.execute("INSERT INTO contracts (contract_id, document_id, party_a, party_b,"
                        " source_sha256, parser_version) VALUES (?,?,?,?,?,?)",
                        (f"CTL-{did}", did, party_a, "上海欧易生物医学科技有限公司", sha, "0.1.0"))
            for i, (cat, amt) in enumerate((("代谢组", 50000.0), ("单细胞", 30000.0))):
                con.execute("INSERT INTO contract_items (item_id, contract_id, product_raw,"
                            " row_type, line_amount) VALUES (?,?,?,?,?)",
                            (f"{did}-{i}", f"CTL-{did}", f"{cat}/明细", "detail", amt))
    approved = {"d1", "d2", "d3"}
    kw = ("代谢", "单细胞")
    kw_meta = ("代谢",)

    def cats(**filters):
        rs = locate_by_product_amount(con, kw, 1000, roots={}, approved_document_ids=approved,
                                      **filters)
        return sorted((r.document_id, r.product) for r in rs if r.hit)

    base = cats()
    assert [c[0] for c in base if c[1] == "代谢组"] == ["d1", "d2", "d3"]
    # 排除机构：华大客户整份合同消失
    assert {c[0] for c in cats(party_exclude=("华大",))} == {"d2", "d3"}
    # 机构包含：只看欧易是乙方的 → 三份都保留；只看华大 → 只剩 d1
    assert {c[0] for c in cats(party_include=("欧易",))} == {"d1", "d2", "d3"}
    assert {c[0] for c in cats(party_include=("华大",))} == {"d1"}
    # 产品排除是**合同级**：这三份合同都同时含代谢与单细胞明细，
    # 所以「排除单细胞」会把它们**整份**排除（不是只去掉单细胞那一行）。
    # 口径依据：冻结查询 G05「代谢合同，不要蛋白组和宏基因组」在金标准里把
    # 这类「多组学」合同标为 irrelevant，即要求整份排除；行级口径下它们仍会被返回。
    assert cats(product_exclude=("单细胞",)) == []
    assert {c[1] for c in cats()} == {"代谢组", "单细胞"}   # 不排除时两种产品都在
    con.close()



# —— 2026-09-13 对抗审计后补的护栏 ——
# 审计实测：新增的 11 个产品**一条断言都没有**，把 PRODUCT_ALIASES 改成字母序测试也全绿。
# 而「首个命中即返回」的判据完全靠**字面书写顺序**，没有任何机制兜底 → 必须钉住。


def test_product_priority_narrow_before_broad():
    """**窄产品必须排在宽产品前面** —— 判据是「首个命中即返回」，顺序错了就静默串味。

    审计用 5000 次随机置换实测：这 6 条的翻转率 ≈50%，即完全靠书写顺序。
    """
    cases = {
        "空间代谢组": "空间代谢组",     # 否则被「代谢组」（别名含「代谢」）抢走
        "空间转录组": "空间转录组",     # 否则被「转录组」抢走
        "单细胞转录组": "单细胞",       # `10x Genomics 单细胞转录组测序` 是**单细胞**产品
        "单细胞ATAC": "ATAC",          # 否则被「单细胞」抢走
        "Olink蛋白质组": "Olink",      # 否则被「蛋白组」（别名含「蛋白」）抢走
        "脂质代谢组": "脂质组",         # 否则被「代谢组」抢走
        "宏基因组": "宏基因组",         # 不能落到「全基因组重测序」
        "全基因组重测序": "全基因组重测序",
        "真核有参转录组": "转录组",
    }
    for text, expected in cases.items():
        got = parse_demo_query(f"查找1万元以上的{text}合同")[0]
        assert got == expected, f"{text} → {got}，应为 {expected}"


def test_broad_alias_does_not_swallow_other_product_lines():
    """宽别名不得吞掉别的产品线 —— 审计实测「转录组」曾把 81.6% 的命中串成单细胞/空间转录组。

    这层由 `PRODUCT_MATCH_EXCLUDE` 在**明细行级**挡（见 `locate_by_product_amount` 的 `match_exclude`）。
    """
    from app.api import PRODUCT_MATCH_EXCLUDE
    assert PRODUCT_MATCH_EXCLUDE["转录组"] == ("单细胞", "空间")
    assert "非靶向" in PRODUCT_MATCH_EXCLUDE["靶向检测"]   # 「靶向」会命中**反义词**「非靶向」


def test_exclusion_list_is_not_hijacked_as_main_product():
    """排除清单里的产品**不得**被当成主产品 —— 否则语义变成「查X、又排除X」→ 恒返回 0 条。

    这是**早就存在**的缺陷（不是补别名引进的）：主产品判定曾扫整句。
    两种可达路径都要钉住：单子句、以及**逗号并列**的续项。
    """
    assert parse_demo_query("查找1万元以上的蛋白组合同，不要宏基因组")[0] == "蛋白组"
    assert parse_demo_query("查找2万元以上的单细胞合同，不要代谢组")[0] == "单细胞"
    assert parse_demo_query("找2万元以上的代谢组合同，不要蛋白组，宏基因组")[0] == "代谢组"
    # 并列的两个排除项都要真的被解析出来，不能只认第一个（静默丢掉第二个）
    from app.api import parse_scope_conditions
    _, _, prod_exc, unresolved = parse_scope_conditions(
        "找2万元以上的代谢组合同，不要蛋白组，宏基因组")
    assert set(prod_exc) == {"蛋白组", "宏基因组"} and unresolved == ()


def test_negation_words_we_cannot_parse_are_rejected_not_dropped():
    """解析器**不认**的否定词（不含/除了/以外）后面若是已知产品 → 必须拒绝，不能静默丢掉。

    实测：`不含蛋白组，不要宏基因组` 曾被放行且只排掉宏基因组（多返回 2 份含蛋白行的合同）。
    而 `不含税` 是**价格口径词**、不是排除材料，归不了类 → 不应被误报成排除条件。
    """
    from app.api import parse_scope_conditions
    _, _, prod_exc, unresolved = parse_scope_conditions("找2万元以上的代谢组合同，不含蛋白组，不要宏基因组")
    assert "蛋白组" in unresolved          # 抓不到的否定词要举报
    # 「不含税」不是排除材料（业务上说的是价格口径）→ 不该被当成排除项举报
    _, _, _exc, unres2 = parse_scope_conditions("找2万元以上的代谢组合同，不含税")
    assert "税" not in "".join(unres2)


def test_platform_aliases_fold_into_spatial_transcriptomics():
    """平台型号归属（2026-09-15 业务裁定，PLAN-20260915 §8-2）：

      · `Visium` / `Visium HD`（10x，测序型全转录组）→ **并入**「空间转录组」；
      · `Stereo-seq`（华大，测序型全转录组）→ **并入**；
      · `Xenium`（成像型、**靶向**，非全转录组）→ **单列**（不得被并入）；
      · `CytAssist` 是仪器不是产品线 → 不出现任何产品别名里。

    这些是**业务口径决定**，不是实现细节 —— 改动前须先改 PLAN 的 §8-2。
    """
    from app.api import PRODUCT_ALIASES
    # ⚠️ 用不带**供应商名**的写法：`华大 Stereo-seq …` 会被「供应商／竞品范围条件」守卫拒绝
    # （那是 R1-4 的问题，与别名无关；R1-4 修好后可加回该写法，见 PLAN-20260915 §R1-4）。
    for text in ("Visium HD 空间转录组", "10x Visium 空转", "Stereo-seq 空间转录组测序"):
        assert parse_demo_query(f"查找1万元以上的{text}合同")[0] == "空间转录组", text
    # Xenium 单列：不得落到「空间转录组」
    assert parse_demo_query("查找1万元以上的Xenium合同")[0] == "Xenium"
    assert "Xenium" not in PRODUCT_ALIASES["空间转录组"]
    # CytAssist 是仪器，不是产品线
    for aliases in PRODUCT_ALIASES.values():
        assert not any("CytAssist" in a for a in aliases)
