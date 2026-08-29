from __future__ import annotations

from typing import Any

from .answer_models import IntentType, SourceClass
from .models import VALID_MEMORY_TYPES

# === Layer 1: Entity Recognition ===
# These are domain-specific terms that help identify context

ENTITY_PATTERNS: list[tuple[str, list[str]]] = [
    # Concurrency & locking
    ("concurrency", ["并发", "乐观锁", "revision", "冲突", "锁", "事务", "atomic"]),
    # Evidence & migration
    ("evidence_migration", ["证据", "locator", "迁移", "路径解析", "可移植", "project_relative", "git_blob"]),
    # AnswerBrain v0.6
    ("answerbrain", ["answerbrain", "意图路由", "answer_v2", "断言级", "provenance"]),
    # WorkflowBrain v0.5
    ("workflow", ["workflowbrain", "session", "observe", "handover", "自动化", "compact", "focused", "full"]),
    # FTS & Search
    ("search", ["fts", "搜索", "索引", "项目隔离", "project_isolation", "ranked_search"]),
    # Curator & Review
    ("curator", ["curator", "审阅", "proposal", "review", "机制", "审查器"]),
    # Version history
    ("version_history", ["v0.1", "v0.2", "v0.3", "v0.4", "v0.5", "v0.6", "版本历史", "演进", "之前版本"]),
]

# === Layer 2: Question Type Patterns ===
# These identify the question structure

QUESTION_TYPE_PATTERNS: list[tuple[str, list[str], dict[str, Any]]] = [
    # Feature summary questions
    (
        "feature_summary",
        ["包含哪些", "有哪些", "功能列表", "支持什么", "实现了什么", "功能概览", "特性", "能力"],
        {"preferred_types": ["knowledge", "decision", "state"], "exclude_types": ["event", "task"], "include_history": False, "prioritize_evidence": False, "default_length": "medium", "max_key_points": 6},
    ),
    # Mechanism explanation questions
    (
        "mechanism_explanation",
        ["如何实现", "工作原理", "怎么工作", "机制是什么", "实现原理", "如何支持", "工作流", "工作流程", "方案", "怎么做", "怎样实现", "如何工作"],
        {"preferred_types": ["knowledge", "decision", "evidence"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "medium", "max_key_points": 5},
    ),
    # Decision reason questions
    (
        "decision_reason",
        ["为什么选择", "决策原因", "为什么用", "为什么这样", "决策依据", "为什么决定", "选型原因"],
        {"preferred_types": ["decision", "experience", "evidence"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "short", "max_key_points": 4},
    ),
    # Evidence trace questions
    (
        "evidence_trace",
        ["证据路径", "证据支持", "如何验证", "证据在哪里", "证据链", "支持证据", "证据类型"],
        {"preferred_types": ["evidence", "knowledge"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "medium", "max_key_points": 4},
    ),
    # Version history questions
    (
        "version_history",
        ["版本历史", "哪个版本", "历史版本", "之前版本", "版本演进", "发展历程"],
        {"preferred_types": ["knowledge", "decision", "state"], "exclude_types": [], "include_history": True, "prioritize_evidence": False, "default_length": "medium", "max_key_points": 8},
    ),
    # Project goal questions
    (
        "project_goal",
        ["核心目标", "项目目标", "主要目的", "目的是什么", "这个项目做什么", "项目是什么", "主要功能", "核心功能", "定位"],
        {"preferred_types": ["identity", "state"], "exclude_types": ["task", "event"], "include_history": False, "prioritize_evidence": False, "default_length": "short", "max_key_points": 2},
    ),
    # Current state questions
    (
        "current_state",
        ["当前状态", "现在进展", "做到哪了", "进行到什么", "当前进度", "正在", "当前任务", "当前阻塞", "进展"],
        {"preferred_types": ["state", "task"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "medium", "max_key_points": 3},
    ),
    # Task next step questions
    (
        "task_next_step",
        ["下一步", "接下来", "待办", "未完成", "剩余任务", "阻塞任务", "推荐步骤"],
        {"preferred_types": ["task", "state"], "exclude_types": [], "include_history": False, "prioritize_evidence": False, "default_length": "short", "max_key_points": 3},
    ),
    # Failure experience questions
    (
        "failure_experience",
        ["失败经验", "遇到过什么", "踩过的坑", "哪里出问题", "哪些失败", "已知失败"],
        {"preferred_types": ["experience", "evidence"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "short", "max_key_points": 4},
    ),
    # Test result questions
    (
        "test_result",
        ["测试结果", "测试通过", "测试失败", "测试覆盖", "测试状态", "测试用例"],
        {"preferred_types": ["evidence"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "short", "max_key_points": 3},
    ),
]

# === Layer 3: Combination Rules ===
# These handle entity + question type combinations

COMBINATION_RULES: list[tuple[list[str], str, dict[str, Any]]] = [
    # v0.6 + AnswerBrain patterns
    (["v0.6", "answerbrain"], None, {"override_intent": None}),  # handled by question type
    # Concurrency mechanism
    (["并发", "乐观锁"], "mechanism_explanation", {"preferred_types": ["knowledge", "decision"]}),
    # Evidence migration
    (["证据", "迁移", "locator"], "evidence_trace", {"preferred_types": ["evidence", "knowledge"]}),
]


def _extract_entities(question: str) -> list[str]:
    """Extract domain entities from question."""
    q_lower = question.lower()
    entities = []
    for entity, keywords in ENTITY_PATTERNS:
        if any(kw.lower() in q_lower for kw in keywords):
            entities.append(entity)
    return entities


def _match_question_type(question: str) -> tuple[str | None, dict[str, Any]]:
    """Match question type using keyword patterns."""
    q_lower = question.lower()
    for intent, keywords, policy in QUESTION_TYPE_PATTERNS:
        if any(kw in q_lower for kw in keywords):
            return intent, policy
    return None, {}


def _apply_entity_bias(entities: list[str], base_policy: dict[str, Any]) -> dict[str, Any]:
    """Apply entity-based bias to policy."""
    policy = dict(base_policy)
    
    if "concurrency" in entities:
        policy["preferred_types"] = ["knowledge", "decision", "evidence"]
        policy["prioritize_evidence"] = True
    
    if "evidence_migration" in entities:
        policy["preferred_types"] = ["evidence", "knowledge"]
        policy["prioritize_evidence"] = True
    
    if "answerbrain" in entities:
        policy["preferred_types"] = ["knowledge", "decision"]
    
    if "workflow" in entities:
        policy["preferred_types"] = ["knowledge", "state"]
    
    return policy


def classify_intent(question: str) -> tuple[IntentType, dict[str, Any]]:
    """
    Classify question intent using three-layer analysis:
    1. Entity recognition (domain-specific terms)
    2. Question type matching (grammatical patterns)
    3. Context combination (entity + question type)
    """
    # Layer 1: Extract entities
    entities = _extract_entities(question)
    
    # Layer 2: Match question type
    matched_intent, base_policy = _match_question_type(question)
    
    # Layer 3: Apply entity bias
    if entities:
        base_policy = _apply_entity_bias(entities, base_policy)
    
    # Handle version history specially - don't trigger on "v0." alone
    # Only trigger if explicit history keywords are present
    if matched_intent == "version_history":
        # Already matched by explicit keywords
        return matched_intent, base_policy
    
    # If we have a question type match, use it
    if matched_intent:
        return matched_intent, base_policy
    
    # Check combination rules
    for entity_keywords, override_intent, rule_policy in COMBINATION_RULES:
        if any(kw in question.lower() for kw in entity_keywords):
            if override_intent:
                policy = dict(rule_policy)
                policy.update(base_policy)
                return override_intent, policy
    
    # Fallback to generic_search with narrow mode
    return "generic_search", {
        "mode": "narrow",  # default to narrow for generic searches
        "preferred_types": list(VALID_MEMORY_TYPES),
        "exclude_types": ["event", "proposal", "task"],
        "include_history": False,
        "prioritize_evidence": False,
        "default_length": "medium",
        "max_key_points": 5,  # limit for narrow mode
    }


def score_intents(question: str) -> list[tuple[IntentType, float]]:
    q_lower = question.lower()
    scored: list[tuple[IntentType, float]] = []
    for intent, keywords, _ in QUESTION_TYPE_PATTERNS:
        hits = sum(1 for kw in keywords if kw in q_lower)
        if hits:
            scored.append((intent, float(hits)))
    if not scored:
        # check entity-only
        has_entity = any(
            any(kw.lower() in q_lower for kw in kws) for _, kws in ENTITY_PATTERNS
        )
        if has_entity:
            scored.append(("generic_search", 0.3))
        else:
            scored.append(("generic_search", 0.1))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def get_source_policy(intent: IntentType) -> dict[str, Any]:
    """Get source policy for intent type."""
    _, policy = classify_intent(intent)
    return policy


def derive_source_class(mem_type: str, status: str, has_evidence: bool) -> SourceClass:
    """Derive source class from memory type and status."""
    if status in ("verified", "active") and has_evidence:
        if mem_type in ("knowledge", "state", "identity"):
            return "active_knowledge"
        if mem_type == "decision":
            return "active_decision"
        return "verified_evidence"
    if mem_type == "decision":
        return "active_decision"
    if mem_type == "task":
        return "task_handover"
    if mem_type in ("identity", "state"):
        return "project_model"
    if mem_type == "experience":
        return "event_observation"
    if status in ("verified", "active") and has_evidence:
        return "verified_evidence"
    return "active_knowledge"
