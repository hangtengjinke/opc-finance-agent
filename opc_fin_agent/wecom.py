# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""企业微信待办卡片推送网关（L1提醒通道，对应阶段0技术规格接口IF-13）。

四条设计约束：
1. 字段白名单：卡片仅允许 任务标题/截止日期/剩余天数/金额区间/链接/L2提示 六个字段；
2. 绝不允许出现：银行账号、税号、身份证号、验证码、登录凭证（防御性脱敏兜底）；
3. 未配置webhook时为dry-run模式：卡片落本地并入库，不向外发出任何字节；
4. 同一任务同一截止日仅推送一次（去重键 task_id+deadline），防止重复打扰。
"""
import datetime as dt
import json
import re
import sqlite3
import urllib.request
from pathlib import Path

from . import db
from .calendar_seed import get_calendar
from .settings import BASE_DIR

WECOM_CONFIG_PATH = BASE_DIR / "config" / "wecom_config.json"
DRYRUN_DIR = BASE_DIR / "data" / "dryrun_cards"

ALLOWED_FIELDS = {"task_id", "title", "deadline", "days_left", "amount_range", "link", "l2_note"}

_SENSITIVE_PATTERNS = [
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "**[已遮蔽-证件号]**"),   # 身份证号
    (re.compile(r"(?<!\d)\d{12,}(?!\d)"), "**[已遮蔽-长数字串]**"),      # 银行账号/票号等
]
_FORBIDDEN_KEYWORDS = ("密码", "验证码", "口令", "U盾", "私钥")


def load_config():
    """加载推送配置。webhook_url为空即dry-run。"""
    if not WECOM_CONFIG_PATH.exists():
        return {"webhook_url": "", "card_link": "", "amount_range": "以当期台账为准"}
    with open(WECOM_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def sanitize(text):
    """防御性脱敏：长数字串与凭证关键词强制遮蔽。卡片内容仍以白名单构造为第一道防线。"""
    out = str(text)
    for pat, rep in _SENSITIVE_PATTERNS:
        out = pat.sub(rep, out)
    for kw in _FORBIDDEN_KEYWORDS:
        out = re.sub(re.escape(kw) + r"[^，。；、\n]{0,20}", kw + "**[已遮蔽]**", out)
    return out


def build_card(task, today=None):
    """按字段白名单构造待办卡片。金额只给区间口径，不给具体数字与账号。"""
    today = today or dt.date.today()
    deadline = dt.date.fromisoformat(task["next_deadline"])
    cfg = load_config()
    card = {
        "task_id": task["id"],
        "title": f"【财税待办】{task['name']}（{task['cycle']}）",
        "deadline": task["next_deadline"],
        "days_left": (deadline - today).days,
        "amount_range": cfg.get("amount_range", "以当期台账为准"),
        "link": cfg.get("card_link", ""),
        "l2_note": "申报/付款等操作须由财务人员本人登录银行/税务系统执行，本卡片不含任何账号凭证。",
    }
    assert set(card) <= ALLOWED_FIELDS, "卡片字段越出白名单"
    return card


def render_markdown(card):
    """渲染为企业微信群机器人markdown消息体内容。"""
    return (
        f"### {sanitize(card['title'])}\n"
        f"> 截止日期：**{card['deadline']}**（剩 {card['days_left']} 天）\n"
        f"> 涉及金额区间：{sanitize(card['amount_range'])}\n\n"
        f"{card['l2_note']}\n"
        f"[查看资料包与操作指引]({card['link']})"
    )


def _send(card, cfg):
    """真实通道：企业微信群机器人webhook（markdown消息）。失败不阻断、不重试轰炸。"""
    url = cfg.get("webhook_url", "")
    if not url:
        return {"mode": "dryrun", "ok": True, "detail": "webhook未配置，dry-run仅落本地"}
    body = json.dumps(
        {"msgtype": "markdown", "markdown": {"content": render_markdown(card)}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"mode": "real", "ok": resp.status == 200, "detail": f"HTTP {resp.status}"}
    except Exception as e:  # noqa: BLE001 失败仅记录，不抛出
        return {"mode": "real", "ok": False, "detail": str(e)}


def due_tasks(today=None):
    """返回当前处于提醒窗口内（提醒日≤今天≤截止日）的日历任务。"""
    today = today or dt.date.today()
    out = []
    for t in get_calendar():
        remind = dt.date.fromisoformat(t["remind_on"])
        deadline = dt.date.fromisoformat(t["next_deadline"])
        if remind <= today <= deadline:
            out.append(t)
    return out


def push_due_reminders(conn, today=None, force=False):
    """扫描提醒窗口内的任务并推送待办卡片（含去重、审计留痕、dry-run落盘）。"""
    today = today or dt.date.today()
    cfg = load_config()
    pushed_keys = {(r["task_id"], r["deadline"]) for r in
                   conn.execute("SELECT task_id, deadline FROM pushed_cards")}
    results = []
    for t in due_tasks(today):
        key = (t["id"], t["next_deadline"])
        if key in pushed_keys and not force:
            results.append({"task_id": t["id"], "name": t["name"],
                            "deadline": t["next_deadline"], "result": "已推送过，跳过（去重）"})
            continue
        card = build_card(t, today)
        sent = _send(card, cfg)
        md = render_markdown(card)
        created_at = dt.datetime.now().isoformat(timespec="seconds")
        try:
            conn.execute(
                "INSERT INTO pushed_cards"
                " (task_id, deadline, title, payload, mode, status, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (card["task_id"], card["deadline"], card["title"], md,
                 sent["mode"], "ok" if sent["ok"] else "fail", created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            results.append({"task_id": t["id"], "name": t["name"],
                            "deadline": t["next_deadline"], "result": "已推送过，跳过（去重）"})
            continue
        if sent["mode"] == "dryrun":
            DRYRUN_DIR.mkdir(parents=True, exist_ok=True)
            (DRYRUN_DIR / f"{card['task_id']}_{card['deadline']}.md").write_text(
                md, encoding="utf-8")
        db.record_event(conn, None, "system", "WECOM_PUSH",
                       f"task={card['task_id']} deadline={card['deadline']} "
                       f"mode={sent['mode']} ok={sent['ok']}")
        results.append({"task_id": t["id"], "name": t["name"],
                        "deadline": t["next_deadline"],
                        "result": f"已推送（{sent['mode']}，{sent['detail']}）"})
    return results
