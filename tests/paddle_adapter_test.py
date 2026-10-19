# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""冒烟测试：PaddleOCR适配器与引擎配置层。

覆盖：OCR行→发票字段锚定（全锚定/诚实缺失）、引擎惰性加载与诚实回退、
配置文件引擎开关、引擎状态探针、demo引擎回归。
运行：python tests/paddle_adapter_test.py
（注：本机未安装paddleocr，真实识别路径在部署服务器按指南第四节验收）
"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from opc_fin_agent import db
from opc_fin_agent.ocr import (OcrNotConfigured, engine_status, get_engine,
                               load_ocr_config, make_demo_png,
                               process_image_document)
from opc_fin_agent.paddle_engine import extract_invoice_fields

# 模拟PaddleOCR对一张增值税发票版面的识别输出（文本行+置信度，自上而下）
SYNTHETIC_LINES = [
    ("24312000000077700001", 0.97),
    ("开票日期：2026年09月18日", 0.96),
    ("购买方 名称：瀚钦科技有限公司", 0.98),
    ("购买方 纳税人识别号：91310000DEMO000000X", 0.97),
    ("销售方 名称：Demo_Supplier_04", 0.95),
    ("销售方 纳税人识别号：91310000DEMO4444DDD", 0.94),
    ("合计 金额 ¥1,200.00 税额 ¥12.00", 0.96),
    ("价税合计（小写） ¥1,212.00", 0.98),
    ("办公用品", 0.93),
]


def main():
    failed = []

    # 用例1：合成OCR行 → 全部字段正确锚定（主数据+关键词+正则）
    f1 = extract_invoice_fields(SYNTHETIC_LINES)
    expect = {
        "invoice_no": "24312000000077700001", "issue_date": "2026-09-18",
        "buyer_name": "瀚钦科技有限公司", "buyer_tax_no": "91310000DEMO000000X",
        "seller_name": "Demo_Supplier_04", "seller_tax_no": "91310000DEMO4444DDD",
        "amount": 1200.0, "tax": 12.0, "total": 1212.0, "category": "办公用品",
    }
    bad = {k: (f1[k]["value"], v) for k, v in expect.items()
           if f1[k]["value"] != v}
    ok = not bad and all(f1[k]["confidence"] >= 0.9 for k in expect)
    print(f"[用例1] 字段全锚定         -> {len(expect)}项 {'PASS' if ok else 'FAIL ' + str(bad)}")
    if not ok:
        failed.append("case1")

    # 用例2：锚定缺失 → 对应字段 value=None/conf=0（诚实缺失，不猜测）
    f2 = extract_invoice_fields([("随机文本行", 0.99)])
    ok = (f2["invoice_no"]["value"] is None and f2["invoice_no"]["confidence"] == 0.0
          and f2["total"]["value"] is None and f2["category"]["value"] is None)
    print(f"[用例2] 缺锚定置零         -> invoice_no={f2['invoice_no']} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case2")

    # 用例3：税号正则不误截20位票号（前后断言）
    f3 = extract_invoice_fields([("票号24312000000077700001尾", 0.99)])
    ok = f3["invoice_no"]["value"] == "24312000000077700001" \
        and f3["seller_tax_no"]["value"] is None
    print(f"[用例3] 票号不误判税号     -> seller_tax_no={f3['seller_tax_no']['value']} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case3")

    # 用例4：价税合计缺失时 total 置零；amount差额补算
    lines4 = [(t, c) for t, c in SYNTHETIC_LINES if "价税合计" not in t]
    f4 = extract_invoice_fields(lines4)
    ok = f4["total"]["value"] is None and f4["amount"]["value"] == 1200.0
    print(f"[用例4] 合计缺失诚实置零   -> total={f4['total']['value']} amount={f4['amount']['value']} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case4")

    # 用例5：未安装paddleocr时 paddle引擎识别请求诚实抛错并附安装指引
    #        （get_engine惰性加载：构造不报错，recognize才触发安装检查）
    try:
        import paddleocr  # noqa: F401
        print("[用例5] 本机已装paddleocr  -> SKIP（部署机验收时覆盖此路径）")
    except ImportError:
        try:
            get_engine("paddle").recognize(b"", None)
            ok = False
        except OcrNotConfigured as e:
            ok = "requirements-ocr.txt" in str(e) and "paddle" in str(e)
        print(f"[用例5] 未装引擎诚实回退   -> {'PASS' if ok else 'FAIL'}")
        if not ok:
            failed.append("case5")

    # 用例6：真实图片走paddle通道（未安装）→ engine_not_configured，不落库
    conn = db.get_conn(Path(tempfile.mkdtemp()) / "pad.db")
    img = make_demo_png(width=900, height=700)
    r6 = process_image_document(conn, "真实照片.png", img, engine_key="paddle")
    n_docs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    conn.close()
    ok = r6["status"] == "engine_not_configured" and n_docs == 0
    print(f"[用例6] 管道诚实回退不落库 -> status={r6['status']} 落库={n_docs} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case6")

    # 用例7：配置文件引擎开关（默认demo，可切paddle/auto）
    cfg = load_ocr_config()
    ok = cfg.get("engine") == "demo" and "paddle" in cfg
    print(f"[用例7] 配置文件引擎开关   -> engine={cfg.get('engine')} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case7")

    # 用例8：引擎状态探针
    st = engine_status()
    ok = st["configured"] in ("demo", "paddle") and isinstance(st["paddle_installed"], bool)
    print(f"[用例8] 状态探针           -> configured={st['configured']} installed={st['paddle_installed']} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case8")

    # 用例9：get_engine('auto'/None) 按配置解析 → demo引擎回归（注入ocr_output）
    eng = get_engine("auto")
    demo_out = {"fields": {"invoice_no": {"value": "24312000000088800001", "confidence": 0.98}}}
    out = eng.recognize(b"", demo_out)
    ok = out["engine"].startswith("demo") and out["fields"]["invoice_no"]["confidence"] == 0.98
    print(f"[用例9] auto解析demo回归   -> engine={out['engine']} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case9")

    if failed:
        print(f"结果：{len(failed)} 个用例失败 -> {failed}")
        sys.exit(1)
    print("结果：9/9 全部通过")


if __name__ == "__main__":
    main()
