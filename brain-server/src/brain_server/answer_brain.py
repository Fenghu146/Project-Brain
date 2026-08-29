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


class AnswerBrain:
    """
    AnswerBrain is the core module for evidence-grounded answering.
    
    Implements:
    - Intent classification and source policy
    - Relevance gating and fact aggregation
    - Evidence coverage and confidence calculation
    - Pollution detection and filtering
    """

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
    ) -> AnswerResult:
        """Main entry point for answering questions."""
        from .db import get_connection, init_db
        from .repository import get_links
        
        conn = get_connection(self.db_path)
        init_db(self.db_path)
        try:
            # Step 1: Classify intent
            intent, policy = classify_intent(question)
            
            # Step 2: Search for candidates
            from .search import FTSProvider
            provider = FTSProvider()
            results = provider.search(
                conn, project_id, question,
                scope=scope or policy.get("preferred_types"),
                limit=limit * 2,
            )
            match_mode = "fts" if results else "none"
            
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
            
            # Step 4: Build key points with provenance
            key_points = self._build_key_points(conn, gated_results, project_id)
            
            # Step 5: Calculate confidence
            breakdown = self._compute_confidence(conn, gated_results, key_points, project_id)
            
            # Step 6: Compose answer
            answer = self._compose_answer(key_points, intent)
            
            # Step 7: Gather evidence
            evidence_list = self._gather_evidence(conn, gated_results, project_id)
            
            # Step 8: Gather related context
            related = self._gather_related_context(results, gated_results)
            
            # Step 9: Gather uncertainties
            uncertainties = self._gather_uncertainties(gated_results, key_points, breakdown)
            
            # Step 10: Check next actions
            next_action = self._check_next_action(gated_results, key_points, breakdown)
            
            # Step 11: Build provenance
            provenance = self._build_provenance(key_points, evidence_list)
            
            result = AnswerResult(
                answer=answer,
                key_points=key_points,
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

    def _relevance_gate(
        self,
        candidates: list[dict[str, Any]],
        policy: dict[str, Any],
        question: str,
    ) -> list[dict[str, Any]]:
        """Apply relevance gate to filter candidates."""
        from .repository import get_memory
        
        gated: list[dict[str, Any]] = []
        preferred_types = set(policy.get("preferred_types", []))
        exclude_types = set(policy.get("exclude_types", []))
        include_history = policy.get("include_history", False)
        
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
        """Build key points with provenance."""
        from .repository import get_links
        
        key_points = []
        
        for c in results:
            row = c.get("row", {})
            score = c.get("score", 0)
            text = content_to_text(row.get("content", {}))
            
            links = get_links(conn, from_id=row["id"], project_id=project_id)
            evidence_ids = [lk["to_id"] for lk in links if lk["relation"] == "evidence_of"]
            source_ids = [lk["to_id"] for lk in links if lk["relation"] == "source_of"]
            
            # Determine support level
            if evidence_ids:
                support = "direct"
            elif score > 0.7:
                support = "indirect"
            elif score > 0.5:
                support = "weak"
            else:
                support = "none"
            
            source_class = derive_source_class(
                row.get("type", "knowledge"),
                row.get("status", "draft"),
                bool(evidence_ids),
            )
            
            pollution_tags = self._detect_pollution(row, text)
            is_stale = bool(row.get("valid_until") and self._is_stale(row["valid_until"]))
            conflicts = [lk for lk in links if lk["relation"] == "conflicts_with"]
            is_conflicted = len(conflicts) > 0
            
            key_point = KeyPoint(
                text=text[:200],
                source_ids=[row["id"]] + source_ids,
                evidence_ids=evidence_ids,
                support=support,
                source_class=source_class,
                pollution_tags=pollution_tags,
                is_stale=is_stale,
                is_conflicted=is_conflicted,
            )
            key_points.append(key_point)
        
        return key_points

    def _detect_pollution(self, row: dict[str, Any], text: str) -> list[PollutionTag]:
        """Detect knowledge pollution in content."""
        tags: list[PollutionTag] = []
        
        # Code blocks
        if "```" in text or text.startswith("def ") or text.startswith("class "):
            tags.append("code_block")
        
        # Version plans
        if any(p in text for p in ["v0.6", "v0.5", "v0.4", "第六版", "第五版"]):
            tags.append("version_plan")
        
        # Test summaries
        if any(p in text.lower() for p in ["test passed", "测试通过", "全部通过", "all green"]):
            tags.append("test_summary")
        
        # Examples
        if any(p in text for p in ["example", "示例", "演示", "demo"]):
            tags.append("example_only")
        
        return tags

    def _compute_confidence(
        self,
        conn: Any,
        results: list[dict[str, Any]],
        key_points: list[KeyPoint],
        project_id: str,
    ) -> ConfidenceBreakdown:
        """Compute confidence breakdown."""
        from .repository import get_links
        
        if not key_points:
            return ConfidenceBreakdown()
        
        # Retrieval confidence
        scores = [c.get("score", 0) for c in results]
        retrieval_conf = sum(scores) / len(scores) if scores else 0
        
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
        
        # Evidence coverage
        evidenced = sum(1 for kp in key_points if kp.evidence_ids)
        evidence_cov = evidenced / len(key_points) if key_points else 0
        
        # Freshness
        fresh_count = sum(1 for kp in key_points if not kp.is_stale)
        freshness = fresh_count / len(key_points) if key_points else 0
        
        # Conflict penalty
        conflicted = sum(1 for kp in key_points if kp.is_conflicted)
        conflict_pen = conflicted * 0.1
        
        return ConfidenceBreakdown(
            retrieval_confidence=round(retrieval_conf, 2),
            source_confidence=round(source_conf, 2),
            evidence_coverage=round(evidence_cov, 2),
            freshness=round(freshness, 2),
            conflict_penalty=round(conflict_pen, 2),
        )

    def _compose_answer(self, key_points: list[KeyPoint], intent: IntentType) -> str:
        """Compose the final answer text."""
        if not key_points:
            return "未找到足够相关的已确认记录。"
        
        # Filter out polluted key points for main answer
        clean_points = [kp for kp in key_points if "version_plan" not in kp.pollution_tags]
        
        if not clean_points:
            return "未找到当前有效的记录，仅有历史或版本规划内容。"
        
        # Build answer from high-quality points
        answer_parts = []
        for kp in clean_points[:3]:
            if kp.support in ("direct", "indirect"):
                answer_parts.append(kp.text)
        
        if answer_parts:
            return "；".join(answer_parts)
        
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
        if breakdown.evidence_coverage < 0.5 and key_points:
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
        """Build provenance trace."""
        provenance = []
        
        for kp in key_points:
            entry = {
                "key_point": kp.text[:100],
                "source_ids": kp.source_ids,
                "evidence_ids": kp.evidence_ids,
                "support": kp.support,
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
    )
