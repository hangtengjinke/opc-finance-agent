# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""数据访问层：SQLite 存储票据与审计事件，审计采用哈希链防篡改。"""
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from .settings import DATA_DIR

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
"""


def get_conn(path=None):
    """获取数据库连接；生产环境替换为分区化的 PostgreSQL（见阶段0技术规格）。"""
    if path is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "opc.db"
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


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
