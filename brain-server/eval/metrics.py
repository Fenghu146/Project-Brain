from __future__ import annotations

from typing import Any


def compute_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        return {"total": 0}

    intent_ok = sum(1 for r in results if r.get("intent_ok"))
    refusal_ok = sum(1 for r in results if r.get("refusal_ok", True))
    clar_ok = sum(1 for r in results if r.get("clar_ok", True))
    noise_ok = sum(1 for r in results if r.get("noise_ok", True))
    coverage_ok = sum(1 for r in results if r.get("coverage_ok", True))

    return {
        "total": total,
        "intent_accuracy": round(intent_ok / total, 3),
        "refusal_precision": round(refusal_ok / total, 3),
        "clarification_ok": round(clar_ok / total, 3),
        "noise_ok": round(noise_ok / total, 3),
        "coverage_ok": round(coverage_ok / total, 3),
        "failures": [r for r in results if not r.get("passed", True)],
    }
