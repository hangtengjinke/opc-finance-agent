# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""规则引擎：全部为确定性代码校验，不依赖模型判断（平台原则：规则优先）。"""
import datetime as dt
import re

REQUIRED_FIELDS = [
    "invoice_no", "issue_date", "seller_name", "buyer_name",
    "buyer_tax_no", "amount", "tax", "total",
]

ALLOWED_CATEGORIES = ["办公用品", "差旅费", "房租", "咨询费", "会议费", "通讯费"]


def complete_fields(fields):
    """关键字段完整性检查。"""
    return all(fields.get(k) not in (None, "") for k in REQUIRED_FIELDS)


def run_rules(fields, master_data):
    """执行规则集 R01-R06，返回逐条结果。规则版本变更须经财务负责人审签。"""
    company = master_data["company"]
    results = []

    def add(rule_id, name, passed, message, severity):
        results.append({
            "rule_id": rule_id, "name": name, "passed": passed,
            "message": "通过" if passed else message, "severity": severity,
        })

    add("R01", "购方名称与企业主数据一致",
        fields.get("buyer_name") == company["name"],
        f"购方名称不匹配：{fields.get('buyer_name')!r} != {company['name']!r}", "hard")
    add("R02", "购方税号与企业主数据一致",
        fields.get("buyer_tax_no") == company["tax_no"],
        "购方税号不匹配", "hard")
    try:
        ok = abs(float(fields.get("total"))
                 - (float(fields.get("amount")) + float(fields.get("tax")))) <= 0.01
    except (TypeError, ValueError):
        ok = False
    add("R03", "价税合计勾稽（合计=金额+税额）", ok, "价税合计不勾稽", "hard")
    try:
        d = dt.date.fromisoformat(fields.get("issue_date"))
        est = dt.date.fromisoformat(company["established_date"])
        ok = est <= d <= dt.date.today()
    except (TypeError, ValueError):
        ok = False
    add("R04", "开票日期在有效区间", ok,
        "开票日期早于公司成立日或晚于当前日期", "hard")
    add("R05", "数电票号码格式（20位数字）",
        bool(re.fullmatch(r"\d{20}", fields.get("invoice_no", ""))),
        "票号非20位数字", "hard")
    add("R06", "费用类别在允许清单内",
        fields.get("category") in ALLOWED_CATEGORIES,
        f"费用类别不在清单内：{fields.get('category')!r}", "soft")
    return results
