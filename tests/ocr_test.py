# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""冒烟测试：OCR图片票据接入层。

覆盖：图片安检（分辨率/格式）、图片哈希查重、置信度门禁（高置信直通/低置信待核验）、
人工核验回填、真实图片无引擎时的诚实回答。
运行：python tests/ocr_test.py
"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from opc_fin_agent import db
from opc_fin_agent.ocr import (OcrRejected, demo_ocr_output, image_info,
                               make_demo_png, process_image_document,
                               verify_ocr_document)


def main():
    tmp = Path(tempfile.mkdtemp()) / "ocr.db"
    conn = db.get_conn(tmp)
    failed = []

    # 用例1：清晰演示票 → 高置信度直通，规则校验通过，等级A归档
    img1 = make_demo_png(width=800, height=600)
    r1 = process_image_document(conn, "Demo_清晰.png", img1,
                               demo_ocr_output("clear"))
    ok = (r1["status"] == "ok" and r1["advice"]["grade"] == "A"
          and r1["doc_id"] is not None)
    print(f"[用例1] 清晰票直通       -> status={r1['status']} grade={r1['advice']['grade']}  {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case1")

    # 用例2：模糊演示票（票号置信度0.62）→ 待核验，绝不定级
    img2 = make_demo_png(width=640, height=480)
    r2 = process_image_document(conn, "Demo_模糊.png", img2,
                               demo_ocr_output("blur"))
    ok = (r2["status"] == "pending_verification"
          and "invoice_no" in r2["low_confidence_fields"])
    print(f"[用例2] 低置信待核验     -> status={r2['status']} 低置信字段={r2['low_confidence_fields']}  {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case2")

    # 用例3：同一图片重复上传 → 图片哈希查重拦截
    r3 = process_image_document(conn, "Demo_清晰.png", img1,
                                demo_ocr_output("clear"))
    ok = r3["status"] == "blocked"
    print(f"[用例3] 图片查重拦截     -> status={r3['status']}  {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case3")

    # 用例4：人工核验回填 → 字段以人工值为准，重新过规则，等级A
    verified = {
        "invoice_no": "24312000000055500002", "issue_date": "2026-09-18",
        "seller_name": "Demo_Supplier_03", "seller_tax_no": "91310000DEMO3333CCC",
        "buyer_name": "瀚钦科技有限公司", "buyer_tax_no": "91310000DEMO000000X",
        "amount": 880.00, "tax": 8.80, "total": 888.80, "category": "通讯费",
    }
    r4 = verify_ocr_document(conn, r2["doc_id"], verified, "财务专员")
    row = conn.execute("SELECT grade, status FROM documents WHERE id=?",
                       (r2["doc_id"],)).fetchone()
    ok = r4["advice"]["grade"] == "A" and row["status"] == "pending"
    n = conn.execute(
        "SELECT COUNT(*) c FROM audit_events WHERE action='OCR_HUMAN_VERIFY'"
    ).fetchone()["c"]
    print(f"[用例4] 人工核验回填     -> grade={r4['advice']['grade']} status={row['status']} 审计{n}条  {'PASS' if ok and n == 1 else 'FAIL'}")
    if not (ok and n == 1):
        failed.append("case4")

    # 用例5：分辨率过低 → 拒绝并提示重拍
    img5 = make_demo_png(width=300, height=200)
    r5 = process_image_document(conn, "Demo_小图.png", img5,
                                demo_ocr_output("clear"))
    ok = r5["status"] == "rejected" and "重新拍照" in r5["steps"][-1][2]
    print(f"[用例5] 低分辨率拒绝     -> status={r5['status']} 提示={r5['steps'][-1][2][:30]}…  {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case5")

    # 用例6：非图片内容 → 格式安检拒绝
    try:
        image_info(b"not an image at all")
        ok = False
    except OcrRejected:
        ok = True
    print(f"[用例6] 非图片格式拒绝   -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case6")

    # 用例7：真实图片（无OCR引擎输出）→ 诚实回答引擎未接入，不编造字段
    img7 = make_demo_png(width=900, height=700)
    r7 = process_image_document(conn, "真实照片.png", img7, None)
    ok = r7["status"] == "engine_not_configured"
    print(f"[用例7] 无引擎诚实回答   -> status={r7['status']}  {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case7")

    # 用例8：审计链——OCR全过程事件齐全
    actions = {r["action"] for r in
               conn.execute("SELECT action FROM audit_events").fetchall()}
    need = {"OCR_ARCHIVE", "OCR_PENDING", "OCR_HUMAN_VERIFY",
            "BLOCK_DUPLICATE_IMG", "REJECT_RESOLUTION"}
    ok = need <= actions
    print(f"[用例8] 审计事件齐全     -> {'PASS' if ok else 'FAIL'}  {sorted(actions)}")
    if not ok:
        failed.append("case8")

    conn.close()
    if failed:
        print(f"结果：{len(failed)} 个用例失败 -> {failed}")
        sys.exit(1)
    print("结果：8/8 全部通过")


if __name__ == "__main__":
    main()
