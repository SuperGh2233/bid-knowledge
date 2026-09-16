"""scripts/refresh_catalog.py 单测：增量登记 / 幂等 / 过滤 / 红线（独立临时库 + 临时根）。

模式与 tests/test_ledger_sync.py 一致：独立临时库 + 手工搭目录树。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

import scripts.refresh_catalog as rc  # noqa: E402
from app.db import deterministic_document_id  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path):
    """独立临时库（rc 全链路真实写入，row_factory=Row 便于按列名断言）。"""
    path = tmp_path / "refresh.db"
    con = rc._connect(path, init_allowed=True)
    yield con
    con.close()


def _write(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def sample_roots(tmp_path):
    """手工搭目录树（对齐真实形态：项目前缀目录、允许嵌套、含非标书/系统文件）。"""
    r2025 = tmp_path / "2025年"
    r2026 = tmp_path / "2026年"
    _write(r2025, "2025A-标书-甲-XX项目/响应文件.DOCX", "a")
    _write(r2026, "2026B-标书-乙-YY项目/子目录/资质.PDF", "b")
    _write(r2026, "2026C-非标书-丙-ZZ调研/调研.xlsx", "c")
    _write(r2026, "2026C-非标书-丙-ZZ调研/报价.docx", "d")
    _write(r2026, "2026D-标书-丁-WW项目/~$缓存.docx", "")
    _write(r2026, "2026D-标书-丁-WW项目/Thumbs.db", "")
    return [rc.SourceRoot("2025年", r2025), rc.SourceRoot("2026年", r2026)]


def _rows_by_rel(con):
    return {r["relative_path"]: r for r in con.execute("SELECT * FROM documents")}


def test_first_run_registers_new_files(tmp_db, sample_roots):
    """新文件全部登记，字段值与现库口径一致。"""
    out = rc.refresh_catalog(tmp_db, sample_roots)
    # 树上共 6 个文件：非标书跳过 2、系统跳过 2、可登记 2
    assert (out["scanned"], out["inserted"], out["skipped_feibiao"], out["skipped_system"]) \
        == (2, 2, 2, 2), out
    rows = _rows_by_rel(tmp_db)
    assert len(rows) == 2
    r = rows["2025A-标书-甲-XX项目/响应文件.DOCX"]
    assert r["source_root_id"] == "2025年"
    assert r["project_folder"] == "2025A-标书-甲-XX项目"  # project_folder == 一级目录
    assert r["file_ext"] == ".docx"                        # 小写带前导点
    assert r["file_size"] == 1
    assert r["file_mtime"] is not None
    assert r["document_role"] == "our_response"           # 响应文件 → 我方响应
    assert r["role_source"] == "rule"
    assert r["parse_status"] is None and r["sha256"] is None
    assert r["document_id"] == deterministic_document_id(
        "2025年", "2025A-标书-甲-XX项目/响应文件.DOCX")
    # 嵌套文件 project_folder 也取一级目录名
    r2 = rows["2026B-标书-乙-YY项目/子目录/资质.PDF"]
    assert r2["project_folder"] == "2026B-标书-乙-YY项目"
    assert r2["document_role"] == "qualification_evidence"


def test_idempotent_rerun(tmp_db, sample_roots):
    """重复跑：新增 0，行数与字段逐项不变。"""
    rc.refresh_catalog(tmp_db, sample_roots)
    before = [tuple(r) for r in tmp_db.execute("SELECT * FROM documents ORDER BY 1")]
    out = rc.refresh_catalog(tmp_db, sample_roots)
    assert out["inserted"] == 0, out
    after = [tuple(r) for r in tmp_db.execute("SELECT * FROM documents ORDER BY 1")]
    assert before == after


def test_feibiao_dirs_skipped(tmp_db, sample_roots):
    """非标书目录整目录跳过：计数准确、库 0 行。"""
    out = rc.refresh_catalog(tmp_db, sample_roots)
    assert out["skipped_feibiao"] == 2          # 调研.xlsx + 报价.docx
    n = tmp_db.execute(
        "SELECT COUNT(*) FROM documents WHERE project_folder LIKE '%非标书%'").fetchone()[0]
    assert n == 0


def test_system_files_skipped_but_existing_kept(tmp_db, sample_roots):
    """~$ / Thumbs.db 跳过；预置的既有 system 行不被删。"""
    tmp_db.execute(
        "INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
        " document_role, role_source) VALUES (?,?,?,?,?,?)",
        (deterministic_document_id("2025年", "2025A-标书-甲-XX项目/旧系统.exe"),
         "2025年", "2025A-标书-甲-XX项目", "2025A-标书-甲-XX项目/旧系统.exe",
         "system_or_temp", "rule"))
    tmp_db.commit()
    out = rc.refresh_catalog(tmp_db, sample_roots)
    assert out["skipped_system"] == 2           # ~$缓存.docx + Thumbs.db
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM documents WHERE document_role='system_or_temp'").fetchone()[0] == 1
    assert tmp_db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3  # 预置 + 2 新


def test_dry_run_writes_nothing(tmp_db, sample_roots):
    """--dry-run 不写库；返回 inserted 为应有值。"""
    out = rc.refresh_catalog(tmp_db, sample_roots, dry_run=True)
    assert out["inserted"] == 2, out
    assert tmp_db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_unreachable_root_aborts_whole_run(tmp_db, sample_roots):
    """任一根不可访问 → 整轮中止，另一个有效根的新文件也不写入。"""
    bad = list(sample_roots) + [rc.SourceRoot("2027年", Path("Z:/__不存在的根__/nope"))]
    with pytest.raises(rc._AbortError):
        rc.refresh_catalog(tmp_db, bad)
    assert tmp_db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_with_meta_only_updates_changed(tmp_db, sample_roots):
    """--with-meta：size/mtime 变化才 UPDATE；默认不动存量。"""
    rc.refresh_catalog(tmp_db, sample_roots)
    key = ("2025年", "2025A-标书-甲-XX项目/响应文件.DOCX")
    con = tmp_db
    # 改文件内容（size 变）后：
    f = sample_roots[0].path / "2025A-标书-甲-XX项目/响应文件.DOCX"
    f.write_text("longer-text", encoding="utf-8")
    # 不带 --with-meta → 不动
    out = rc.refresh_catalog(tmp_db, sample_roots, with_meta=False)
    assert out["updated_meta"] == 0, out
    sz = con.execute("SELECT file_size FROM documents WHERE source_root_id=? AND relative_path=?",
                     key).fetchone()[0]
    assert sz == 1
    # 带 --with-meta → updated_meta=1，size 更新
    out = rc.refresh_catalog(tmp_db, sample_roots, with_meta=True)
    assert out["updated_meta"] == 1, out
    sz = con.execute("SELECT file_size FROM documents WHERE source_root_id=? AND relative_path=?",
                     key).fetchone()[0]
    assert sz == len("longer-text")


def test_limit_deterministic(tmp_db, sample_roots):
    """--limit 确定性截断：先按 key 排序再取前 N。"""
    out = rc.refresh_catalog(tmp_db, sample_roots, limit=1)
    assert out["inserted"] == 1 and out["skipped_limit"] == 1, out
    rows = tmp_db.execute("SELECT source_root_id, relative_path FROM documents").fetchall()
    assert len(rows) == 1
    # key 排序最小的是 2025年 那条
    assert rows[0][0] == "2025年"
    assert rows[0][1] == "2025A-标书-甲-XX项目/响应文件.DOCX"


def test_prod_db_refused(tmp_path):
    """正式库路径 → 拒绝（exit 2）。"""
    prod = Path(__file__).resolve().parents[1] / "bid_ai_clean.db"
    assert rc.resolve_db_path(str(prod)).resolve() == rc.FORBIDDEN_PROD_DB
    code = rc.main(["--db", str(prod), "--dry-run"])
    assert code == rc.EXIT_ABORT