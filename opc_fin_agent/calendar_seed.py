# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""征期日历种子数据：源自《MVP需求清单》财税周期任务模板，可配置化。"""
import datetime as dt

TASKS = [
    {"id": "M01", "cycle": "每月", "name": "增值税申报",
     "deadline_rule": "每月15日前（征期顺延以税务机关公告为准）", "lead_days": 7,
     "agent_action": "提前推送待办卡片并备好销项/进项台账与申报表草稿"},
    {"id": "M02", "cycle": "每月", "name": "个人所得税扣缴申报",
     "deadline_rule": "每月15日前", "lead_days": 7,
     "agent_action": "推送工资表核对清单"},
    {"id": "M03", "cycle": "每月", "name": "银行流水下载导入与对账",
     "deadline_rule": "每月初5个工作日内（人工下载-受控导入通道A）", "lead_days": 3,
     "agent_action": "生成对账异常清单供人工处理"},
    {"id": "Q01", "cycle": "每季", "name": "企业所得税预缴申报",
     "deadline_rule": "季度终了后15日内", "lead_days": 10,
     "agent_action": "备好季度利润表草稿与勾稽预检结果"},
    {"id": "Q02", "cycle": "每季", "name": "记账凭证抽查复核",
     "deadline_rule": "季度结账前", "lead_days": 7,
     "agent_action": "输出抽查清单供财务负责人复核"},
]


def next_deadline(cycle):
    """按周期推算下一个截止日。真实征期以税务机关公告为准，本函数为演示实现。"""
    today = dt.date.today()
    if cycle == "每月":
        d = dt.date(today.year, today.month, 15)
        if d < today:
            ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            d = dt.date(ny, nm, 15)
        return d
    if cycle == "每季":
        qm = ((today.month - 1) // 3) * 3 + 4
        ny, nm = (today.year + 1, qm - 12) if qm > 12 else (today.year, qm)
        return dt.date(ny, nm, 15)
    return today


def get_calendar():
    """输出日历视图：截止日 + 提醒日 + 智能体动作（全部为L0/L1，提交由人执行）。"""
    out = []
    for t in TASKS:
        dl = next_deadline(t["cycle"])
        out.append({
            **t,
            "next_deadline": dl.isoformat(),
            "remind_on": (dl - dt.timedelta(days=t["lead_days"])).isoformat(),
            "human_action": "由财务人员登录电子税务局/网银执行申报或付款（L2）",
        })
    return out
