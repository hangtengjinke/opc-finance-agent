# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""冒烟测试：主流程管道四类典型结果（通过/重复/硬失败/软提醒）。

运行：python tests/smoke_test.py
"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from opc_fin_agent import db
from opc_fin_agent.pipeline import process_document


def demo_invoice(**overrides):
    payload = {
        "doc_type": "invoice_eticket",
        "file_name": "Demo_ETicket_001.json",
        "invoice": {
            "invoice_no": "24312000000012345678",
            "issue_date": "2026-09-18",
            "seller_name": "Demo_Supplier_01",
            "seller_tax_no": "91310000DEMO1111AAA",
            "buyer_name": "瀚钦科技有限公司",
            "buyer_tax_no": "91310000DEMO000000X",
            "amount": 1000.00,
            "tax": 10.00,
            "total": 1010.00,
            "category": "办公用品",
        },
    }
    payload["invoice"].update(overrides)
    return payload


def main():
    tmp = Path(tempfile.mkdtemp()) / "smoke.db"
    conn = db.get_conn(tmp)
    failed = []

    # 用例1：合规票据 → 等级A，归档成功
    r1 = process_document(conn, demo_invoice())
    assert_ok = r1["status"] == "ok" and r1["advice"]["grade"] == "A"
    print(f"[用例1] 合规票据       -> status={r1['status']} grade={r1['advice']['grade']}  {'PASS' if assert_ok else 'FAIL'}")
    if not assert_ok:
        failed.append("case1")

    # 用例2：同一票重复提交 → 拦截
    r2 = process_document(conn, demo_invoice())
    assert_ok = r2["status"] == "blocked"
    print(f"[用例2] 重复票据       -> status={r2['status']}  {'PASS' if assert_ok else 'FAIL'}  {r2['advice']['summary']}")
    if not assert_ok:
        failed.append("case2")

    # 用例3：价税合计不勾稽 → 等级C（硬失败转人工）
    r3 = process_document(conn, demo_invoice(
        invoice_no="24312000000099999999", total=1020.00))
    assert_ok = r3["status"] == "ok" and r3["advice"]["grade"] == "C"
    print(f"[用例3] 勾稽失败       -> status={r3['status']} grade={r3['advice']['grade']}  {'PASS' if assert_ok else 'FAIL'}")
    if not assert_ok:
        failed.append("case3")

    # 用例4：费用类别不在清单 → 等级B（软提醒）
    r4 = process_document(conn, demo_invoice(
        invoice_no="24312000000088888888", category=" unrecognized类别"))
    assert_ok = r4["status"] == "ok" and r4["advice"]["grade"] == "B"
    print(f"[用例4] 类别清单外     -> status={r4['status']} grade={r4['advice']['grade']}  {'PASS' if assert_ok else 'FAIL'}")
    if not assert_ok:
        failed.append("case4")

    # 用例5：安检拦截（结构非法）
    r5 = process_document(conn, {"foo": "bar"})
    assert_ok = r5["status"] == "rejected"
    print(f"[用例5] 非法结构       -> status={r5['status']}  {'PASS' if assert_ok else 'FAIL'}")
    if not assert_ok:
        failed.append("case5")

    conn.close()
    if failed:
        print(f"结果：{len(failed)} 个用例失败 -> {failed}")
        sys.exit(1)
    print("结果：5/5 全部通过")


if __name__ == "__main__":
    main()
