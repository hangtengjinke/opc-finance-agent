# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""FastAPI 网关：对外唯一入口，仅暴露L0/L1能力，不提供任何L2自动化接口。"""
import base64
import datetime as dt
import json

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
from .settings import APP_NAME, APP_VERSION, BASE_DIR, WEB_DIR, load_master_data
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
def push_cards(dry_run: str = ""):
    """推送提醒窗口内待办卡片至企业微信（未配置webhook时为dry-run落本地）。
    测试钩子：dry_run=fail 模拟推送失败，用于验证前端自动重试与原因清单。"""
    if dry_run == "fail":
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="模拟推送失败：网络不通（测试钩子）")
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


# ================= V1.1 P0 新增接口（小白友好升级，仅界面聚合，不动业务内核） =================

ONBOARDING_STATE_PATH = BASE_DIR / "config" / "onboarding_state.json"

GLOSSARY = [
    {"term": "数电票", "plain": "电子发票，税务局发的数字发票，不用纸质版也能正常入账。"},
    {"term": "查重", "plain": "检查这张票是不是已经录入过，防止同一张票重复入账。"},
    {"term": "建议等级", "plain": "系统给出的处理意见：A=可以直接确认；B=核对后确认；C=转人工。"},
    {"term": "置信度", "plain": "机器识别有多准：90%以上算很准；低于90%建议人工再核对一眼。"},
    {"term": "小规模纳税人", "plain": "规模小、税率低的企业类型，一般按季度申报。"},
    {"term": "归档", "plain": "把票据存进台账留底，之后随时可查。"},
    {"term": "测试模式", "plain": "演示用的推送模式，不会真的发到微信，只是让你看效果。"},
    {"term": "人工核验", "plain": "机器没认准的时候，需要你对照原件核对、修正信息。"},
    {"term": "转人工", "plain": "这笔业务超出系统能处理的范围，需要交给财务或税务专业人员处理。"},
    {"term": "申报期", "plain": "向税务局报税的期限，到期前要完成申报，逾期会有影响。"},
    {"term": "操作留痕", "plain": "系统记录谁在什么时候做了什么操作，随时可查，安全放心。"},
    {"term": "价税合计", "plain": "一张票的\"价钱\"= 商品金额 + 税钱，合起来叫价税合计。"},
    {"term": "含税/不含税", "plain": "含税=价格里已经算进税钱；不含税=只算商品本身的价钱。看金额前先分清这个。"},
    {"term": "台账", "plain": "系统里的票据记账本，归档后的票都存在这里，随时可查。"},
    {"term": "电子税务局", "plain": "税务局官方的网上办税平台，报税、缴税都在那里办，本系统不会替你登录。"},
    {"term": "申报表", "plain": "给税务局交的报表，说明你这段时间该交多少税。"},
    {"term": "税种", "plain": "税的种类，比如增值税、所得税、印花税，不同税种交法不同。"},
    {"term": "OCR识别", "plain": "机器把图片上的字\"读\"出来变成文字，就是认票据图片的技术。"},
    {"term": "拖拽上传", "plain": "直接把文件图标用鼠标拖到框里就能上传，不用点开文件夹找。"},
    {"term": "查重拦截", "plain": "系统发现这张票以前录过了，自动拦下，免得你重复入账。"},
    {"term": "人工确认", "plain": "由你亲自点一下\"确认\"，表示你核对过这张票，确认后记入台账留痕。"},
]


def _load_onboarding():
    try:
        with open(ONBOARDING_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"done": False, "step": 0}


class OnboardingBody(BaseModel):
    done: bool = None
    step: int = None


@app.get("/api/v1/dashboard")
def dashboard():
    """首页驾驶舱聚合：税期倒计时 + 待确认票据数 + 待推送任务数 + 最近5笔票据。"""
    conn = db.get_conn()
    try:
        today = dt.date.today()
        upcoming = [t for t in get_calendar()
                    if t["next_deadline"] >= today.isoformat()]
        nearest = min(upcoming, key=lambda t: t["next_deadline"]) if upcoming else None
        countdown = None
        if nearest:
            days_left = (dt.date.fromisoformat(nearest["next_deadline"]) - today).days
            countdown = {
                "task_name": nearest["name"],
                "deadline": nearest["next_deadline"],
                "days_left": days_left,
            }
        pending_count = conn.execute(
            "SELECT COUNT(*) c FROM documents"
            " WHERE status IN ('pending','pending_verification')").fetchone()["c"]
        pushed = {(r["task_id"], r["deadline"]) for r in
                  conn.execute("SELECT task_id, deadline FROM pushed_cards")}
        todo_push = [t for t in due_tasks()
                     if (t["id"], t["next_deadline"]) not in pushed]
        rows = conn.execute(
            "SELECT id, invoice_no, grade, status, fields, created_at"
            " FROM documents ORDER BY id DESC LIMIT 5").fetchall()
        recent = []
        for r in rows:
            amount = None
            try:
                fields = json.loads(r["fields"] or "{}")
                amount = fields.get("total") or fields.get("amount")
            except ValueError:
                pass
            recent.append({"id": r["id"], "invoice_no": r["invoice_no"],
                           "grade": r["grade"], "status": r["status"],
                           "amount": amount, "date": r["created_at"]})
        return {
            "countdown": countdown,
            "pending_count": int(pending_count),
            "pending_push_count": len(todo_push),
            "recent": recent,
            "company": load_master_data()["company"],
        }
    finally:
        conn.close()


@app.get("/api/v1/onboarding")
def get_onboarding():
    state = _load_onboarding()
    return {"done": bool(state.get("done", False)), "step": int(state.get("step", 0))}


@app.post("/api/v1/onboarding")
def set_onboarding(body: OnboardingBody):
    state = _load_onboarding()
    if body.done is not None:
        state["done"] = bool(body.done)
    if body.step is not None:
        state["step"] = int(body.step)
        if body.step >= 3:
            state["done"] = True
    with open(ONBOARDING_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return state


@app.post("/api/v1/documents/tutorial")
def run_tutorial():
    """一键演示：合成演示票据（图片+确定性OCR输出），跑通完整图片票据流程。"""
    conn = db.get_conn()
    try:
        demo = demo_ocr_output("clear")
        png = make_demo_png()
        return process_image_document(
            conn, "Demo_一键演示.png", png, demo, actor="演示用户", engine_key="demo")
    finally:
        conn.close()


@app.get("/api/v1/glossary")
def glossary():
    return {"items": GLOSSARY, "version": "1.0"}
