# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""OCR图片票据接入层（L1能力，对应阶段0技术规格接口IF-04适配点）。

四条设计约束：
1. 本地闭环：不调用任何外部OCR API，票据图片不出内网；
2. 引擎适配器：MVP提供DemoOcrEngine（确定性演示输出），生产环境由本地引擎
   （如PaddleOCR）实现同一接口，接入点在 ENGINE_REGISTRY 注册即可；
3. 置信度门禁：任一必填字段置信度<0.90即进人工核验队列（pending_verification），
   绝不给低置信度字段硬定级——宁可慢一步，不给一个看起来对的错值；
4. 分辨率安检：图片宽度<400px直接拒绝并提示重拍。
"""
import datetime as dt
import hashlib
import json
import struct
import zlib

from .db import record_event
from .rules import run_rules
from .pipeline import build_advice
from .settings import ARCHIVE_DIR, BASE_DIR, load_master_data

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_WIDTH = 400
CONF_THRESHOLD = 0.90


class OcrRejected(Exception):
    """安检拒绝（格式/大小/分辨率），不归档。"""


class OcrNotConfigured(Exception):
    """无可用本地OCR引擎（真实图片无法识别时的诚实回答）。"""


# ---------------------------------------------------------------- 图片安检

def _png_size(data):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise OcrRejected("PNG文件头不合法")
    w, h = struct.unpack(">II", data[16:24])
    return "png", w, h


def _jpeg_size(data):
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise OcrRejected("JPEG文件头不合法")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return "jpeg", w, h
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seglen
    raise OcrRejected("JPEG结构解析失败")


def image_info(image_bytes):
    """嗅探图片格式与尺寸（不依赖第三方库）。"""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_size(image_bytes)
    if image_bytes[:2] == b"\xff\xd8":
        return _jpeg_size(image_bytes)
    raise OcrRejected("不支持的图片格式，仅支持PNG/JPEG")


# ---------------------------------------------------------------- OCR引擎

class DemoOcrEngine:
    """演示引擎：接受确定性OCR输出（模拟本地引擎识别结果），不产生随机值。

    生产环境以 PaddleOcrEngine 实现同一 recognize 接口并在
    ENGINE_REGISTRY 注册，管道代码无需改动。
    """

    name = "demo-engine-v1"

    def recognize(self, image_bytes, ocr_output):
        if not ocr_output or "fields" not in ocr_output:
            raise OcrNotConfigured(
                "本地OCR引擎未接入（PaddleOCR适配位已预留，见README），"
                "当前仅支持演示数据流")
        return {
            "engine": self.name,
            "fields": {
                k: {"value": v.get("value"), "confidence": float(v.get("confidence", 0.0))}
                for k, v in ocr_output["fields"].items()
            },
        }


ENGINE_REGISTRY = {"demo": DemoOcrEngine()}


def load_ocr_config():
    """加载OCR引擎配置（config/ocr_config.json；缺省返回空配置走demo引擎）。"""
    path = BASE_DIR / "config" / "ocr_config.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_engine(engine_key=None):
    """引擎解析：engine_key为空或'auto'时取配置文件engine项。

    paddle为惰性加载——未安装paddleocr时模块可正常导入，
    识别请求才触发OcrNotConfigured（诚实回答，附安装指引）。
    """
    if not engine_key or engine_key == "auto":
        engine_key = load_ocr_config().get("engine", "demo")
    if engine_key == "demo":
        return ENGINE_REGISTRY["demo"]
    if engine_key == "paddle":
        from .paddle_engine import PaddleOcrEngine  # 惰性导入
        return PaddleOcrEngine(load_ocr_config().get("paddle", {}))
    raise KeyError(f"未知OCR引擎：{engine_key}（支持demo/paddle/auto）")


def engine_status():
    """引擎状态探针（部署检查表用）：配置项 + paddleocr是否已安装。"""
    installed = False
    try:
        import paddleocr  # noqa: F401
        installed = True
    except Exception:
        installed = False
    return {
        "configured": load_ocr_config().get("engine", "demo"),
        "paddle_installed": installed,
        "hint": "paddle已安装且engine=paddle时，图片票据走真实本地识别" if installed
        else "paddle未安装，真实图片走engine_not_configured诚实回退；演示数据流可用",
    }


# ---------------------------------------------------------------- 主流程

REQUIRED_OCR_FIELDS = [
    "invoice_no", "issue_date", "seller_name", "buyer_name",
    "buyer_tax_no", "amount", "tax", "total",
]


def process_image_document(conn, file_name, image_bytes, ocr_output=None,
                           actor="demo_user", engine_key=None):
    """OCR图片主流程：图片安检→查重→OCR识别→置信度门禁→规则校验→归档/待核验。

    engine_key=None/'auto'时按config/ocr_config.json的engine配置解析
    （部署PaddleOCR后改配置即启用，代码零改动）。
    """
    steps = []

    def step(name, passed, msg):
        steps.append((name, bool(passed), msg))

    # 1. 图片安检
    if len(image_bytes) > MAX_IMAGE_BYTES:
        record_event(conn, None, actor, "REJECT_SECURITY", "图片超过5MB上限")
        step("图片安检", False, "图片超过5MB上限")
        return {"status": "rejected", "steps": steps}
    try:
        fmt, width, height = image_info(image_bytes)
    except OcrRejected as e:
        record_event(conn, None, actor, "REJECT_SECURITY", str(e))
        step("图片安检", False, str(e))
        return {"status": "rejected", "steps": steps}
    if width < MIN_WIDTH:
        msg = f"分辨率过低（宽{width}px<{MIN_WIDTH}px），请重新拍照：拍正、拍清、全票入框"
        record_event(conn, None, actor, "REJECT_RESOLUTION", msg)
        step("图片安检", False, msg)
        return {"status": "rejected", "steps": steps}
    step("图片安检", True, f"{fmt.upper()} {width}x{height}，{len(image_bytes)}字节")

    # 2. 查重（图片字节哈希，防同一张照片重复入账）
    img_hash = hashlib.sha256(image_bytes).hexdigest()
    row = conn.execute(
        "SELECT id FROM documents WHERE content_hash=?", (img_hash,)).fetchone()
    if row:
        msg = f"图片已上传过（文档#{row['id']}），拒绝重复入账"
        record_event(conn, None, actor, "BLOCK_DUPLICATE_IMG", msg)
        step("查重", False, msg)
        return {"status": "blocked", "steps": steps, "content_hash": img_hash}
    step("查重", True, "通过（图片哈希唯一）")

    # 3. OCR识别（引擎按配置解析：demo演示流 / paddle本地引擎）
    try:
        ocr = get_engine(engine_key).recognize(image_bytes, ocr_output)
    except OcrNotConfigured as e:
        step("OCR识别", False, str(e))
        return {"status": "engine_not_configured", "steps": steps,
                "content_hash": img_hash}
    confs = {k: ocr["fields"].get(k, {"value": None, "confidence": 0.0})
             for k in REQUIRED_OCR_FIELDS}
    avg_conf = sum(c["confidence"] for c in confs.values()) / len(confs)
    step("OCR识别", True, f"引擎 {ocr['engine']}，平均置信度 {avg_conf:.2f}")

    # 4. 置信度门禁
    low = [k for k, c in confs.items() if c["confidence"] < CONF_THRESHOLD]
    fields_with_conf = {k: c for k, c in ocr["fields"].items()}
    payload = {
        "doc_type": "image_invoice",
        "file_name": file_name,
        "image_format": fmt, "image_width": width, "image_height": height,
        "image_hash": img_hash, "ocr_engine": ocr["engine"],
        "ocr_fields": {k: c for k, c in ocr["fields"].items()},
    }
    # 归档原件（图片不可变保存）
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / f"{img_hash}.{fmt}"
    if not archive_path.exists():
        archive_path.write_bytes(image_bytes)

    if low:
        msg = (f"低置信度字段：{', '.join(low)}（阈值{CONF_THRESHOLD}），"
               "转人工核验，不给不确定的字段定级")
        step("置信度门禁", False, msg)
        cur = conn.execute(
            "INSERT INTO documents"
            " (invoice_no, content_hash, archive_path, payload, fields, grade,"
            "  status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (confs["invoice_no"]["value"], img_hash, str(archive_path),
             json.dumps(payload, ensure_ascii=False),
             json.dumps(fields_with_conf, ensure_ascii=False), None,
             "pending_verification",
             dt.datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        doc_id = cur.lastrowid
        record_event(conn, doc_id, actor, "OCR_PENDING", msg)
        return {"status": "pending_verification", "doc_id": doc_id,
                "steps": steps, "fields": fields_with_conf,
                "low_confidence_fields": low, "content_hash": img_hash,
                "archive_path": str(archive_path)}

    step("置信度门禁", True, f"全部必填字段≥{CONF_THRESHOLD}")

    # 5. 规则校验（复用R01-R06，规则优先）
    fields = {k: confs[k]["value"] for k in REQUIRED_OCR_FIELDS}
    fields["category"] = ocr["fields"].get("category", {}).get("value", "")
    fields["seller_tax_no"] = ocr["fields"].get("seller_tax_no", {}).get("value", "")
    payload["invoice"] = fields
    rule_results = run_rules(fields, load_master_data())
    failed = "; ".join(r["message"] for r in rule_results if not r["passed"]) or "全部通过"
    step("规则校验", all(r["passed"] for r in rule_results), failed)

    advice = build_advice(fields, rule_results, {"blocked": False, "reason": ""})
    cur = conn.execute(
        "INSERT INTO documents"
        " (invoice_no, content_hash, archive_path, payload, fields, grade, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (fields["invoice_no"], img_hash, str(archive_path),
         json.dumps(payload, ensure_ascii=False),
         json.dumps(fields, ensure_ascii=False), advice["grade"],
         dt.datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    doc_id = cur.lastrowid
    record_event(conn, doc_id, actor, "OCR_ARCHIVE",
                 f"grade={advice['grade']} avg_conf={avg_conf:.2f}")
    return {"status": "ok", "doc_id": doc_id, "fields": fields,
            "fields_with_conf": fields_with_conf, "advice": advice,
            "steps": steps, "content_hash": img_hash,
            "archive_path": str(archive_path)}


def verify_ocr_document(conn, doc_id, invoice, actor="demo_user"):
    """人工核验回填：财务人员核对/修正OCR字段后提交，字段自此以人工值为准。"""
    row = conn.execute(
        "SELECT id, payload, content_hash FROM documents WHERE id=?",
        (doc_id,)).fetchone()
    if not row:
        raise KeyError("文档不存在")
    payload = json.loads(row["payload"])
    payload["human_verified_invoice"] = invoice
    rule_results = run_rules(invoice, load_master_data())
    advice = build_advice(invoice, rule_results, {"blocked": False, "reason": ""})
    conn.execute(
        "UPDATE documents SET invoice_no=?, payload=?, fields=?, grade=?,"
        " status='pending' WHERE id=?",
        (str(invoice.get("invoice_no", "")),
         json.dumps(payload, ensure_ascii=False),
         json.dumps(invoice, ensure_ascii=False), advice["grade"], doc_id),
    )
    conn.commit()
    record_event(conn, doc_id, actor, "OCR_HUMAN_VERIFY",
                 "人工核验回填，字段以人工值为准，grade=" + advice["grade"])
    return {"doc_id": doc_id, "advice": advice, "rule_results": rule_results,
            "status": "pending"}


# ---------------------------------------------------------------- 演示数据

def make_demo_png(width=800, height=600, rgb=(245, 247, 250)):
    """生成演示用PNG（纯Python实现，无第三方依赖；仅用于演示与测试数据流）。"""
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(b"".join([scanline] * height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def demo_ocr_output(quality="clear"):
    """演示OCR输出：clear=高置信度全通过；blur=票号低置信度触发待核验。"""
    if quality == "clear":
        return {
            "fields": {
                "invoice_no": {"value": "24312000000055500001", "confidence": 0.98},
                "issue_date": {"value": "2026-09-18", "confidence": 0.97},
                "seller_name": {"value": "Demo_Supplier_02", "confidence": 0.99},
                "seller_tax_no": {"value": "91310000DEMO2222BBB", "confidence": 0.96},
                "buyer_name": {"value": "瀚钦科技有限公司", "confidence": 0.99},
                "buyer_tax_no": {"value": "91310000DEMO000000X", "confidence": 0.99},
                "amount": {"value": 2600.00, "confidence": 0.98},
                "tax": {"value": 26.00, "confidence": 0.98},
                "total": {"value": 2626.00, "confidence": 0.98},
                "category": {"value": "办公用品", "confidence": 0.95},
            },
        }
    return {
        "fields": {
            "invoice_no": {"value": "24312000000055?0002", "confidence": 0.62},
            "issue_date": {"value": "2026-09-18", "confidence": 0.93},
            "seller_name": {"value": "Demo_Supplier_03", "confidence": 0.94},
            "seller_tax_no": {"value": "91310000DEMO3333CCC", "confidence": 0.91},
            "buyer_name": {"value": "瀚钦科技有限公司", "confidence": 0.95},
            "buyer_tax_no": {"value": "91310000DEMO000000X", "confidence": 0.95},
            "amount": {"value": 880.00, "confidence": 0.97},
            "tax": {"value": 8.80, "confidence": 0.97},
            "total": {"value": 888.80, "confidence": 0.97},
            "category": {"value": "通讯费", "confidence": 0.92},
        },
    }
