from __future__ import annotations

from typing import Any

from .answer_models import (
    AnswerResult,
    ConfidenceBreakdown,
    IntentType,
    KeyPoint,
    NextAction,
    PollutionTag,
    RelatedContext,
    SourceClass,
)
from .intent_router import classify_intent, derive_source_class
from .models import content_to_text
from .search import RELEVANCE_THRESHOLD, ranked_search

# Max key points by intent (P0: limit key points)
MAX_KEY_POINTS_BY_INTENT: dict[str, int] = {
    "project_goal": 2,
    "current_state": 3,
    "decision_reason": 4,
    "mechanism_explanation": 5,
    "feature_summary": 6,
    "evidence_trace": 4,
    "version_history": 8,
    "task_next_step": 3,
    "test_result": 3,
    "failure_experience": 4,
    "file_or_module_lookup": 3,
    "generic_narrow": 3,
    "generic_broad": 5,
}

# Memory type field extractors (P1: extract specific fields)
FIELD_EXTRACTORS: dict[str, list[str]] = {
    "identity": ["purpose", "name", "principles", "constraints", "architecture"],
    "state": ["current_goal", "phase", "blockers", "open_questions", "recent_changes"],
    "decision": ["decision", "reason", "alternatives_considered", "verified"],
    "knowledge": ["content", "text"],
    "experience": ["lesson", "result", "reason", "conditions", "attempt"],
    "task": ["title", "remaining", "next_step", "status"],
    "evidence": ["type", "source", "description", "result"],
}


def _extract_memory_field(mem_type: str, content: Any, max_fields: int = 3) -> str:
    """Extract relevant fields from memory content based on type."""
    if not isinstance(content, dict):
        return content_to_text(content)[:200]
    
    fields = FIELD_EXTRACTORS.get(mem_type, ["content", "text"])
    parts = []
    
    for field in fields[:max_fields]:
        if field in content:
            val = content[field]
            if isinstance(val, str) and val.strip():
                parts.append(f"{field}: {val}")
            elif isinstance(val, (list, tuple)) and val:
                parts.append(f"{field}: {', '.join(str(v) for v in val[:3])}")
    
    if parts:
        return "; ".join(parts)[:200]
    
    # Fallback to full content
    return content_to_text(content)[:200]


class AnswerBrain:
    """
    AnswerBrain is the core module for evidence-grounded answering.
    
    Implements:
    - Intent classification and source policy
    - Relevance gating and fact aggregation
    - Evidence coverage and confidence calculation
    - Pollution detection and filtering
    """
    
    # P0: Version metadata
    schema_version = "4"
    brain_runtime_version = "0.6"
    workflow_version = "0.5"
    answer_version = "0.6"
    capability_schema_version = "4"

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path

    def answer(
        self,
        question: str,
        project_id: str = "default",
        agent_id: str = "system",
        session_id: str | None = None,
        scope: list[str] | None = None,
        limit: int = 8,
        include_proposals: bool = False,
        as_of_commit: str | None = None,
        as_of_time: str | None = None,
        token_budget: int | None = None,
        output_level: str = "compact",
        answer_mode: str = "standard",
    ) -> AnswerResult:
        """Main entry point for answering questions."""
        from .db import get_connection, init_db
        from .repository import get_links
        
        conn = get_connection(self.db_path)
        init_db(self.db_path)
        try:
            # Step 1: Classify intent
            intent, policy = classify_intent(question)
            
            # Determine mode based on answer_mode
            if answer_mode == "concise":
                max_kp = min(policy.get("max_key_points", 3), 3)
            elif answer_mode == "detailed":
                max_kp = policy.get("max_key_points", 8)
            else:  # standard
                max_kp = policy.get("max_key_points", 5)
            
            # Step 2: Search for candidates
            from .search import FTSProvider
            provider = FTSProvider()
            results = provider.search(
                conn, project_id, question,
                scope=scope or policy.get("preferred_types"),
                limit=limit * 2,
            )
            
            # Track actual match mode from results
            match_modes = set()
            for r in results:
                mm = r.get("match_mode", "fts")
                match_modes.add(mm)
            if match_modes == {"fts"}:
                match_mode = "fts"
            elif match_modes == {"like_fallback"}:
                match_mode = "like_fallback"
            elif len(match_modes) > 1:
                match_mode = "mixed"
            else:
                match_mode = "none"
            
            # Build matches list for compatibility
            matches = [
                {
                    "id": r["row"]["id"],
                    "score": r.get("score", 0),
                    "matched_terms": r.get("matched_terms", []),
                    "match_mode": r.get("match_mode", match_mode),
                }
                for r in results
            ]
            
            # Step 3: Apply relevance gate
            gated_results = self._relevance_gate(results, policy, question)
            
            # Step 4: Apply max_key_points limit (P0)
            gated_results = gated_results[:max_kp]
            
            # Step 5: Build key points with provenance
            key_points = self._build_key_points(conn, gated_results, project_id)
            
            # Step 6: Filter polluted key points for main answer (P1)
            clean_points = self._filter_pollution(key_points, intent)
            
            # Step 7: Calculate confidence with breakdown (P2)
            breakdown = self._compute_confidence(conn, gated_results, clean_points, project_id)
            
            # Step 8: Compose answer with mode-aware length (P1)
            answer = self._compose_answer(clean_points, intent, answer_mode)
            
            # Step 9: Gather evidence
            evidence_list = self._gather_evidence(conn, gated_results, project_id)
            
            # Step 10: Gather related context
            related = self._gather_related_context(results, gated_results)
            
            # Step 11: Gather uncertainties
            uncertainties = self._gather_uncertainties(gated_results, clean_points, breakdown)
            
            # Step 12: Check next actions
            next_action = self._check_next_action(gated_results, clean_points, breakdown)
            
            # Step 13: Build provenance
            provenance = self._build_provenance(clean_points, evidence_list)
            
            result = AnswerResult(
                answer=answer,
                key_points=clean_points,
                facts=[],
                evidence=evidence_list,
                related_context=related,
                uncertainties=uncertainties,
                next_action=next_action,
                intent=intent,
                match_mode=match_mode,
                confidence=breakdown.to_final(),
                confidence_breakdown=breakdown,
                provenance=provenance,
                matches=matches,
            )
            
            return result
        finally:
            conn.close()

    def _filter_pollution(self, key_points: list[KeyPoint], intent: str) -> list[KeyPoint]:
        """
        Filter key points based on pollution tags.
        
        P1: Strict pollution handling
        - documentation_example: never in main answer
        - example_only: only in related_context
        - code_block: only for file/module questions
        - version_plan: only for version history questions
        - test_summary: merge to evidence
        - historical: only for history questions
        """
        filtered = []
        
        for kp in key_points:
            tags = kp.pollution_tags
            
            # Skip documentation examples entirely
            if "documentation_example" in tags:
                continue
            
            # Skip examples unless explicitly asked
            if "example_only" in tags and "example" not in intent.lower():
                continue
            
            # Allow code blocks only for file/module lookup
            if "code_block" in tags and "file_or_module_lookup" not in intent:
                continue
            
            # Allow version plans only for version history
            if "version_plan" in tags and intent != "version_history":
                continue
            
            # Mark historical content appropriately
            if "historical" in tags and not any(t in kp.text for t in ["v0.", "历史"]):
                continue
            
            filtered.append(kp)
        
        return filtered

    def _relevance_gate(
        self,
        candidates: list[dict[str, Any]],
        policy: dict[str, Any],
        question: str,
    ) -> list[dict[str, Any]]:
        """Apply relevance gate to filter candidates."""
        gated: list[dict[str, Any]] = []
        preferred_types = set(policy.get("preferred_types", []))
        exclude_types = set(policy.get("exclude_types", []))
        include_history = policy.get("include_history", False)
        generic_mode = policy.get("mode", "narrow")
        
        for c in candidates:
            row = c.get("row", {})
            mem_type = row.get("type", "")
            status = row.get("status", "")
            score = c.get("score", 0)
            
            # Skip excluded types
            if mem_type in exclude_types:
                continue
            
            # Skip deprecated/invalid for non-history queries
            if not include_history and status in ("deprecated", "invalid"):
                continue
            
            # Apply score threshold
            if score < RELEVANCE_THRESHOLD:
                continue
            
            # P0: Restrict generic_broad mode - allow more results
            # P0: Restrict generic_narrow mode - limit to 3-5 high-quality results
            if generic_mode == "narrow" and score < 0.4:
                continue
            
            # Boost for preferred types
            if preferred_types and mem_type in preferred_types:
                c["score"] = min(score * 1.2, 1.0)
            
            gated.append(c)
        
        gated.sort(key=lambda x: x.get("score", 0), reverse=True)
        return gated

    def _build_key_points(
        self,
        conn: Any,
        results: list[dict[str, Any]],
        project_id: str,
    ) -> list[KeyPoint]:
        """Build key points with provenance and field extraction (P1)."""
        from .repository import get_links
        
        key_points = []
        
        for c in results:
            row = c.get("row", {})
            score = c.get("score", 0)
            mem_type = row.get("type", "knowledge")
            
            # P1: Extract specific fields instead of full content
            text = _extract_memory_field(mem_type, row.get("content", {}))
            
            links = get_links(conn, from_id=row["id"], project_id=project_id)
            evidence_ids = [lk["to_id"] for lk in links if lk["relation"] == "evidence_of"]
            source_ids = [lk["to_id"] for lk in links if lk["relation"] == "source_of"]
            
            # Determine support level with finer granularity
            if evidence_ids:
                support = "direct"
            elif score > 0.7:
                support = "indirect"
            elif score > 0.5:
                support = "weak"
            else:
                support = "none"
            
            source_class = derive_source_class(
                mem_type,
                row.get("status", "draft"),
                bool(evidence_ids),
            )
            
            pollution_tags = self._detect_pollution(row, text)
            is_stale = bool(row.get("valid_until") and self._is_stale(row["valid_until"]))
            conflicts = [lk for lk in links if lk["relation"] == "conflicts_with"]
            is_conflicted = len(conflicts) > 0
            
            # P2: Add freshness and evidence coverage per key point
            freshness = "current"
            if is_stale:
                freshness = "stale"
            elif row.get("valid_from") and self._is_future(row["valid_from"]):
                freshness = "future"
            
            evidence_coverage = len(evidence_ids) / max(len(links), 1) if links else 0.0
            
            key_point = KeyPoint(
                text=text[:200],
                source_ids=[row["id"]] + source_ids,
                evidence_ids=evidence_ids,
                support=support,
                source_class=source_class,
                pollution_tags=pollution_tags,
                is_stale=is_stale,
                is_conflicted=is_conflicted,
                freshness=freshness,
                evidence_coverage=round(evidence_coverage, 2),
            )
            key_points.append(key_point)
        
        return key_points

    def _detect_pollution(self, row: dict[str, Any], text: str) -> list[PollutionTag]:
        """Detect knowledge pollution in content."""
        tags: list[PollutionTag] = []
        
        # Code blocks
        if "```" in text or text.startswith("def ") or text.startswith("class "):
            tags.append("code_block")
        
        # Version plans - only if it's about future versions, not historical
        if any(p in text for p in ["第六版", "第七版", "未来版本", "计划新增", "准备实现"]):
            tags.append("version_plan")
        
        # Test summaries
        if any(p in text.lower() for p in ["全部通过", "26 passed", "36 passed", "all green", "无失败"]):
            tags.append("test_summary")
        
        # Examples
        if any(p in text for p in ["example", "示例", "演示", "demo"]):
            tags.append("example_only")
        
        # Documentation examples
        if any(p in text for p in ["参见", "参考", "见文档", "类似"]):
            tags.append("documentation_example")
        
        return tags

    def _compute_confidence(
        self,
        conn: Any,
        results: list[dict[str, Any]],
        key_points: list[KeyPoint],
        project_id: str,
    ) -> ConfidenceBreakdown:
        """Compute confidence breakdown with P2 improvements."""
        from .repository import get_links
        
        if not key_points:
            return ConfidenceBreakdown()
        
        # P2: Weight by support level and evidence coverage
        direct_points = [kp for kp in key_points if kp.support == "direct"]
        indirect_points = [kp for kp in key_points if kp.support == "indirect"]
        weak_points = [kp for kp in key_points if kp.support == "weak"]
        
        # Retrieval confidence - weighted by support
        scores = [c.get("score", 0) for c in results]
        retrieval_conf = sum(scores) / len(scores) if scores else 0
        
        # Adjust by support distribution
        if direct_points:
            retrieval_conf *= 1.0  # Full weight for direct evidence
        elif indirect_points:
            retrieval_conf *= 0.8  # Slight penalty for indirect
        elif weak_points:
            retrieval_conf *= 0.6  # More penalty for weak
        
        # Source confidence
        source_scores = {
            "user_confirmed": 1.0,
            "verified_evidence": 0.95,
            "active_decision": 0.9,
            "active_knowledge": 0.85,
            "project_model": 0.8,
            "task_handover": 0.75,
            "event_observation": 0.7,
            "proposal": 0.3,
            "generated_summary": 0.5,
            "documentation_example": 0.2,
        }
        source_vals = [source_scores.get(kp.source_class, 0.5) for kp in key_points]
        source_conf = sum(source_vals) / len(source_vals) if source_vals else 0
        
        # P2: Evidence coverage weighted by support
        evidenced_direct = sum(1 for kp in key_points if kp.evidence_ids and kp.support == "direct")
        evidence_cov = evidenced_direct / len(key_points) if key_points else 0
        
        # Freshness
        fresh_count = sum(1 for kp in key_points if kp.freshness == "current")
        freshness = fresh_count / len(key_points) if key_points else 0
        
        # Conflict penalty - increased for direct conflicts
        conflicted = sum(1 for kp in key_points if kp.is_conflicted)
        conflict_pen = conflicted * 0.15  # Increased penalty
        
        return ConfidenceBreakdown(
            retrieval_confidence=round(retrieval_conf, 2),
            source_confidence=round(source_conf, 2),
            evidence_coverage=round(evidence_cov, 2),
            freshness=round(freshness, 2),
            conflict_penalty=round(conflict_pen, 2),
        )

    def _compose_answer(
        self, 
        key_points: list[KeyPoint], 
        intent: IntentType,
        answer_mode: str = "standard",
    ) -> str:
        """Compose answer with mode-aware length control (P1)."""
        if not key_points:
            return "未找到足够相关的已确认记录。"
        
        # Filter out heavily polluted points
        clean_points = [
            kp for kp in key_points 
            if "documentation_example" not in kp.pollution_tags
            and "example_only" not in kp.pollution_tags
        ]
        
        if not clean_points:
            return "未找到当前有效的记录，仅有历史或版本规划内容。"
        
        # Mode-aware composition
        if answer_mode == "concise":
            # Only direct and high-confidence indirect
            main_points = [kp for kp in clean_points if kp.support in ("direct", "indirect")][:2]
            if not main_points:
                main_points = clean_points[:1]
        elif answer_mode == "detailed":
            # Include all except weak
            main_points = [kp for kp in clean_points if kp.support != "none"][:6]
        else:  # standard
            # Direct and indirect, up to 4
            main_points = [kp for kp in clean_points if kp.support in ("direct", "indirect")]
            if len(main_points) > 4:
                main_points = main_points[:4]
        
        if main_points:
            return "；".join(kp.text for kp in main_points)
        
        return "找到相关记录但证据不足，建议补充验证。"

    def _gather_evidence(
        self,
        conn: Any,
        results: list[dict[str, Any]],
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Gather evidence for key points."""
        from .repository import get_evidence, get_links
        
        evidence_list = []
        seen_ids = set()
        
        for c in results:
            row = c.get("row", {})
            links = get_links(conn, from_id=row["id"], project_id=project_id)
            
            for lk in links:
                if lk["relation"] == "evidence_of" and lk["to_id"] not in seen_ids:
                    ev = get_evidence(conn, lk["to_id"], project_id=project_id)
                    if ev:
                        evidence_list.append(ev)
                        seen_ids.add(lk["to_id"])
        
        return evidence_list

    def _gather_related_context(
        self,
        all_results: list[dict[str, Any]],
        gated_results: list[dict[str, Any]],
    ) -> list[RelatedContext]:
        """Gather related but non-core context."""
        related = []
        gated_ids = {c.get("row", {}).get("id") or c.get("id") for c in gated_results}

        for c in all_results:
            row = c.get("row", {}) or c
            row_id = row.get("id")
            if not row_id or row_id in gated_ids:
                continue

            reason = "低相关度命中" if c.get("score", 0) < RELEVANCE_THRESHOLD else "历史版本"
            related.append(RelatedContext(
                id=row_id,
                type=row.get("type", ""),
                status=row.get("status", ""),
                reason=reason,
            ))
        return related[:5]

    def _gather_uncertainties(
        self,
        results: list[dict[str, Any]],
        key_points: list[KeyPoint],
        breakdown: ConfidenceBreakdown,
    ) -> list[str]:
        """Gather uncertainty statements."""
        uncertainties = []
        
        if not results:
            uncertainties.append("检索未命中任何候选，已应用相关度阈值过滤低相关结果。")
            return uncertainties
        
        unverified = [kp for kp in key_points if kp.support == "none"]
        if unverified:
            uncertainties.append(f"{len(unverified)} 条关键断言缺乏直接证据支持。")
        
        stale = [kp for kp in key_points if kp.is_stale]
        if stale:
            uncertainties.append(f"{len(stale)} 条记录已过期，置信度已降低。")
        
        conflicted = [kp for kp in key_points if kp.is_conflicted]
        if conflicted:
            uncertainties.append(f"{len(conflicted)} 条记录存在冲突，请人工审阅。")
        
        if breakdown.evidence_coverage < 0.5 and key_points:
            uncertainties.append("证据覆盖率较低，建议补充验证。")
        
        return uncertainties

    def _check_next_action(
        self,
        results: list[dict[str, Any]],
        key_points: list[KeyPoint],
        breakdown: ConfidenceBreakdown,
    ) -> NextAction | None:
        """Check if there are recommended next actions."""
        if breakdown.evidence_coverage < 0.3 and key_points:
            return NextAction(
                type="verify_evidence",
                reason="证据覆盖率较低，建议验证关键断言",
                target_ids=[kp.source_ids[0] for kp in key_points[:3] if kp.source_ids],
            )
        
        if not results:
            return NextAction(
                type="record_knowledge",
                reason="未找到相关记录，建议补充知识",
            )
        
        return None

    def _build_provenance(
        self,
        key_points: list[KeyPoint],
        evidence_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build provenance trace with P2 details."""
        provenance = []
        
        for kp in key_points:
            entry = {
                "key_point": kp.text[:100],
                "source_ids": kp.source_ids,
                "evidence_ids": kp.evidence_ids,
                "support": kp.support,
                "freshness": kp.freshness,
                "evidence_coverage": kp.evidence_coverage,
            }
            provenance.append(entry)
        
        return provenance

    def _is_stale(self, valid_until: str) -> bool:
        """Check if validity has passed."""
        try:
            from datetime import datetime, timezone
            vu = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            return vu < datetime.now(timezone.utc)
        except Exception:
            return False
    
    def _is_future(self, valid_from: str) -> bool:
        """Check if validity is in the future."""
        try:
            from datetime import datetime, timezone
            vf = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
            return vf > datetime.now(timezone.utc)
        except Exception:
            return False

    def to_compat_response(self, answer_result: AnswerResult) -> dict[str, Any]:
        """Convert to v0.5 compatible format."""
        return {
            "answer": answer_result.answer,
            "facts": [
                {
                    "id": kp.source_ids[0] if kp.source_ids else "",
                    "type": "",
                    "status": "",
                    "kind": "fact",
                }
                for kp in answer_result.key_points
            ],
            "evidence": answer_result.evidence,
            "uncertainties": answer_result.uncertainties,
            "confidence": answer_result.confidence,
            "match_mode": answer_result.match_mode,
            "matches": answer_result.matches,
            "schema_version": "0.6",
        }


def answer_v2(
    question: str,
    project_id: str = "default",
    agent_id: str = "system",
    session_id: str | None = None,
    scope: list[str] | None = None,
    limit: int = 8,
    include_proposals: bool = False,
    as_of_commit: str | None = None,
    as_of_time: str | None = None,
    token_budget: int | None = None,
    output_level: str = "compact",
    db_path: str | None = None,
    answer_mode: str = "standard",
) -> AnswerResult:
    """Public API for AnswerBrain."""
    brain = AnswerBrain(db_path=db_path)
    return brain.answer(
        question=question,
        project_id=project_id,
        agent_id=agent_id,
        session_id=session_id,
        scope=scope,
        limit=limit,
        include_proposals=include_proposals,
        as_of_commit=as_of_commit,
        as_of_time=as_of_time,
        token_budget=token_budget,
        output_level=output_level,
        answer_mode=answer_mode,
    )
