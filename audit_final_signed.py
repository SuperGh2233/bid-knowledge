"""R2 签章角色审计：只读扫描快照和登记库，不读 NAS 正文、不写数据库。

原登记 final_signed 与泛签章候选分别统计。自动规则满足条件仅记待复核，
不把分类器的预测当成人工真值，也不据此计算 Precision/Recall。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path, PureWindowsPath

from app import classifier

# 保留旧审计的候选口径，不能随分类器收紧一起缩小审计范围。
AUDIT_HINTS = ("最终版", "定稿版", "签章版", "盖章版", "正本扫描件", "正本", "定稿", "盖章")


def audit_records(inventory: dict, catalog: list[dict]) -> dict:
    roots = {PureWindowsPath(p).name: p for p in inventory["source_roots"]}
    if len(roots) != len(inventory["source_roots"]):
        raise ValueError("源目录标识不唯一，不能猜测完整路径")
    registered = {(r["source_root_id"], r["relative_path"].replace("\\", "/")): r for r in catalog}
    if len(registered) != len(catalog):
        raise ValueError("登记库存在重复源路径")
    counts = Counter()
    rows = []
    seen = set()
    for project in inventory["projects"]:
        root_id, folder = project["root_id"], project["project_folder"]
        source_root = roots[root_id]
        siblings = {f["rel"].replace("\\", "/") for f in project["files"]}
        for file in project["files"]:
            rel = file["rel"].replace("\\", "/")
            key = (root_id, folder + "/" + rel)
            if key in seen:
                raise ValueError(f"扫描快照存在重复源路径: {key}")
            seen.add(key)
            previous = registered[key]
            result = classifier.classify(folder, rel)
            counts[result.role] += 1
            triggers = [h for h in AUDIT_HINTS if h in file["name"]]
            was_final = previous["document_role"] == classifier.FINAL_SIGNED
            if not (triggers or was_final or result.role == classifier.FINAL_SIGNED):
                continue
            if previous.get("manual_override"):
                status = "manual_override_requires_separate_review"
            elif result.role == classifier.FINAL_SIGNED:
                status = "rule_eligible_pending_review"
            elif result.role == classifier.UNKNOWN:
                status = "pending_review"
            elif result.role == classifier.OUR_RESPONSE:
                status = "response_final_status_unconfirmed"
            else:
                status = "excluded_" + result.role
            pdf = re.sub(r"_\d+\.(?:jpg|jpeg|png)$", ".pdf", rel, flags=re.I)
            sibling_pdf = pdf if pdf != rel and pdf in siblings else None
            rows.append({
                "source_root_id": root_id, "project_folder": folder,
                "relative_path_within_project": rel,
                "source_path": str(PureWindowsPath(source_root) / folder / rel),
                "document_id": previous.get("document_id"),
                "registered_role": previous["document_role"],
                "registered_final_signed": was_final,
                "manual_override": bool(previous.get("manual_override")),
                "candidate_triggers": triggers,
                "auto_role": result.role, "auto_reason": result.reason,
                "vendor_conflict": result.conflict, "audit_status": status,
                "sibling_pdf_path": str(PureWindowsPath(source_root) / folder / sibling_pdf) if sibling_pdf else None,
                "verification_basis": "path_and_filename_only",
                "manual_verified": False, "scoring_enabled": False,
            })
    if seen != set(registered):
        raise ValueError("扫描快照与登记库路径集合不一致，先核对输入版本")
    baseline = [r for r in rows if r["registered_final_signed"]]
    return {
        "scope": "R2 final_signed 路径规则审计；保留原预测和全部签章候选",
        "inventory_file_count": len(seen),
        "registered_final_signed_count": len(baseline),
        "filename_trigger_candidate_count": sum(bool(r["candidate_triggers"]) for r in rows),
        "current_auto_role_distribution": dict(sorted(counts.items())),
        "baseline_final_new_roles": dict(Counter(r["auto_role"] for r in baseline)),
        "baseline_final_audit_statuses": dict(Counter(r["audit_status"] for r in baseline)),
        "null_document_id_count": sum(not r.get("document_id") for r in catalog),
        "metrics": {"precision": None, "recall": None, "reason": "无独立文件级真值，本报告不评分"},
        "baseline_final_signed": baseline,
        "other_candidates": [r for r in rows if not r["registered_final_signed"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    con = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        catalog = [dict(r) for r in con.execute("SELECT * FROM documents")]
    finally:
        con.close()
    report = audit_records(inventory, catalog)
    report["inputs"] = {
        label: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for label, path in (("inventory", args.inventory), ("catalog", args.db),
                            ("classifier", Path(classifier.__file__)), ("audit_script", Path(__file__)))
    }
    # 新报告独占创建，禁止覆盖已有冻结产物。
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("baseline_final_signed", "other_candidates", "inputs")}, ensure_ascii=False))
    print(f"report_sha256={hashlib.sha256(args.output.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
