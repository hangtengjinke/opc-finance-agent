# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""FastAPI 网关：对外唯一入口，仅暴露L0/L1能力，不提供任何L2自动化接口。"""
import base64

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db
from .objectstore import object_store_status
from .calendar_seed import get_calendar
from .kb import ask as kb_ask, kb_inventory, search_kb
from .ocr import (demo_ocr_output, engine_status, make_demo_png,
                  process_image_document, verify_ocr_document)
from .pipeline import process_document
from .settings import APP_NAME, APP_VERSION, WEB_DIR, load_master_data
from .wecom import due_tasks, push_due_reminders

app = FastAPI(title=APP_NAME, version=APP_VERSION)


class ConfirmBody(BaseModel):
    actor: str = "demo_user"
    note: str = ""


class ImageDocBody(BaseModel):
    file_name: str = "Demo_Image.png"
    image_base64: str
    ocr_output: dict = None
    engine: str = "auto"  # auto=按config/ocr_config.json解析（demo/paddle）
    actor: str = "demo_user"


class VerifyOcrBody(BaseModel):
    actor: str = "财务人员"
    invoice: dict


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
        "ocr_engine": engine_status(),
        "storage": db.storage_status(),
        "object_store": object_store_status(),
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


@app.post("/api/v1/documents/image")
def upload_image(body: ImageDocBody):
    """OCR图片票据主流程：图片安检→查重→OCR→置信度门禁→规则校验→归档/待核验。"""
    conn = db.get_conn()
    try:
        image_bytes = base64.b64decode(body.image_base64)
        return process_image_document(
            conn, body.file_name, image_bytes, body.ocr_output,
            body.actor, body.engine)
    except Exception as e:
        raise HTTPException(400, f"图片处理失败：{e}")
    finally:
        conn.close()


@app.post("/api/v1/documents/{doc_id}/verify_ocr")
def verify_ocr(doc_id: int, body: VerifyOcrBody):
    """人工核验回填：OCR待核验文档由财务人员核对修正字段后提交。"""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, status FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        if row["status"] != "pending_verification":
            raise HTTPException(400, "仅待核验(pending_verification)文档需核验回填")
        return verify_ocr_document(conn, doc_id, body.invoice, body.actor)
    finally:
        conn.close()


@app.get("/api/v1/documents/{doc_id}")
def get_doc(doc_id: int):
    """单文档详情（含OCR字段置信度），供人工核验界面预填。"""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, invoice_no, grade, status, payload, fields, created_at"
            " FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        return dict(row)
    finally:
        conn.close()


@app.get("/api/v1/ocr/demo")
def ocr_demo(quality: str = "clear"):
    """生成演示数据流（合成图片+确定性OCR输出），供界面演示与测试使用。"""
    if quality not in ("clear", "blur"):
        raise HTTPException(400, "quality仅支持clear/blur")
    width = 800 if quality == "clear" else 640
    png = make_demo_png(width=width, height=600)
    return {
        "file_name": f"Demo_Image_{'清晰' if quality == 'clear' else '模糊'}.png",
        "image_base64": base64.b64encode(png).decode("ascii"),
        "ocr_output": demo_ocr_output(quality),
    }


@app.get("/api/v1/notifications/pending")
def pending_cards():
    """预览当前提醒窗口内、尚未推送的待办卡片（不发送）。"""
    conn = db.get_conn()
    try:
        pushed = {(r["task_id"], r["deadline"]) for r in
                  conn.execute("SELECT task_id, deadline FROM pushed_cards")}
        out = []
        for t in due_tasks():
            if (t["id"], t["next_deadline"]) not in pushed:
                out.append({"task_id": t["id"], "name": t["name"], "cycle": t["cycle"],
                            "deadline": t["next_deadline"], "remind_on": t["remind_on"],
                            "agent_action": t["agent_action"]})
        return out
    finally:
        conn.close()


@app.post("/api/v1/notifications/push")
def push_cards():
    """推送提醒窗口内待办卡片至企业微信（未配置webhook时为dry-run落本地）。"""
    conn = db.get_conn()
    try:
        return push_due_reminders(conn)
    finally:
        conn.close()


@app.get("/api/v1/notifications")
def list_cards():
    """已推送卡片清单（含模式与内容，供审计核对）。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, task_id, deadline, title, mode, status, created_at"
            " FROM pushed_cards ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/v1/kb/ask")
def ask_kb(q: str):
    return kb_ask(q)


@app.get("/api/v1/kb/search")
def kb_search(q: str, top_k: int = 3):
    """检索明细（透明化）：评分排序+命中词+段落ID，供审计与界面展示。"""
    hits, q_tokens = search_kb(q, top_k)
    return {"query_tokens": q_tokens, "hits": hits}


@app.get("/api/v1/kb/documents")
def kb_docs():
    """知识库清单：已索引文档（含审签人）与被门禁排除的文件。"""
    return kb_inventory()


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
