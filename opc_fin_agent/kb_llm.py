# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""本地大模型回答适配器（Ollama，answer_mode=llm时启用）。

三条纪律：
1. 仅依据检索到的条款作答——提示词强制"只允许使用资料内容，资料不足必须回答
   证据不足"，并要求逐句附出处（segment_id）；
2. 诚实回退：模型未启动/超时/返回不含出处，一律返回None由调用方回退抽取式
   回答并置fallback标志——大模型说得好不如条款引得准；
3. 本地闭环：只访问config/kb_config.json里配置的内网base_url，不出内网。

部署：见 deploy/RAG问答部署指南.md（Ollama安装与模型拉取）。
"""
import json
import urllib.request

PROMPT_TMPL = (
    "你是企业财税智能体的问答模块。只允许依据下面给出的资料回答问题，"
    "禁止使用资料以外的知识，禁止编造。回答末尾必须单独一行写明出处编号"
    "（形如：出处：KB-XX-00）。\n"
    "若资料不足以回答，必须回答：证据不足，请提交人工复核。\n\n"
    "【资料】\n出处编号：{seg_id}\n标题：{title}\n结论：{answer}\n"
    "依据：{basis}\n适用条件：{condition}\n来源：{source}（{date}，{version}）\n\n"
    "【问题】{question}\n\n【回答（不超过200字，末尾附出处编号）】"
)


def answer_with_llm(question, seg, doc, llm_cfg):
    """调用Ollama生成式回答；任何失败返回None（调用方回退抽取式）。

    seg: kb.build_segments产出的段落（含seg/doc键）；llm_cfg: kb_config的llm节。
    """
    base_url = llm_cfg.get("base_url", "http://127.0.0.1:11434")
    model = llm_cfg.get("model", "")
    timeout = int(llm_cfg.get("timeout", 30))
    if not model:
        return None
    s, d = seg["seg"], seg["doc"]
    prompt = PROMPT_TMPL.format(
        seg_id=s["id"], title=d["title"], answer=s["answer"],
        basis=s.get("basis", ""), condition=s.get("condition", ""),
        source=d["source"], date=d["publish_date"], version=d["version"],
        question=question)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0}}).encode()
    try:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    text = (out.get("response") or "").strip()
    # 守门：回答为空或不含出处编号 → 视为不合格，回退抽取式
    if not text or s["id"] not in text:
        return None
    return {"answer": text}
