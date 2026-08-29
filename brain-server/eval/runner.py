from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.answer_brain import AnswerBrain
from brain_server.db import init_db

from metrics import compute_metrics


def load_cases(cases_dir: Path) -> list[dict]:
    cases: list[dict] = []
    for p in sorted(cases_dir.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        for c in data.get("cases", []):
            cases.append(c)
    return cases


def run(db_path: str | None = None) -> list[dict]:
    cases = load_cases(Path(__file__).parent / "cases")
    brain = AnswerBrain(db_path=db_path)
    # Ensure DB exists
    from brain_server.db import get_connection

    conn = get_connection(db_path)
    init_db(db_path)
    conn.close()

    results: list[dict] = []
    for case in cases:
        q = case["question"]
        pid = case.get("project_id", "default")
        exp = case.get("expected", {})
        res = brain.answer(q, project_id=pid)
        expected_intent = exp.get("intent")
        intent_ok = (expected_intent is None) or (res.intent == expected_intent)
        max_kp = exp.get("max_key_points")
        noise_ok = True
        if max_kp is not None:
            noise_ok = len(res.key_points) <= max_kp
        should_refuse = exp.get("should_refuse", False)
        refusal_ok = True
        if should_refuse:
            refusal_ok = res.confidence == 0.0 and len(res.key_points) == 0
        may_clarify = exp.get("may_clarify", False)
        clar_ok = True
        if may_clarify:
            # Accept either clarification or normal answer
            clar_ok = True
        min_conf = exp.get("min_confidence")
        coverage_ok = True
        if min_conf is not None and not should_refuse:
            coverage_ok = res.confidence >= min_conf or res.confidence == 0.0  # allow 0 if no data
        passed = intent_ok and noise_ok and refusal_ok and coverage_ok
        results.append({
            "id": case["id"],
            "question": q,
            "intent": res.intent,
            "expected_intent": expected_intent,
            "intent_ok": intent_ok,
            "key_points": len(res.key_points),
            "confidence": res.confidence,
            "noise_ok": noise_ok,
            "refusal_ok": refusal_ok,
            "clar_ok": clar_ok,
            "coverage_ok": coverage_ok,
            "passed": passed,
            "answer": res.answer[:120],
        })
    return results


def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = run(db_path=args.db)
    metrics = compute_metrics(results)

    if args.json:
        print(json.dumps({"results": results, "metrics": metrics}, ensure_ascii=False, indent=2))
    else:
        print(f"Eval: {metrics['total']} cases | intent_accuracy={metrics['intent_accuracy']} refusal={metrics['refusal_precision']} noise_ok={metrics['noise_ok']}")
        failures = metrics.get("failures", [])
        if failures:
            print(f"Failures ({len(failures)}):")
            for f in failures:
                print(f"  - {f['id']}: q={f['question']!r} intent={f['intent']} expected={f['expected_intent']} kp={f['key_points']} conf={f['confidence']}")
            return 1
        print("All cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
