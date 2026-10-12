# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""SQLite → PostgreSQL 数据迁移工具（一次性，部署切换时执行）。

用法：
    python tools/migrate_sqlite_to_pg.py [--sqlite data/opc.db] [--dsn "postgresql://..."]

不指定 --dsn 时读取 config/storage_config.json 的 database.pg_dsn
（或环境变量 OPC_PG_DSN）。

迁移内容与验收：
1. 三张表（documents/audit_events/pushed_cards）按 id 顺序全量复制，保留原 id；
2. 迁移后逐表核对行数一致；
3. 审计哈希链完整性校验：按存储字段重算每条 event_hash 并与迁移值比对，
   任一不匹配即失败退出（防止迁移过程篡改/丢行）；
4. 修复 identity 序列，确保新插入不与迁移 id 冲突。

注意：本工具不会删除 SQLite 原库（回滚兜底，切换观察期后再人工处置）。
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from opc_fin_agent.pg_store import PG_SCHEMA, StorageNotConfigured  # noqa: E402
from opc_fin_agent.settings import load_storage_config  # noqa: E402

TABLES = ["documents", "audit_events", "pushed_cards"]
COLS = {
    "documents": ["id", "invoice_no", "content_hash", "archive_path", "payload",
                  "fields", "grade", "status", "created_at"],
    "audit_events": ["id", "doc_id", "actor", "action", "detail",
                     "prev_hash", "event_hash", "created_at"],
    "pushed_cards": ["id", "task_id", "deadline", "title", "payload",
                     "mode", "status", "created_at"],
}


def recompute_event_hash(row):
    base = json.dumps(
        [row["doc_id"], row["actor"], row["action"], row["detail"],
         row["prev_hash"], row["created_at"]],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=str(BASE / "data" / "opc.db"))
    ap.add_argument("--dsn", default="")
    args = ap.parse_args()

    dsn = args.dsn or load_storage_config()["database"].get("pg_dsn") \
        or os.environ.get("OPC_PG_DSN", "")
    if not dsn:
        print("[FAIL] 未提供目标 DSN（--dsn / pg_dsn / OPC_PG_DSN）")
        sys.exit(1)

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("[FAIL] psycopg 未安装，请先：pip install -r deploy/requirements-prod.txt")
        sys.exit(1)

    src = sqlite3.connect(args.sqlite)
    src.row_factory = sqlite3.Row
    pg = psycopg.connect(dsn, row_factory=dict_row)
    try:
        cur = pg.cursor()
        for stmt in PG_SCHEMA.split(";"):
            if stmt.strip():
                cur.execute(stmt)
        pg.commit()

        report = {}
        for table in TABLES:
            cols = COLS[table]
            rows = src.execute(
                f"SELECT {','.join(cols)} FROM {table} ORDER BY id"
            ).fetchall()
            placeholders = ",".join(["%s"] * len(cols))
            for r in rows:
                cur.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
                    f" ON CONFLICT (id) DO NOTHING",
                    tuple(r[c] for c in cols))
            pg.commit()
            pg_count = cur.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            report[table] = (len(rows), pg_count)
            print(f"[OK] {table}: sqlite={len(rows)} -> pg={pg_count}")

        # identity 序列修复
        for table in TABLES:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'),"
                " COALESCE((SELECT MAX(id) FROM " + table + "), 1))",
                (table,))
        pg.commit()

        # 审计哈希链完整性校验
        bad = 0
        for row in cur.execute(
                "SELECT doc_id, actor, action, detail, prev_hash, event_hash,"
                " created_at FROM audit_events ORDER BY id").fetchall():
            if recompute_event_hash(row) != row["event_hash"]:
                bad += 1
                print(f"[FAIL] 审计链校验失败 event#{row['event_hash'][:16]}")
        if bad:
            sys.exit(1)
        print("[OK] 审计哈希链完整性校验通过（迁移未篡改）")

        mismatch = [t for t, (a, b) in report.items() if a != b]
        if mismatch:
            print(f"[FAIL] 行数不一致：{mismatch}")
            sys.exit(1)
        print("[DONE] 迁移完成。请改 config/storage_config.json 的"
              " database.backend=pg 并重启服务，观察期后再处置 SQLite 原库。")
    except StorageNotConfigured as e:
        print(f"[FAIL] {e}")
        sys.exit(1)
    finally:
        pg.close()
        src.close()


if __name__ == "__main__":
    main()
