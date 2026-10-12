# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""合规问答（演示）：仅从已审核知识库检索，无来源即拒答（硬编码规则）。"""
KB = [
    {"keywords": ["数电票", "打印", "贴凭证"],
     "answer": "数电票采用全电子方式归档，无需打印贴凭证。数电票为税务机关签发的"
               "结构化数据文件，可电子入账并在系统内归档。",
     "source": "知识库-票据管理规范（演示条目）", "date": "2026-09-01"},
    {"keywords": ["增值税", "申报", "截止", "征期"],
     "answer": "增值税小规模纳税人按季申报，申报截止日为季度终了后15日内；"
               "具体以当期税务机关征期公告为准。",
     "source": "知识库-征期日历（演示条目）", "date": "2026-09-01"},
    {"keywords": ["报销", "发票", "附件"],
     "answer": "报销须附发票原件或数电票电子文件，经经办、审核两级签批后入账；"
               "纸质原件须登记存放位置。",
     "source": "知识库-报销制度（演示条目）", "date": "2026-09-01"},
]


def ask(question):
    """命中知识库返回带出处的答案；未命中一律拒答，不允许模型自由发挥。"""
    for item in KB:
        if any(k in question for k in item["keywords"]):
            return {"answered": True, "confidence": "A",
                    "answer": item["answer"],
                    "source": item["source"], "date": item["date"]}
    return {"answered": False, "confidence": "C",
            "answer": "证据不足，请提交人工复核。",
            "source": "", "date": ""}
