"""离线检查仅验证词面主题覆盖，不代替真实模型的证据充分性判断。"""

import re

from app.retrieval.tokenizer import tokenize

STOP = set(
    "什么 怎么 可以 是否 支持 请问 呢 吗 如果 但是 已经 还能 多少 问题 一个 这个 那么 时候 查询 条件 规则 追问".split()
)


def lexical_check(query, chunks):
    query = query.replace("七天", "7天")
    text = "\n".join(c.content for c in chunks)
    terms = {
        t for t in tokenize(query) if len(t) > 1 and t not in STOP and t not in {"需要", "满足"}
    }
    terms = {t for t in terms if not re.fullmatch(r"ORD\d+", t, re.I)}
    covered = {t for t in terms if t in text}
    missing = terms - covered
    # 复合政策必须同时覆盖例外和规则；单纯“有 chunk”不能说明可回答。
    aspects = [
        word for word in ("定制", "质量", "运费", "七天", "无理由", "偏远", "退款") if word in query
    ]
    absent = [
        word for word in aspects if word not in text and not (word == "七天" and "7天" in text)
    ]
    ratio = len(covered) / len(terms) if terms else 0
    enough = bool(chunks) and ratio >= 0.65 and not absent
    return {
        "relevant": ratio > 0,
        "sufficient": enough,
        "missing_aspect": " ".join(absent or sorted(missing)),
        "rationale": f"demo_lexical_coverage={ratio:.2f};不是语义裁判",
    }
