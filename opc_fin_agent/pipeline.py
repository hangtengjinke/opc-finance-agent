# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""主流程管道：安检 → 抽取 → 查重 → 规则校验 → 处理建议 → 归档（全程留痕）。"""
import datetime as dt
import hashlib
import json

from .db import record_event
from .rules import complete_fields, run_rules
from .settings import ARCHIVE_DIR, load_master_data

MAX_PAYLOAD_BYTES = 2 * 1024 * 1024  # 2MB，超出即安检拦截


def security_check(payload):
    """L1 安检：结构合法性与大小限制（规则引擎判断，非模型判断）。"""
    issues = []
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        issues.append("payload超过2MB上限")
    if not isinstance(payload, dict) or "invoice" not in payload:
        issues.append("缺少invoice字段，非有效票据结构")
    return {"passed": not issues, "issues": issues}


def extract_fields(payload):
    """字段抽取：数电票为结构化数据直接读取；OCR低置信度场景由人工核验队列处理。"""
    inv = payload.get("invoice", {})
    return {
        "invoice_no": str(inv.get("invoice_no", "")).strip(),
        "issue_date": str(inv.get("issue_date", "")).strip(),
        "seller_name": str(inv.get("seller_name", "")).strip(),
        "seller_tax_no": str(inv.get("seller_tax_no", "")).strip(),
        "buyer_name": str(inv.get("buyer_name", "")).strip(),
        "buyer_tax_no": str(inv.get("buyer_tax_no", "")).strip(),
        "amount": inv.get("amount"),
        "tax": inv.get("tax"),
        "total": inv.get("total"),
        "category": str(inv.get("category", "")).strip(),
    }


def content_hash(payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_duplicate(conn, fields, chash):
    """查重：内容哈希防重复入账 + 票号唯一性双重拦截。"""
    row = conn.execute(
        "SELECT id FROM documents WHERE content_hash=?", (chash,)
    ).fetchone()
    if row:
        return {"blocked": True, "reason": f"内容哈希重复（已归档文档#{row['id']}）"}
    if fields["invoice_no"]:
        row = conn.execute(
            "SELECT id FROM documents WHERE invoice_no=?", (fields["invoice_no"],)
        ).fetchone()
        if row:
            return {"blocked": True, "reason": f"票号重复（已归档文档#{row['id']}）"}
    return {"blocked": False, "reason": ""}


def archive_payload(payload, chash):
    """归档：原件以哈希命名落盘（生产环境为MinIO fin-originals桶，不可变）。"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE_DIR / f"{chash}.json"
    if not path.exists():
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return str(path)


def build_advice(fields, rule_results, dup):
    """处理建议：A/B/C三级置信度分级，C级强制人工复核（硬编码规则）。"""
    hard_fails = [r for r in rule_results if not r["passed"] and r["severity"] == "hard"]
    soft_warns = [r for r in rule_results if not r["passed"] and r["severity"] == "soft"]
    if dup["blocked"]:
        grade, summary = "C", f"查重拦截：{dup['reason']}"
    elif hard_fails or not complete_fields(fields):
        grade = "C"
        summary = "存在硬性校验失败或字段缺失，转人工复核：" + "；".join(
            r["message"] for r in hard_fails)
    elif soft_warns:
        grade = "B"
        summary = "存在软性提醒，请人工核对后处理：" + "；".join(
            r["message"] for r in soft_warns)
    else:
        grade, summary = "A", (
            "全部校验通过。入账科目建议（草稿）：按费用类别归集，"
            "由财务人员确认后人工执行记账（L2）。"
        )
    return {
        "grade": grade, "summary": summary,
        "sources": [f"{r['rule_id']} {r['name']}" for r in rule_results],
    }


def process_document(conn, payload, actor="demo_user"):
    """主流程入口。返回各步骤结果；查重拦截与安检失败均不归档。"""
    steps = []
    sec = security_check(payload)
    steps.append(("安检", sec["passed"], "; ".join(sec["issues"]) or "通过"))
    if not sec["passed"]:
        record_event(conn, None, actor, "REJECT_SECURITY", "; ".join(sec["issues"]))
        return {"status": "rejected", "steps": steps}

    fields = extract_fields(payload)
    chash = content_hash(payload)
    dup = check_duplicate(conn, fields, chash)
    steps.append(("查重", not dup["blocked"], dup["reason"] or "通过"))

    rule_results = run_rules(fields, load_master_data())
    failed_msgs = "; ".join(
        r["message"] for r in rule_results if not r["passed"]) or "全部通过"
    steps.append(("规则校验", all(r["passed"] for r in rule_results), failed_msgs))

    advice = build_advice(fields, rule_results, dup)
    if dup["blocked"]:
        record_event(conn, None, actor, "BLOCK_DUPLICATE", dup["reason"])
        return {"status": "blocked", "fields": fields, "advice": advice,
                "steps": steps, "content_hash": chash}

    archive_path = archive_payload(payload, chash)
    cur = conn.execute(
        "INSERT INTO documents"
        " (invoice_no, content_hash, archive_path, payload, fields, grade, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (fields["invoice_no"], chash, archive_path,
         json.dumps(payload, ensure_ascii=False),
         json.dumps(fields, ensure_ascii=False), advice["grade"],
         dt.datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    doc_id = cur.lastrowid
    record_event(conn, doc_id, actor, "ARCHIVE",
                 f"grade={advice['grade']} sha256={chash[:16]}")
    return {"status": "ok", "doc_id": doc_id, "fields": fields,
            "advice": advice, "steps": steps, "content_hash": chash,
            "archive_path": archive_path}
