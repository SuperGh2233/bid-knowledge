# 清理误写残留：删除「当前判据 header_found=False 却仍有 LEDGER 行」的文档的记录（只写 reg 库）
# 这是一次性补救 —— 第一次宽判据backfill把报价表误当业绩写入，第二次修正判据因「不得清空」红线未清。
# 判据与 probe_residual_dryrun 完全一致（当前抽取器判定 → 隔离误写），故本次删除是「数据修正」而非扩大红线。
import sqlite3, os, sys, time, shutil, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.extract import extract_contract_ledger_state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get('BID_AI_CLEAN_DB', os.path.join(ROOT, 'bid_ai_clean_reg.db'))
assert DB.endswith('_reg.db') and os.path.basename(DB) != 'bid_ai_clean.db', f'拒绝写非测试库：{DB}'

def main(dry_run=True):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    docs = [dict(r) for r in con.execute(
        "SELECT c.document_id, d.canonical_document_id, d.relative_path, COUNT(*) n "
        "FROM contracts c JOIN documents d ON d.document_id = c.document_id "
        "WHERE c.contract_id LIKE 'LEDGER-%' GROUP BY c.document_id").fetchall()]
    to_del = []
    for info in docs:
        row = con.execute("SELECT text FROM parse_artifacts WHERE canonical_document_id=? LIMIT 1",
                          (info['canonical_document_id'],)).fetchone() if info['canonical_document_id'] else None
        if not row or not row['text']:
            continue
        st = extract_contract_ledger_state(row['text'], info['document_id'])
        if not st['header_found']:
            to_del.append(info['document_id'])
    n_rows = 0
    if to_del:
        ph = ','.join('?' * len(to_del))
        n_rows = con.execute(f"SELECT COUNT(*) n FROM contracts WHERE contract_id LIKE 'LEDGER-%' "
                             f"AND document_id IN ({ph})", to_del).fetchone()['n']
    print(f'待清理文档 {len(to_del)} 份 / LEDGER 行 {n_rows}')
    if dry_run:
        print('[dry-run] 不写库。确认后执行：python script.py no-dry-run')
        con.close(); return
    bak = f"{DB}.bak-cleanup-{time.strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(DB, bak)
    print(f'清理前备份：{os.path.basename(bak)}')
    cur = con.execute(f"DELETE FROM contracts WHERE contract_id LIKE 'LEDGER-%' AND document_id IN ({ph})",
                      to_del)
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM contracts WHERE contract_id LIKE 'LEDGER-%'").fetchone()[0]
    print(f'删除 {cur.rowcount} 行；当前 LEDGER 总行数 {after}')
    con.close()

if __name__ == '__main__':
    main(dry_run='no-dry-run' not in sys.argv)