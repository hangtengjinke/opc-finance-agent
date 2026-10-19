# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""OCR引擎部署自检：用一张真实票据图片验证PaddleOCR端到端可用。

用法（部署服务器上，激活装好paddleocr的venv后）：
    python tools/ocr_selftest.py <图片路径>

输出：引擎状态 → 管道各步骤 → 字段锚定结果与置信度 → 最终状态/等级。
验收口径见 deploy/OCR引擎部署指南.md 第四节。
"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from opc_fin_agent import db
from opc_fin_agent.ocr import engine_status, process_image_document


def main():
    if len(sys.argv) < 2:
        print("用法：python tools/ocr_selftest.py <票据图片路径>")
        print("示例：python tools/ocr_selftest.py docs/screenshots/03_OCR图片票据接入.png")
        sys.exit(2)
    img_path = Path(sys.argv[1])
    if not img_path.exists():
        print(f"图片不存在：{img_path}")
        sys.exit(2)

    status = engine_status()
    print(f"[引擎状态] configured={status['configured']} "
          f"paddle_installed={status['paddle_installed']}")
    print(f"[提示] {status['hint']}")
    print()

    conn = db.get_conn(Path(tempfile.mkdtemp()) / "selftest.db")
    try:
        r = process_image_document(
            conn, img_path.name, img_path.read_bytes(), engine_key="paddle")
        for name, passed, msg in r["steps"]:
            print(f"  [{'OK' if passed else '--'}] {name}: {msg}")
        print(f"\n[最终状态] {r['status']}")
        if r["status"] == "ok":
            print(f"[建议等级] {r['advice']['grade']}")
            for k, v in r["fields"].items():
                print(f"    - {k} = {v}")
        elif r["status"] == "pending_verification":
            print(f"[低置信字段] {r['low_confidence_fields']}（已转人工核验，未定级）")
            for k, v in r["fields"].items():
                print(f"    - {k} = {v.get('value')!r} (conf={v.get('confidence')})")
        elif r["status"] == "engine_not_configured":
            print("[未通过] PaddleOCR未安装或初始化失败，请按部署指南第三节安装后重试")
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
