"""`/api/status` —— 只读预览的状态与范围。拆分自 `app/api.py`（2026-09-14 APIRouter 整理）。

共享辅助（`readonly_db` / `live_scope` / `_approved_contract_ids`）留在 `app.api` 顶层定义，
从本模块 `from app.api import ...` 引入 —— 这样 `tests/` 对 `app.api.DEMO_DB` /
`app.api.APPROVED_DOCUMENT_IDS` 的 monkeypatch 仍作用于同一个模块全局，测试语义不变。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api import readonly_db, live_scope

router = APIRouter()


@router.get("/api/status")
def status():
    import json

    with readonly_db() as con:
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("documents", "contracts", "contract_items", "parse_artifacts")
        }
        scope = live_scope(con)
    n = scope["queryable_contracts"]
    return {
        "mode": "只读预览",
        "readonly": True,
        "counts": counts,
        "scope": scope,
        "boundary": (f"合同定位覆盖 {n} 份已核合同；方案生成取材于已入库的我方响应文件。"
                     if n else "合同定位尚未纳入已核合同；方案生成取材于已入库的我方响应文件。"),
        "limits": f"合同定位覆盖 {n} 份已核合同",
    }