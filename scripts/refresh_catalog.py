"""增量登记刷新：把 Z 盘新增文件登记进 documents 台账（**只登记，不解析**）。

为什么存在：2026年 文件夹几乎每天新增项目目录/文件，本地台账不会自动跟进。
本脚本手动或定时（Windows 计划任务）跑一次，把 Z 盘上「现库没有的」文件登记进 documents。

口径（**延续现库既有一致行为，勿改**）：
  - 一级目录名含「非标书」→ 整目录跳过（现库 0 条这样的记录，实测）；
  - classifier 判 system_or_temp（thumbs.db / ~$xx / .db / .exe 等）→ 跳过；
  - 其余（标书 / 比选 / 调研 / 询价 / 报名 …）都登记，与现库 15 个无类型词目录一致。

红线（与父级 CLAUDE.md 一致）：
  - Z 盘**只读**：脚本只 os.walk + stat，绝不写 Z；
  - **只登记不解析**：不触 ES、不外发、不 OCR、不算 sha256（登记阶段留 NULL，内容去重留给 r5）；
  - **不删除任何行**：Z 上已消失的文件保留旧登记；改名目录按新路径重新登记、旧行保留（预期行为）；
  - **正式库 bid_ai_clean.db 禁写**：默认写 reg 演示库，显式 --db 指向正式库也拒绝（退出码 2）；
  - 任一根目录不可访问 → **整轮中止且一行不写**。

用法：
    python scripts/refresh_catalog.py --dry-run       # 先看将登记多少（推荐第一步）
    python scripts/refresh_catalog.py                  # 真实登记（幂等，重复跑新增 0）
    python scripts/refresh_catalog.py --limit 50       # 限 50 条新文件（确定性取自排序后的最前 key）

    可选 --root <ID>=<PATH> 覆盖默认根（可重复；不传用 2025年/2026年 两棵）。
    --db <路径> 指定库（默认 $BID_AI_CLEAN_DB，再默认 reg 演示库）；正式库禁写。
    --init-db  库文件不存在/缺表时建表（首次用）；否则报错中止。
    --with-meta 对已存在但 size/mtime 变化的行 UPDATE 元数据（默认不 update 存量）。

计划任务（由用户自建，本脚本不自动注册）：
    schtasks /Create /TN "bid-ai-clean-refresh" /SC DAILY /ST 09:00 ^
      /TR "C:\\Users\\hao.guo\\Desktop\\标书文库\\bid-ai-clean\\scripts\\refresh_catalog.bat" /F
    退出码：0=成功；2=根不可访问/禁写库/库缺表（计划任务可见失败）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app import db as db_mod  # noqa: E402
from app.classifier import SYSTEM_TEMP, classify_path  # noqa: E402

EXIT_OK = 0
EXIT_ABORT = 2
# 正式库禁写（红线硬校验）：无论 --db 怎么传，指向它就拒绝
FORBIDDEN_PROD_DB = (BASE / "bid_ai_clean.db").resolve()


class _AbortError(RuntimeError):
    """任一根不可访问 / 禁写库 / 缺表 —— 语义=整轮中止且不写一行。"""


@dataclass(frozen=True)
class SourceRoot:
    root_id: str  # 与库 source_root_id 等值（'2025年'/'2026年'）
    path: Path


DEFAULT_ROOTS = (
    SourceRoot("2025年", Path(r"Z:\01 投标项目文件\2025年")),
    SourceRoot("2026年", Path(r"Z:\01 投标项目文件\2026年")),
)


def resolve_roots(cli_roots: list[str] | None) -> list[SourceRoot]:
    """解析 --root <ID>=<PATH>；不传用 DEFAULT_ROOTS。任一项不可访问 → 中止。"""
    if not cli_roots:
        roots = list(DEFAULT_ROOTS)
    else:
        roots = []
        for item in cli_roots:
            if "=" not in item:
                raise _AbortError(f"--root 格式应为 <ID>=<PATH>，收到：{item!r}")
            rid, raw = item.split("=", 1)
            if not rid.strip():
                raise _AbortError(f"--root 的 ID 不能为空：{item!r}")
            roots.append(SourceRoot(rid.strip(), Path(raw)))
    errors = [f"{r.root_id} -> {r.path}" for r in roots
              if not r.path.exists() or not r.path.is_dir()]
    if errors:
        raise _AbortError("扫描根目录不可访问，已中止且不会写入：" + "；".join(errors))
    return roots


def scan_roots(roots: list[SourceRoot]) -> tuple[dict, dict[str, int]]:
    """遍历全部根，返回 ({(root_id, rel): meta}, 跳过计数)。收集阶段即过滤。

    过滤规则（与现库口径一致，见模块 docstring）：
      - 文件无一级目录（散在根下）→ skipped_loose；
      - project_folder 含「非标书」→ skipped_feibiao；
      - classifier 判 system_or_temp（.db/.exe/thumbs.db/~$…）→ skipped_system。
    meta 内预存 role（扫描时已判一次，写入不再重复调 classify_path）。
    """
    meta_by_key: dict[tuple[str, str], dict] = {}
    skipped = {"feibiao": 0, "system": 0, "loose": 0}
    # ⚠️ 存在性校验必须在遍历**之前**做：os.walk 对不存在的目录会**静默返回空**（不抛错），
    # 直接从 refresh_catalog 调本函数时，没有 main 的 resolve_roots 兜底就会"当作没有文件"放行。
    # 红线语义是「任一根不可访问 → 整轮中止且不写」，任何调用路径都必须成立。
    bad = [f"{r.root_id} -> {r.path}" for r in roots
           if not r.path.exists() or not r.path.is_dir()]
    if bad:
        raise _AbortError("扫描根目录不可访问，已中止且不会写入：" + "；".join(bad))
    for root in roots:
        try:
            for dirpath, _dirnames, filenames in os.walk(root.path, followlinks=False):
                for fn in filenames:
                    abs_path = Path(dirpath) / fn
                    rel = os.path.relpath(abs_path, root.path).replace("\\", "/")
                    parts = rel.split("/")
                    if len(parts) < 2:
                        skipped["loose"] += 1
                        continue  # 散在根下、无一级目录 —— 现库无此形态
                    project_folder = parts[0]
                    if "非标书" in project_folder:
                        skipped["feibiao"] += 1
                        continue
                    role = classify_path(rel).role
                    if role == SYSTEM_TEMP:
                        skipped["system"] += 1
                        continue
                    st = abs_path.stat()
                    meta_by_key[(root.root_id, rel)] = {
                        "root_id": root.root_id,
                        "relative_path": rel,
                        "project_folder": project_folder,
                        "file_size": st.st_size,
                        "file_mtime": st.st_mtime,
                        "file_ext": (abs_path.suffix or "").casefold(),
                        "role": role,
                    }
        except OSError as exc:
            raise _AbortError(f"扫描 {root.root_id}（{root.path}）失败，已中止：{exc}") from exc
    return meta_by_key, skipped


def _existing_keys(con: sqlite3.Connection, root_ids: list[str]) -> set[tuple[str, str]]:
    """只读：预读现有登记键供 diff。"""
    if not root_ids:
        return set()
    ph = ",".join("?" * len(root_ids))
    rows = con.execute(
        f"SELECT source_root_id, relative_path FROM documents WHERE source_root_id IN ({ph})",
        root_ids).fetchall()
    return {(str(r[0]), str(r[1])) for r in rows}


def _existing_meta(con: sqlite3.Connection, root_ids: list[str]) -> dict[tuple[str, str], tuple]:
    """只读：现有行的 (file_size, file_mtime) —— 供 --with-meta 比对（一次批量取，不逐行查）。"""
    if not root_ids:
        return {}
    ph = ",".join("?" * len(root_ids))
    rows = con.execute(
        f"SELECT source_root_id, relative_path, file_size, file_mtime "
        f"FROM documents WHERE source_root_id IN ({ph})", root_ids).fetchall()
    return {(str(r[0]), str(r[1])): (r[2], r[3]) for r in rows}


def _meta_changed(current: tuple, scan: dict) -> bool:
    """size/mtime 任一与库不符 → 需 UPDATE（只在 --with-meta 时触发）。"""
    size, mtime = current
    return scan["file_size"] != size or scan["file_mtime"] != mtime


def ensure_schema(con: sqlite3.Connection, *, init_allowed: bool) -> None:
    """缺 documents 表：init_allowed 才建；否则中止（不对陌生库自作主张写结构）。"""
    has = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()
    if has:
        return
    if not init_allowed:
        raise _AbortError("库没有 documents 表；首次请加 --init-db（或先跑 "
                          "'python -c \"from app.db import init_db; init_db()\"'）")
    for stmt in db_mod.SCHEMA.split(";"):
        if stmt.strip():
            con.execute(stmt)


def refresh_catalog(con: sqlite3.Connection, roots: list[SourceRoot], *,
                    dry_run: bool = False, limit: int = 0, with_meta: bool = False) -> dict:
    """核心入口：扫描 → diff → （单事务）登记。返回统计 dict。

    顺序保证：scan_roots 任一根失败即在写入前抛 _AbortError（整轮未写）→ 预读 existing（只读）
    → 新候选排序后截 limit → 非 dry_run 单事务 executemany，失败整体 rollback。
    """
    output = {"scanned": 0, "existing": 0, "updated_meta": 0, "inserted": 0,
              "skipped_feibiao": 0, "skipped_system": 0, "skipped_loose": 0,
              "skipped_limit": 0}

    scans, skipped = scan_roots(roots)
    output["scanned"] = len(scans)
    output["skipped_feibiao"] = skipped["feibiao"]
    output["skipped_system"] = skipped["system"]
    output["skipped_loose"] = skipped["loose"]

    root_ids = [r.root_id for r in roots]
    existing = _existing_keys(con, root_ids)
    output["existing"] = len(existing)

    # 存量元数据比对（--with-meta）：只对新键以外、且 size/mtime 有变动的行 UPDATE
    updates: list[tuple] = []
    if with_meta:
        cur = _existing_meta(con, root_ids)
        for key in sorted(existing & scans.keys()):
            if _meta_changed(cur.get(key), scans[key]):
                m = scans[key]
                updates.append((m["file_mtime"], m["file_size"], key[0], key[1]))
    output["updated_meta"] = len(updates)

    # 新候选：按 key 排序后截 limit（保证 --limit 复现确定性）
    new_keys = sorted(scans.keys() - existing)
    head = new_keys[: limit] if limit > 0 else new_keys
    output["skipped_limit"] = len(new_keys) - len(head)
    output["inserted"] = len(head)

    if dry_run:
        return output

    rows = [(db_mod.deterministic_document_id(rid, scans[(rid, rel)]["relative_path"]),
             rid,
             scans[(rid, rel)]["project_folder"],
             scans[(rid, rel)]["relative_path"],
             scans[(rid, rel)]["file_ext"],
             scans[(rid, rel)]["file_size"],
             scans[(rid, rel)]["file_mtime"],
             scans[(rid, rel)]["role"],
             "rule")
            for rid, rel in head]
    try:
        if rows:
            con.executemany(
                "INSERT INTO documents (document_id, source_root_id, project_folder, relative_path,"
                " file_ext, file_size, file_mtime, document_role, role_source)"
                " VALUES (?,?,?,?,?,?,?,?,?)", rows)
        for row in updates:
            con.execute("UPDATE documents SET file_mtime=?, file_size=? "
                        "WHERE source_root_id=? AND relative_path=?", row)
        con.commit()
    except sqlite3.Error as exc:
        con.rollback()
        raise _AbortError(f"写库失败，已整体回滚：{exc}") from exc
    return output


def _connect(db_path: Path, *, init_allowed: bool) -> sqlite3.Connection:
    """打开 SQLite 连接（Row 工厂，列名可读），缺 documents 表时按 init_allowed 决定建表或中止。"""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    ensure_schema(con, init_allowed=init_allowed)
    return con


def resolve_db_path(arg: str | None) -> Path:
    """优先级 --db > $BID_AI_CLEAN_DB > reg 演示库。绝不回落正式库。"""
    if arg:
        return Path(arg)
    if os.environ.get("BID_AI_CLEAN_DB"):
        return Path(os.environ["BID_AI_CLEAN_DB"])
    return BASE / "bid_ai_clean_reg.db"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="增量登记刷新：Z 盘新文件 → documents 台账（只登记不解析）")
    ap.add_argument("--db", help="SQLite 库路径（默认 $BID_AI_CLEAN_DB，再默认 reg 演示库；正式库禁写）")
    ap.add_argument("--dry-run", action="store_true", help="只报将登记多少，不写库")
    ap.add_argument("--limit", type=int, default=0, help="本次最多登记 N 条新文件（0=不限）")
    ap.add_argument("--with-meta", action="store_true", help="对已存在且 size/mtime 变化的行 UPDATE 元数据")
    ap.add_argument("--root", action="append", default=[], help="覆盖根：--root <ID>=<PATH>，可重复")
    ap.add_argument("--init-db", action="store_true", help="库缺 documents 表时建表（首次用）")
    args = ap.parse_args(argv)

    try:
        db_path = resolve_db_path(args.db)
        if db_path.resolve() == FORBIDDEN_PROD_DB:
            print(f"[中止] {db_path} 是正式库，禁写。请改用演示库（默认 reg）。", file=sys.stderr)
            return EXIT_ABORT
        if not db_path.exists() and not args.init_db:
            print(f"[中止] 库不存在：{db_path}（首次请加 --init-db）", file=sys.stderr)
            return EXIT_ABORT
        roots = resolve_roots(args.root or None)
        con = _connect(db_path, init_allowed=args.init_db)
        try:
            out = refresh_catalog(con, roots, dry_run=args.dry_run,
                                  limit=args.limit, with_meta=args.with_meta)
        finally:
            con.close()
    except _AbortError as exc:
        print(f"[中止] {exc}", file=sys.stderr)
        return EXIT_ABORT

    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}扫描 {out['scanned']} | 已存在 {out['existing']} | 新增 {out['inserted']}"
          f" | 更新元数据 {out['updated_meta']}"
          f" | 跳过:非标书目录 {out['skipped_feibiao']} / 系统文件 {out['skipped_system']}"
          f" / 根级散文件 {out['skipped_loose']} / 超limit {out['skipped_limit']}"
          f" | 库: {db_path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())