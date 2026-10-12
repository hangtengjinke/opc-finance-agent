# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""PaddleOCR本地识别引擎适配器（生产引擎，与DemoOcrEngine实现同一recognize接口）。

四条设计约束（对齐阶段0技术规格IF-04与README生产化路线）：
1. 本地闭环：Paddle推理仅在本地服务器运行，票据图片不出内网；
2. 惰性加载：paddleocr未安装时本模块可被正常导入，识别请求才触发安装检查；
3. 诚实回答：引擎未安装/初始化失败时抛OcrNotConfigured并给出安装指引，
   绝不返回编造字段——与"无来源拒答"同一原则；
4. 字段锚定：OCR文本行→发票字段采用"企业主数据优先+关键词正则锚定"
   （extract_invoice_fields，纯函数可离线单测）。未锚定到的字段
   value=None/confidence=0.0，交由置信度门禁统一转人工核验。

部署：见 deploy/OCR引擎部署指南.md；安装完成后把 config/ocr_config.json
的 engine 改为 "paddle" 即启用，管道与API代码零改动。
"""
import io
import re

from .ocr import OcrNotConfigured
from .rules import ALLOWED_CATEGORIES
from .settings import load_master_data

INSTALL_HINT = (
    "本地OCR引擎PaddleOCR未安装。请在部署服务器上执行："
    "pip install -r deploy/requirements-ocr.txt"
    "（详细步骤与离线模型方案见 deploy/OCR引擎部署指南.md），"
    "安装完成后将 config/ocr_config.json 的 engine 改为 paddle 即可启用。")

ALL_FIELD_KEYS = (
    "invoice_no", "issue_date", "seller_name", "seller_tax_no",
    "buyer_name", "buyer_tax_no", "amount", "tax", "total", "category",
)

# 税号/统一社会信用代码：15-20位数字字母组合，前后不能再有数字字母
# （兼容历史18位税号与演示数据19位；配合_is_tax_id排除纯数字票号行）
_TAX_ID_PAT = re.compile(r"(?<![0-9A-Z])[0-9A-Z]{15,20}(?![0-9A-Z])")


def _is_tax_id(s):
    """税号须含字母——排除纯数字串（如20位发票号码）被误判为税号。"""
    return any(c.isalpha() for c in s)
_INVOICE_NO_PAT = re.compile(r"(?<!\d)\d{20}(?!\d)")
_DATE_PAT = re.compile(r"(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})")
_MONEY_PAT = re.compile(r"[¥￥]?\s*([\d,]+(?:\.\d+)?)(?!\d)")


def _norm(s):
    """压掉全部空白字符，便于OCR噪声容错。"""
    return re.sub(r"\s+", "", s or "")


def _num(s):
    return float(str(s).replace(",", "").replace("￥", "").replace("¥", ""))


def extract_invoice_fields(lines, master_data=None):
    """OCR文本行 → 发票字段映射（企业主数据优先 + 关键词正则锚定）。

    lines: [(text, confidence), ...]，按版面自上而下；
    未锚定到的字段 value=None / confidence=0.0，由置信度门禁统一转人工，
    本函数绝不猜测或填充"看起来对"的值。
    """
    md = master_data or load_master_data()
    fields = {k: {"value": None, "confidence": 0.0} for k in ALL_FIELD_KEYS}

    company = _norm(md.get("company", {}).get("name", ""))
    buyer_tax_no = _norm(md.get("company", {}).get("tax_no", ""))

    for text, conf in lines:
        t = _norm(text)

        # 1) 20位发票号码
        if fields["invoice_no"]["value"] is None:
            m = _INVOICE_NO_PAT.search(t)
            if m:
                fields["invoice_no"] = {"value": m.group(), "confidence": conf}

        # 2) 开票日期 → 归一化为 YYYY-MM-DD
        if fields["issue_date"]["value"] is None:
            m = _DATE_PAT.search(t)
            if m:
                fields["issue_date"] = {
                    "value": "%04d-%02d-%02d" % (
                        int(m.group(1)), int(m.group(2)), int(m.group(3))),
                    "confidence": conf,
                }

        # 3) 购买方：企业主数据锚定（最可靠，规则引擎R01/R02同源）
        if (company and fields["buyer_name"]["value"] is None
                and company in t):
            fields["buyer_name"] = {
                "value": md["company"]["name"], "confidence": conf}
        if (buyer_tax_no and fields["buyer_tax_no"]["value"] is None
                and buyer_tax_no in t):
            fields["buyer_tax_no"] = {"value": buyer_tax_no, "confidence": conf}

        # 4) 价税合计（小写）→ total
        if fields["total"]["value"] is None and ("价税合计" in t or "小写" in t):
            m = re.search(r"[¥￥]([\d,]+(?:\.\d+)?)", t)
            if m:
                fields["total"] = {"value": _num(m.group(1)), "confidence": conf}

        # 5) 税额 → tax：关键词"税额"之后的第一个金额
        if fields["tax"]["value"] is None and "税额" in t:
            m = _MONEY_PAT.search(t, t.find("税额") + 2)
            if m and m.group(1):
                fields["tax"] = {"value": _num(m.group(1)), "confidence": conf}

        # 6) 金额 → amount：关键词"金额"之后的第一个金额（跳过"税额"字样）
        if fields["amount"]["value"] is None and "金额" in t:
            idx = t.find("金额")
            m = _MONEY_PAT.search(t, idx + 2)
            if m and m.group(1):
                fields["amount"] = {"value": _num(m.group(1)), "confidence": conf}

        # 7) 销售方税号：标识串锚定，排除购买方税号与纯数字串（票号）
        if fields["seller_tax_no"]["value"] is None:
            for m in _TAX_ID_PAT.finditer(t):
                v = m.group()
                if not _is_tax_id(v):
                    continue
                if buyer_tax_no and v == buyer_tax_no:
                    continue
                fields["seller_tax_no"] = {"value": v, "confidence": conf}
                break

        # 8) 销售方名称：以"名称"锚定。行前缀含"购/买方"跳过；
        #    候选等于本企业名跳过（防止购买方行抢注）。
        if fields["seller_name"]["value"] is None and "名称" in t:
            prefix = t[:t.find("名称")]
            if "购" not in prefix and "买方" not in prefix:
                m = re.search(r"名称[:：]?(.+)", t)
                if m:
                    cand = m.group(1).strip("：:，,。;")
                    if cand and (not company or company not in cand):
                        fields["seller_name"] = {
                            "value": cand, "confidence": conf}

        # 9) 费用类别：规则引擎允许清单逐项锚定（与R06同源）
        if fields["category"]["value"] is None:
            for cat in ALLOWED_CATEGORIES:
                if cat in text or cat in t:
                    fields["category"] = {"value": cat, "confidence": conf}
                    break

    # 后置补算：amount 缺失但 total/tax 已锚定 → 差额计算，置信取二者低值
    if (fields["amount"]["value"] is None
            and fields["total"]["value"] is not None
            and fields["tax"]["value"] is not None):
        c = min(fields["total"]["confidence"], fields["tax"]["confidence"])
        fields["amount"] = {
            "value": round(fields["total"]["value"] - fields["tax"]["value"], 2),
            "confidence": c,
        }
    return fields


class PaddleOcrEngine:
    """PaddleOCR本地引擎（生产通道）。识别结果为文本行+置信度，
    字段映射统一走 extract_invoice_fields，与管道置信度门禁无缝衔接。"""

    name = "paddleocr-local"

    def __init__(self, options=None):
        self.options = dict(options or {})
        self._engine = None

    # ------------------------------------------------ 引擎惰性加载

    def _load(self):
        if self._engine is not None:
            return self._engine
        try:
            from paddleocr import PaddleOCR
        except Exception:
            raise OcrNotConfigured(INSTALL_HINT)
        opts = dict(self.options)
        opts.pop("enabled", None)
        opts.setdefault("lang", "ch")
        try:
            self._engine = PaddleOCR(**opts)
        except TypeError:
            # PaddleOCR大版本参数签名差异 → 降级为最小参数集再试
            self._engine = PaddleOCR(lang=opts.get("lang", "ch"))
        return self._engine

    # ------------------------------------------------ 识别

    def recognize(self, image_bytes, ocr_output=None):
        # 与DemoOcrEngine一致：允许外部注入确定性结构化输出（部署自检/回归用）
        if ocr_output and "fields" in ocr_output:
            return {
                "engine": self.name,
                "fields": {
                    k: {"value": v.get("value"),
                        "confidence": float(v.get("confidence", 0.0))}
                    for k, v in ocr_output["fields"].items()
                },
            }
        engine = self._load()
        fp = io.BytesIO(image_bytes)
        try:
            result = engine.ocr(fp, cls=True)          # PaddleOCR 2.x
        except TypeError:
            result = engine.predict(fp)                 # PaddleOCR 3.x
        lines = self._flatten(result)
        if not lines:
            raise OcrNotConfigured(
                "本地OCR引擎未识别到任何文本行，请检查图片清晰度后重新上传")
        fields = extract_invoice_fields(lines, load_master_data())
        return {"engine": self.name, "fields": fields}

    # ------------------------------------------------ 结果归一

    @staticmethod
    def _flatten(result):
        """把不同版本的PaddleOCR返回结构统一为 [(text, confidence), ...]。"""
        lines = []

        def from_pairs(pairs):
            for item in pairs:
                try:
                    _box, txt = item
                except (ValueError, TypeError):
                    continue
                if isinstance(txt, (tuple, list)) and len(txt) == 2:
                    text, conf = str(txt[0]), float(txt[1])
                else:
                    text, conf = str(txt), 0.9
                if text:
                    lines.append((text, conf))

        if not result:
            return lines
        # 形态A（2.x）：[ [box, (text, conf)], ... ] 或外层多包一页
        if isinstance(result, (list, tuple)):
            probe = result[0] if result else None
            if (isinstance(probe, (list, tuple)) and len(probe) == 2
                    and isinstance(probe[1], (tuple, list))
                    and len(probe[1]) == 2):
                from_pairs(result)
            elif isinstance(probe, (list, tuple)) and probe:
                from_pairs(probe if isinstance(probe[0], (list, tuple)) else [])
        # 形态B（3.x）：预测对象/dict，含 rec_texts / rec_scores
        if not lines:
            for res in result:
                data = getattr(res, "json", None)
                if not isinstance(data, dict):
                    data = res if isinstance(res, dict) else {}
                texts = data.get("rec_texts") or []
                scores = data.get("rec_scores") or []
                for i, t in enumerate(texts):
                    conf = float(scores[i]) if i < len(scores) else 0.9
                    if t:
                        lines.append((str(t), conf))
        return lines
