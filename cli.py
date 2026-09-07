"""bid-ai-clean 最小入口与健康自检。

R1 阶段仅：建空 SQLite 库 / 空 ES 方案索引映射 / 连通自检。
不扫描 NAS、不解析文件、不 OCR、不迁移旧业务数据（R1 红线）。
"""
from __future__ import annotations

import sys


def health() -> dict:
    """SQLite 四表存在 + ES 可连通 + 空方案索引映射就绪。"""
    import app.db as db

    db_result = db.health()
    report: dict = {"sqlite": db_result, "elasticsearch": None}

    from elasticsearch import Elasticsearch
    import app.config as config
    import app.index as idx

    es = Elasticsearch(config.ES_URL, request_timeout=30)
    try:
        info = es.info()
    except Exception as exc:  # noqa: BLE001
        report["elasticsearch"] = {"ok": False, "error": str(exc)[:160]}
        return report

    index = idx.ensure_index(es=es)  # R1: 只建空映射
    report["elasticsearch"] = {"ok": True, "version": info.get("version", {}).get("number"),
                               "index": index}
    return report


def main() -> int:
    h = health()
    ok = bool(h["sqlite"].get("ok") and h["elasticsearch"] and h["elasticsearch"].get("ok"))
    print(f"db_ok={h['sqlite'].get('ok')} tables={h['sqlite'].get('tables')}")
    print(f"es_ok={bool(h['elasticsearch'] and h['elasticsearch'].get('ok'))} "
          f"{h['elasticsearch'] and h['elasticsearch']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())