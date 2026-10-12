# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""FastAPI 网关：对外唯一入口，仅暴露L0/L1能力，不提供任何L2自动化接口。"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db
from .calendar_seed import get_calendar
from .kb import ask as kb_ask
from .pipeline import process_document
from .settings import APP_NAME, APP_VERSION, WEB_DIR, load_master_data

app = FastAPI(title=APP_NAME, version=APP_VERSION)


class ConfirmBody(BaseModel):
    actor: str = "demo_user"
    note: str = ""


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/v1/meta")
def meta():
    md = load_master_data()
    return {
        "app": APP_NAME, "version": APP_VERSION,
        "company": md["company"]["name"],
        "taxpayer_type": md["company"]["taxpayer_type"],
        "l2_note": "付款、申报、缴税、记账、开票等L2动作无自动化接口，须由财务人员人工执行",
    }


@app.post("/api/v1/documents")
def upload(payload: dict):
    """票据上传主流程：安检→抽取→查重→规则校验→建议→归档。"""
    conn = db.get_conn()
    try:
        return process_document(conn, payload)
    finally:
        conn.close()


@app.get("/api/v1/documents")
def list_docs():
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, invoice_no, grade, status, created_at"
            " FROM documents ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/v1/documents/{doc_id}/confirm")
def confirm(doc_id: int, body: ConfirmBody):
    """人工确认：仅记录确认动作与审计事件，不触发任何对外执行。"""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        conn.execute("UPDATE documents SET status='confirmed' WHERE id=?", (doc_id,))
        conn.commit()
        db.record_event(conn, doc_id, body.actor, "HUMAN_CONFIRM",
                        body.note or "人工确认（L2动作由人在外部系统执行）")
        return {"doc_id": doc_id, "status": "confirmed"}
    finally:
        conn.close()


@app.get("/api/v1/calendar")
def calendar():
    return get_calendar()


@app.get("/api/v1/kb/ask")
def ask_kb(q: str):
    return kb_ask(q)


@app.get("/api/v1/audit")
def audit(doc_id: int = None):
    conn = db.get_conn()
    try:
        if doc_id:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE doc_id=? ORDER BY id",
                (doc_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
