# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""数据访问层：SQLite/PostgreSQL 双后端，审计采用哈希链防篡改。

后端由 config/storage_config.json 的 database.backend 决定：
- sqlite：开发机零依赖默认
- pg    ：生产后端，经 pg_store.PgCompatConn（sqlite API 兼容包装器）切换
"""
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from .pg_store import PgCompatConn, pg_driver_status
from .settings import DATA_DIR, load_storage_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no TEXT,
    content_hash TEXT UNIQUE,
    archive_path TEXT,
    payload TEXT,
    fields TEXT,
    grade TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER,
    actor TEXT,
    action TEXT,
    detail TEXT,
    prev_hash TEXT,
    event_hash TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS pushed_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    deadline TEXT NOT NULL,
    title TEXT,
    payload TEXT,
    mode TEXT,
    status TEXT,
    created_at TEXT,
    UNIQUE(task_id, deadline)
);
"""


def get_conn(path=None):
    """获取数据库连接：按 storage_config 分发 sqlite / pg 后端。"""
    dbcfg = load_storage_config()["database"]
    if dbcfg.get("backend") == "pg":
        return PgCompatConn(dbcfg.get("pg_dsn", ""))
    if path is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "opc.db"
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def storage_status():
    """存储后端状态探针（供 meta 接口展示，部署验收一眼可见）。"""
    dbcfg = load_storage_config()["database"]
    return {
        "backend": dbcfg.get("backend", "sqlite"),
        "pg_driver": pg_driver_status(),
        "pg_dsn_source": "env:OPC_PG_DSN" if not dbcfg.get("pg_dsn") else "config",
    }


def record_event(conn, doc_id, actor, action, detail=""):
    """追加审计事件：每条事件哈希引用前一条，形成防篡改链条。"""
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    prev = conn.execute(
        "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = prev["event_hash"] if prev else "GENESIS"
    base = json.dumps(
        [doc_id, actor, action, detail, prev_hash, created_at],
        ensure_ascii=False, sort_keys=True,
    )
    event_hash = hashlib.sha256(base.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO audit_events"
        " (doc_id, actor, action, detail, prev_hash, event_hash, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (doc_id, actor, action, detail, prev_hash, event_hash, created_at),
    )
    conn.commit()
    return event_hash
