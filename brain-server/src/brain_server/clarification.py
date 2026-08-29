from __future__ import annotations

from typing import Any

from .answer_models import Clarification, IntentType
from .intent_router import ENTITY_PATTERNS, QUESTION_TYPE_PATTERNS


def score_intents(question: str) -> list[tuple[IntentType, float]]:
    q_lower = question.lower()
    scores: list[tuple[IntentType, float]] = []

    for intent, keywords, _ in QUESTION_TYPE_PATTERNS:
        hits = sum(1 for kw in keywords if kw in q_lower)
        if hits:
            scores.append((intent, float(hits)))

    # Entity-only hits get a small score toward mechanism_explanation / evidence_trace
    has_entity = any(
        any(kw.lower() in q_lower for kw in kws)
        for _, kws in ENTITY_PATTERNS
    )
    if not scores and has_entity:
        scores.append(("generic_search", 0.3))

    if not scores:
        scores.append(("generic_search", 0.1))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


_INTENT_LABELS: dict[str, str] = {
    "mechanism_explanation": "实现机制",
    "decision_reason": "决策原因",
    "failure_experience": "失败经验",
    "evidence_trace": "证据链",
    "feature_summary": "功能概览",
    "current_state": "当前状态",
    "project_goal": "项目目标",
    "version_history": "版本历史",
    "task_next_step": "下一步任务",
    "test_result": "测试结果",
}


def maybe_clarify(
    question: str,
    threshold: float = 0.15,
    min_length: int = 12,
) -> Clarification | None:
    stripped = question.strip()
    if len(stripped) >= min_length:
        return None

    # Very short / vague questions like "怎么实现？" "为什么？"
    vague_markers = ["怎么", "如何", "为什么", "当前", "这个", "可靠吗", "怎么样"]
    if not any(m in stripped for m in vague_markers):
        return None

    scores = score_intents(stripped)
    if len(scores) < 2:
        return None

    gap = scores[0][1] - scores[1][1]
    if gap >= threshold:
        return None

    # Gap is small — candidates are ambiguous
    top_two = [s[0] for s in scores[:2]]
    labels = [_INTENT_LABELS.get(i, i) for i in top_two]
    prompt = f"你想了解的是：{ ' / '.join(f'{idx+1}. {lbl}' for idx, lbl in enumerate(labels))} ？"

    return Clarification(
        needed=True,
        prompt=prompt,
        candidates=top_two,
        threshold=threshold,
    )
