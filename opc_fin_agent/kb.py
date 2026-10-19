# -*- coding: utf-8 -*-
# OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""合规问答（RAG检索层）：BM25真检索 + 审签门禁 + 无来源拒答 + C级守门。

四条硬规则（对齐总体方案第五节与阶段0技术规格）：
1. 审签门禁：知识库文档无reviewed_by（签字人）或status!=approved的，一律不索引——
   "没有审核人签字不进库"是硬编码，不是流程约定；
2. 无来源拒答：检索最高分低于阈值或命中词条不足，一律回答"证据不足，请提交
   人工复核"，不允许任何模型自由发挥；
3. C级守门：问题命中涉税判断类敏感模式（优惠/筹划/免税/入账处理等），即便检索
   高分也不给结论，只附参考来源转专业人员；
4. 回答四要素：结论/依据/适用条件/来源（含发布日期与版本），逐条可追溯。

检索实现：纯Python中文双字（bigram）BM25，无第三方依赖；Ollama本地大模型
回答适配器见 kb_llm.py（answer_mode=llm时启用，宕机自动回退抽取式）。
知识库源文件：config/kb_docs/*.json（一手来源+审签元数据，D3纪律演示占位）。
"""
import json
import math
import re
from collections import Counter

from .settings import BASE_DIR

KB_DOCS_DIR = BASE_DIR / "config" / "kb_docs"
KB_CONFIG_PATH = BASE_DIR / "config" / "kb_config.json"

# 涉税判断类敏感模式：C级，只转人工不给结论（即便检索高分）
C_GUARD_PATTERNS = (
    "优惠", "筹划", "免税", "合理避税", "怎么入账", "如何入账",
    "资本化", "能不能享受", "能否享受",
)

_HAN_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD = re.compile(r"[a-z0-9]+")
_K1, _B = 1.5, 0.75


def tokenize(text):
    """中文双字bigram + ASCII词（纯Python，无分词依赖）。"""
    text = (text or "").lower()
    tokens = _ASCII_WORD.findall(text)
    for run in _HAN_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def load_kb_config():
    if not KB_CONFIG_PATH.exists():
        return {}
    with open(KB_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_kb_documents(kb_dir=None):
    """加载知识库文档并执行审签门禁：无签字/非approved一律不索引。"""
    root = kb_dir or KB_DOCS_DIR
    docs, excluded = [], []
    if not root.exists():
        return docs, excluded
    for path in sorted(root.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            excluded.append({"file": path.name, "reason": "文件解析失败"})
            continue
        if doc.get("status") != "approved" or not doc.get("reviewed_by"):
            excluded.append({
                "file": path.name,
                "reason": "未审签（无reviewed_by或status!=approved），不进检索",
            })
            continue
        docs.append(doc)
    return docs, excluded


def build_segments(docs):
    """文档→检索段落，段落文本=question+tags+answer+basis+condition。"""
    segments = []
    for doc in docs:
        for seg in doc.get("segments", []):
            text = " ".join(
                [seg.get("question", ""), " ".join(seg.get("tags", [])),
                 seg.get("answer", ""), seg.get("basis", ""),
                 seg.get("condition", "")])
            segments.append({
                "seg": seg, "doc": doc,
                "tokens": tokenize(text), "tf": Counter(tokenize(text)),
            })
    return segments


class BM25Index:
    """BM25索引（段落级）。N较小时idf截断不敏感，够用且可解释。"""

    def __init__(self, segments):
        self.segments = segments
        self.n = len(segments)
        self.df = Counter()
        for s in segments:
            for t in set(s["tokens"]):
                self.df[t] += 1
        self.avgdl = (sum(len(s["tokens"]) for s in segments) / self.n
                      if segments else 1.0)

    def search(self, query, top_k=3):
        q_tokens = tokenize(query)
        scored = []
        for s in self.segments:
            matched = [t for t in set(q_tokens) if t in s["tf"]]
            if not matched:
                continue
            score = 0.0
            dl = len(s["tokens"])
            for t in matched:
                idf = math.log((self.n - self.df[t] + 0.5)
                               / (self.df[t] + 0.5) + 1.0)
                tf = s["tf"][t]
                score += idf * (tf * (_K1 + 1)) / (
                    tf + _K1 * (1 - _B + _B * dl / self.avgdl))
            scored.append((score, len(matched), s, matched))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [{
            "segment_id": s["seg"]["id"],
            "title": s["doc"]["title"],
            "question": s["seg"]["question"],
            "score": round(score, 2),
            "matched_terms": matched,
        } for score, _n, s, matched in scored[:top_k]], q_tokens


# ---------------------------------------------------------------- 索引缓存

_INDEX_CACHE = {"key": None, "index": None}


def get_index(kb_dir=None):
    """索引缓存：按目录内容指纹重建（知识库更新后无需重启服务）。"""
    root = kb_dir or KB_DOCS_DIR
    files = sorted(root.glob("*.json")) if root.exists() else []
    key = tuple((p.name, p.stat().st_mtime) for p in files)
    if _INDEX_CACHE["key"] != key or kb_dir is not None:
        docs, _ = load_kb_documents(root)
        _INDEX_CACHE["index"] = BM25Index(build_segments(docs))
        if kb_dir is None:
            _INDEX_CACHE["key"] = key
    return _INDEX_CACHE["index"]


# ---------------------------------------------------------------- 问答

def search_kb(question, top_k=None, kb_dir=None):
    """检索明细（透明化接口）：评分排序+命中词，供审计与界面展示。"""
    cfg = load_kb_config()
    top_k = top_k or cfg.get("top_k", 3)
    index = get_index(kb_dir)
    return index.search(question, top_k)


def _reference(hit):
    return (f"{hit['segment_id']}《{hit['title']}》"
            f"（匹配分{hit['score']}，命中：{'/'.join(hit['matched_terms'][:6])}）")


def ask(question, kb_dir=None):
    """合规问答主入口：检索→阈值门禁→C级守门→回答四要素。

    返回与旧版字段兼容（answered/confidence/answer/source/date），
    新增basis/condition/version/score/mode等可追溯字段。
    """
    cfg = load_kb_config()
    min_score = float(cfg.get("min_score", 3.5))
    min_terms = int(cfg.get("min_match_terms", 2))
    hits, q_tokens = search_kb(question, cfg.get("top_k", 3), kb_dir)

    # C级守门：涉税判断类问题不给结论，只附参考转专业人员
    if any(p in question for p in C_GUARD_PATTERNS):
        refs = [_reference(h) for h in hits[:2]] if hits else []
        return {
            "answered": False, "confidence": "C",
            "answer": ("该问题涉及税务判断（优惠政策适用/税负筹划类），"
                       "超出知识库可作答范围，请咨询税务师或提交人工复核。"
                       + ("" if not refs else "参考来源：" + "；".join(refs))),
            "source": "", "date": "", "mode": "guard-c",
            "references": refs,
        }

    # 无来源拒答：最高分低于阈值或命中词条不足
    if (not hits or hits[0]["score"] < min_score
            or len(hits[0]["matched_terms"]) < min_terms):
        return {
            "answered": False, "confidence": "C",
            "answer": "证据不足，请提交人工复核。",
            "source": "", "date": "", "mode": "refuse-no-source",
            "references": [],
        }

    top = hits[0]
    index = get_index(kb_dir)
    seg = next(s for s in index.segments if s["seg"]["id"] == top["segment_id"])
    doc, segd = seg["doc"], seg["seg"]
    base = {
        "answered": True, "confidence": "A",
        "answer": segd["answer"],
        "basis": segd["basis"], "condition": segd["condition"],
        "source": f"{doc['title']}（{doc['source']}）",
        "date": doc["publish_date"], "version": doc["version"],
        "reviewed_by": doc["reviewed_by"],
        "segment_id": segd["id"], "score": top["score"],
        "references": [_reference(h) for h in hits],
    }

    # 回答模式：llm（本地Ollama，宕机回退）| extractive（引用原文条款）
    if cfg.get("answer_mode") == "llm":
        from .kb_llm import answer_with_llm
        out = answer_with_llm(question, seg, doc, cfg.get("llm", {}))
        if out is not None:
            base.update(out)
            base["mode"] = "llm"
            return base
        base["mode"] = "extractive-fallback"
        return base
    base["mode"] = "extractive"
    return base


def kb_inventory(kb_dir=None):
    """知识库清单：已索引文档与被门禁排除的文件（审计用）。"""
    docs, excluded = load_kb_documents(kb_dir or KB_DOCS_DIR)
    return {
        "indexed": [{
            "title": d["title"], "source": d["source"],
            "publish_date": d["publish_date"], "version": d["version"],
            "reviewed_by": d["reviewed_by"],
            "segments": len(d.get("segments", [])),
        } for d in docs],
        "excluded": excluded,
    }
