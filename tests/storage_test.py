# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""存储层专项测试：PG兼容层 / 对象存储门面 / 后端分发（9用例）。

运行：python tests/storage_test.py
（本机无 PostgreSQL/MinIO 服务也能全通过——未就绪路径验证的是诚实回退，
 部署机验收时由 tools/ 自检脚本与部署指南第6节覆盖真实后端路径。）
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from opc_fin_agent import db, pg_store  # noqa: E402
from opc_fin_agent.objectstore import (  # noqa: E402
    read_original, save_original, object_store_status)
from opc_fin_agent.pipeline import process_document  # noqa: E402
from opc_fin_agent.settings import load_storage_config  # noqa: E402

failed = []


def case(n, name, ok):
    print(f"[用例{n}] {name} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append(n)


# 用例1：默认sqlite后端——get_conn 返回 sqlite 连接且三表就绪
tmp = Path(tempfile.mkdtemp()) / "t.db"
conn = db.get_conn(str(tmp))
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
case(1, "sqlite默认后端三表就绪",
     isinstance(conn, sqlite3.Connection)
     and all(t in tables for t in ("documents", "audit_events", "pushed_cards")))
conn.close()

# 用例2：PG未就绪诚实回退——坏DSN必须抛 StorageNotConfigured 且带部署指引
try:
    pg_store.PgCompatConn("postgresql://127.0.0.1:59999/no_such_db")
    ok2 = False
except pg_store.StorageNotConfigured as e:
    msg = str(e)
    ok2 = ("requirements-prod" in msg or "psycopg" in msg
           or "部署指南" in msg or "连接失败" in msg)
except Exception:
    ok2 = False
case(2, "PG未就绪诚实回退带指引", ok2)

# 用例3：SQL方言翻译纯函数——?→%s 且不误伤字面量以外内容
sql = "SELECT id FROM documents WHERE invoice_no=? AND content_hash=?"
case(3, "SQL占位符翻译",
     pg_store.translate_sql(sql) ==
     "SELECT id FROM documents WHERE invoice_no=%s AND content_hash=%s")

# 用例4：PG DDL按分号切分后逐条非空且全部为CREATE语句（惰性建表前提）
stmts = [s.strip() for s in pg_store.PG_SCHEMA.split(";") if s.strip()]
case(4, "PG DDL切分有效",
     len(stmts) == 5 and all(s.upper().startswith("CREATE") for s in stmts))

# 用例5：local对象存储存取回环——字节一致
data5 = json.dumps({"demo": "原件内容"}, ensure_ascii=False).encode("utf-8")
ref5 = save_original("test_roundtrip.json", data5)
back5 = read_original(ref5)
case(5, "对象存储存取回环", back5 == data5 and Path(ref5).exists())

# 用例6：原件不可变——同key二次写入不覆盖首次内容
first = save_original("test_immutable.json", b"FIRST")
save_original("test_immutable.json", b"SECOND-OVERWRITE-ATTEMPT")
case(6, "原件不可变二次写入不覆盖", read_original(first) == b"FIRST")

# 用例7：minio后端未配置——诚实回退且不崩溃（不污染配置文件）
try:
    save_original("k.json", b"x",
                  cfg={"object_store": {"backend": "minio", "endpoint": "",
                                        "bucket": "fin-originals"}})
    ok7 = False
except pg_store.StorageNotConfigured as e:
    ok7 = "endpoint" in str(e) and "部署指南" in str(e)
case(7, "minio未配置诚实回退", ok7)

# 用例8：存储状态探针——字段齐全供meta展示
st_db, st_obj = db.storage_status(), object_store_status()
case(8, "存储状态探针字段齐全",
     {"backend", "pg_driver", "pg_dsn_source"} <= set(st_db)
     and {"backend", "bucket", "minio_sdk"} <= set(st_obj))

# 用例9：主流程归档经对象存储门面——引用可反读、建议分级正常
conn9 = db.get_conn(str(Path(tempfile.mkdtemp()) / "t9.db"))
payload = {"invoice": {
    "invoice_no": "24312000000012345678", "issue_date": "2026-09-18",
    "seller_name": "Demo_Supplier_02", "seller_tax_no": "91310000DEMO00000AX",
    "buyer_name": "瀚钦科技有限公司", "buyer_tax_no": "91310000DEMO000000X",
    "amount": 800.00, "tax": 8.00, "total": 808.00, "category": "办公用品"}}
r9 = process_document(conn9, payload)
ref9 = r9["archive_path"]
ok9 = (r9["status"] == "ok" and r9["advice"]["grade"] == "A"
       and json.loads(read_original(ref9).decode("utf-8"))["invoice"]["invoice_no"]
       == payload["invoice"]["invoice_no"])
conn9.close()
case(9, "主流程归档经对象存储可反读", ok9)

# 配置健壮性附加断言（不计用例数）：默认配置可加载且backend合法
cfg = load_storage_config()
assert cfg["database"]["backend"] in ("sqlite", "pg"), "非法database.backend"
assert cfg["object_store"]["backend"] in ("local", "minio"), "非法object_store.backend"

print("-" * 40)
if failed:
    print(f"结果：{len(failed)} 用例失败 -> {failed}")
    sys.exit(1)
print("存储层测试 9/9 全部通过")
