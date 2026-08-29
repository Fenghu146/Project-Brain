from __future__ import annotations

from typing import Any

from .answer_models import IntentType, SourceClass
from .models import VALID_MEMORY_TYPES

# Keyword patterns for intent classification
INTENT_PATTERNS: list[tuple[str, list[str], dict[str, Any]]] = [
    (
        "project_goal",
        ["核心目标", "项目目标", "主要目的", "目的是什么", "这个项目做什么", "项目是什么", "主要功能", "核心功能"],
        {"preferred_types": ["identity", "state"], "exclude_types": ["task", "event"], "include_history": False, "prioritize_evidence": False, "default_length": "short"},
    ),
    (
        "current_state",
        ["当前状态", "现在进展", "做到哪了", "进行到什么", "当前进度", "正在", "当前任务", "当前阻塞", "进展"],
        {"preferred_types": ["state", "task"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "medium"},
    ),
    (
        "feature_summary",
        ["包含哪些功能", "有哪些特性", "功能列表", "支持什么", "实现了什么", "功能概览", "版本包含"],
        {"preferred_types": ["knowledge", "decision", "state"], "exclude_types": ["event", "task"], "include_history": False, "prioritize_evidence": False, "default_length": "medium"},
    ),
    (
        "mechanism_explanation",
        ["如何实现", "工作原理", "怎么工作", "机制是什么", "实现原理", "如何支持", "工作流", "工作流程"],
        {"preferred_types": ["knowledge", "decision", "evidence"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "medium"},
    ),
    (
        "decision_reason",
        ["为什么选择", "决策原因", "为什么用", "为什么这样", "决策依据", "为什么决定"],
        {"preferred_types": ["decision", "experience", "evidence"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "short"},
    ),
    (
        "failure_experience",
        ["失败经验", "遇到过什么", "踩过的坑", "哪里出问题", "哪些失败", "已知失败"],
        {"preferred_types": ["experience", "evidence"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "short"},
    ),
    (
        "evidence_trace",
        ["证据路径", "证据支持", "如何验证", "证据在哪里", "证据链", "支持证据"],
        {"preferred_types": ["evidence", "links", "events"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "medium"},
    ),
    (
        "version_history",
        ["v0.", "版本历史", "哪个版本", "版本包含", "历史版本", "之前版本"],
        {"preferred_types": ["knowledge", "decision", "state"], "exclude_types": [], "include_history": True, "prioritize_evidence": False, "default_length": "medium"},
    ),
    (
        "task_next_step",
        ["下一步", "接下来", "待办", "未完成", "剩余任务", "阻塞任务", "推荐步骤"],
        {"preferred_types": ["task", "state"], "exclude_types": [], "include_history": False, "prioritize_evidence": False, "default_length": "short"},
    ),
    (
        "test_result",
        ["测试结果", "测试通过", "测试失败", "测试覆盖", "测试状态"],
        {"preferred_types": ["evidence"], "exclude_types": [], "include_history": False, "prioritize_evidence": True, "default_length": "short"},
    ),
    (
        "file_or_module_lookup",
        ["文件在哪", "模块在哪", "源码位置", "代码文件", "实现文件"],
        {"preferred_types": ["knowledge"], "exclude_types": [], "include_history": False, "prioritize_evidence": False, "default_length": "short"},
    ),
]


def classify_intent(question: str) -> tuple[IntentType, dict[str, Any]]:
    """Classify question intent using keyword matching with fallback."""
    q_lower = question.lower()
    
    for intent, keywords, policy in INTENT_PATTERNS:
        if any(kw in q_lower for kw in keywords):
            return intent, policy
    
    # Fallback to generic_search
    return "generic_search", {
        "preferred_types": list(VALID_MEMORY_TYPES),
        "exclude_types": [],
        "include_history": False,
        "prioritize_evidence": False,
        "default_length": "medium",
    }


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
