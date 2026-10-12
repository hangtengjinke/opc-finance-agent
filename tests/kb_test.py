# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""冒烟测试：RAG真检索问答层。

覆盖：BM25命中与出处四要素、近义问法召回、无关问题拒答、C级守门转人工、
审签门禁（未签文档不索引）、阈值拒答、检索明细、LLM适配器诚实回退。
运行：python tests/kb_test.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from opc_fin_agent.kb import (ask, get_index, kb_inventory, load_kb_config,
                              search_kb)
from opc_fin_agent.kb_llm import answer_with_llm


def main():
    failed = []

    # 用例1：数电票问题 → 命中并带出处四要素
    r1 = ask("数电票需要打印出来贴凭证吗")
    ok = (r1["answered"] is True and r1["confidence"] == "A"
          and "数电票归档规范" in r1["source"]
          and r1.get("basis") and r1.get("condition") and r1.get("version"))
    print(f"[用例1] 命中带出处四要素   -> A级 score={r1.get('score')} {'PASS' if ok else 'FAIL ' + str(r1)}")
    if not ok:
        failed.append("case1")

    # 用例2：近义问法召回（未出现原文关键词，靠双字索引）
    r2 = ask("发票扫描之后纸质原件还需要保留吗")
    ok = (r2["answered"] is True
          and r2["segment_id"] == "KB-ET-02"
          and r2["source"].startswith("数电票归档规范"))
    print(f"[用例2] 近义问法召回       -> {r2.get('segment_id')} score={r2.get('score')} {'PASS' if ok else 'FAIL ' + str(r2)}")
    if not ok:
        failed.append("case2")

    # 用例3：无关问题 → 无来源拒答（硬规则，不允许自由发挥）
    r3 = ask("今天中午吃什么好")
    ok = (r3["answered"] is False and "证据不足" in r3["answer"]
          and r3["mode"] == "refuse-no-source")
    print(f"[用例3] 无关问题拒答       -> {r3['mode']} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case3")

    # 用例4：C级守门——涉税判断类问题不给结论，转专业人员并附参考
    r4 = ask("我们公司能不能享受增值税优惠政策")
    ok = (r4["answered"] is False and r4["confidence"] == "C"
          and "税务判断" in r4["answer"] and r4["mode"] == "guard-c")
    print(f"[用例4] C级守门转人工      -> {r4['mode']} 参考数={len(r4['references'])} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case4")

    # 用例5：审签门禁——未审签文档不进索引（草稿条目检索不到）
    inv = kb_inventory()
    seg_ids = {s["seg"]["id"] for s in get_index().segments}
    excluded_ok = any("05_未审签示例" in e["file"]
                      and "未审签" in e["reason"] for e in inv["excluded"])
    hits5, _ = search_kb("税负优化方案建议")
    ok = excluded_ok and "KB-PLAN-01" not in seg_ids and not hits5
    print(f"[用例5] 审签门禁           -> 排除{len(inv['excluded'])}份 草稿检索命中={len(hits5)} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case5")

    # 用例6：检索明细接口（评分排序+命中词+段落ID）
    hits6, q_tokens = search_kb("数电票打印")
    ok = (hits6 and hits6[0]["segment_id"] == "KB-ET-01"
          and hits6[0]["score"] > 0 and hits6[0]["matched_terms"]
          and "数电" in q_tokens)
    print(f"[用例6] 检索明细           -> top1={hits6[0]['segment_id'] if hits6 else '-'} score={hits6[0]['score'] if hits6 else '-'} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case6")

    # 用例7：阈值拒答——单词条弱匹配不给答案
    r7 = ask("凭证")
    ok = r7["answered"] is False and r7["mode"] == "refuse-no-source"
    print(f"[用例7] 弱匹配阈值拒答     -> {r7['mode']} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case7")

    # 用例8：回答模式与配置解析
    cfg = load_kb_config()
    ok = (cfg.get("answer_mode") == "extractive" and "min_score" in cfg
          and r1["mode"] == "extractive")
    print(f"[用例8] 回答模式配置       -> mode={r1['mode']} min_score={cfg.get('min_score')} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case8")

    # 用例9：LLM适配器诚实回退——模型不可达返回None（调用方回退抽取式）
    seg9 = next(s for s in get_index().segments
                if s["seg"]["id"] == "KB-ET-01")
    out = answer_with_llm("数电票要打印吗", seg9, seg9["doc"],
                          {"base_url": "http://127.0.0.1:9", "model": "demo",
                           "timeout": 2})
    out2 = answer_with_llm("数电票要打印吗", seg9, seg9["doc"], {})
    ok = out is None and out2 is None
    print(f"[用例9] LLM不可达回退     -> unreachable={out is None} nomodel={out2 is None} {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed.append("case9")

    if failed:
        print(f"结果：{len(failed)} 个用例失败 -> {failed}")
        sys.exit(1)
    print("结果：9/9 全部通过")


if __name__ == "__main__":
    main()
