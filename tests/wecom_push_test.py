# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""冒烟测试：企业微信待办卡片推送通道。

覆盖：字段白名单、防御性脱敏、提醒窗口判定、去重推送、dry-run落盘。
运行：python tests/wecom_push_test.py
"""
import datetime as dt
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from opc_fin_agent import db
from opc_fin_agent.wecom import (ALLOWED_FIELDS, build_card, due_tasks,
                                 push_due_reminders, sanitize)


def main():
    tmp = Path(tempfile.mkdtemp()) / "wecom.db"
    conn = db.get_conn(tmp)
    today = dt.date(2026, 10, 12)
    failed = []

    # 用例1：脱敏——长数字串（银行账号）与验证码关键词必须被遮蔽
    dirty = "收款账号6222020200112233445，验证码123456，身份证310101199001011234"
    clean = sanitize(dirty)
    ok = ("6222020200112233445" not in clean
          and "310101199001011234" not in clean
          and "123456" not in clean
          and "验证码**[已遮蔽]**" in clean)
    print(f"[用例1] 脱敏防线       -> {'PASS' if ok else 'FAIL'}  输出：{clean[:60]}…")
    if not ok:
        failed.append("case1")

    # 用例2：卡片字段白名单——构造的卡片不得越出六字段白名单
    tasks = due_tasks(today)
    cards = [build_card(t, today) for t in tasks]
    ok = all(set(c) <= ALLOWED_FIELDS for c in cards) and len(cards) > 0
    print(f"[用例2] 字段白名单    -> {'PASS' if ok else 'FAIL'}  窗口内任务数={len(tasks)} 字段集={sorted(cards[0].keys()) if cards else []}")
    if not ok:
        failed.append("case2")

    # 用例3：首次推送——窗口内任务全部推送成功（dry-run），卡片入库且落盘
    r1 = push_due_reminders(conn, today)
    ok = (len(r1) == len(tasks) and all("已推送" in x["result"] for x in r1))
    n_db = conn.execute("SELECT COUNT(*) c FROM pushed_cards").fetchone()["c"]
    print(f"[用例3] 首次推送       -> {'PASS' if ok else 'FAIL'}  推送{len(r1)}张/窗口{len(tasks)}张，入库{n_db}条")
    if not ok or n_db != len(tasks):
        failed.append("case3")

    # 用例4：重复推送——同任务同截止日全部去重跳过
    r2 = push_due_reminders(conn, today)
    ok = all("去重" in x["result"] or "跳过" in x["result"] for x in r2)
    n_db2 = conn.execute("SELECT COUNT(*) c FROM pushed_cards").fetchone()["c"]
    print(f"[用例4] 去重推送       -> {'PASS' if ok else 'FAIL'}  第二次全部跳过，入库仍{n_db2}条")
    if not ok or n_db2 != n_db:
        failed.append("case4")

    # 用例5：dry-run卡片落盘且内容不含敏感字段
    dry_files = list((BASE / "data" / "dryrun_cards").glob("*.md"))
    content_ok = True
    for f in dry_files:
        txt = f.read_text(encoding="utf-8")
        if any(k in txt for k in ("税号", "账号6", "验证码：", "密码")):
            content_ok = False
    mode_ok = all(r["mode"] == "dryrun" for r in
                  [dict(x) for x in conn.execute("SELECT mode FROM pushed_cards")])
    print(f"[用例5] dry-run落盘    -> {'PASS' if (dry_files and content_ok and mode_ok) else 'FAIL'}  落盘{len(dry_files)}张，内容安检{'通过' if content_ok else '失败'}")
    if not (dry_files and content_ok and mode_ok):
        failed.append("case5")

    # 用例6：审计链——每张卡片推送都有WECOM_PUSH审计事件
    n_audit = conn.execute(
        "SELECT COUNT(*) c FROM audit_events WHERE action='WECOM_PUSH'").fetchone()["c"]
    ok = n_audit == len(tasks)
    print(f"[用例6] 审计留痕       -> {'PASS' if ok else 'FAIL'}  WECOM_PUSH事件{n_audit}条")
    if not ok:
        failed.append("case6")

    conn.close()
    if failed:
        print(f"结果：{len(failed)} 个用例失败 -> {failed}")
        sys.exit(1)
    print("结果：6/6 全部通过")


if __name__ == "__main__":
    main()
