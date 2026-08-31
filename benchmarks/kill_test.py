from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Iterable


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    observation: str
    category: str
    expected_type: str
    acceptable_types: tuple[str, ...] = ()
    trap: str | None = None


@dataclass(frozen=True)
class Decision:
    type: str | None


def _norm(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def score_case(case: BenchmarkCase, decision: Decision) -> dict:
    allowed = {_norm(case.expected_type), *(_norm(v) for v in case.acceptable_types)}
    return {"correct": _norm(decision.type) in allowed}


def summarize(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    total = len(rows)
    correct = sum(bool(r.get("correct")) for r in rows)
    canonical_rows = [r for r in rows if r.get("canonical")]
    consensus_rows = [r for r in rows if r.get("consensus")]
    wrong_canonical = sum(not r.get("correct") for r in canonical_rows)
    false_consensus = sum(not r.get("correct") for r in consensus_rows)
    operational_failures = sum(bool(r.get("operational_failure")) for r in rows)

    def rate(n: int, d: int) -> float:
        return n / d if d else 0.0

    return {
        "total": total,
        "correct_rate": rate(correct, total),
        "wrong_canonical_rate": rate(wrong_canonical, len(canonical_rows)),
        "false_consensus_rate": rate(false_consensus, len(consensus_rows)),
        "operational_failure_rate": rate(operational_failures, total),
    }


def kill_verdict(simple_summary: dict, testgraph_summary: dict, reduction_target: float = 0.5) -> dict:
    baseline = float(simple_summary.get("wrong_canonical_rate", 0.0))
    candidate = float(testgraph_summary.get("wrong_canonical_rate", 0.0))
    if baseline <= 0:
        return {"verdict": "ARCHIVE", "reason": "baseline_has_no_wrong_canonicalisations"}
    reduction = (baseline - candidate) / baseline
    return {
        "verdict": "CONTINUE" if reduction >= reduction_target else "ARCHIVE",
        "reason": "wrong_canonicalisation_reduction",
        "relative_reduction": reduction,
        "target": reduction_target,
    }


def load_cases(path: str | Path) -> list[BenchmarkCase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    compact = isinstance(raw, dict) and "cases" in raw
    items = raw["cases"] if compact else raw
    seen: set[str] = set()
    cases: list[BenchmarkCase] = []
    for item in items:
        if compact:
            case_id, observation, category, expected_type, acceptable_types, trap = item
        else:
            case_id = item["id"]
            observation = item["observation"]
            category = item["category"]
            expected_type = item["expected_type"]
            acceptable_types = item.get("acceptable_types", ())
            trap = item.get("trap")
        case_id = str(case_id)
        if case_id in seen:
            raise ValueError(f"duplicate benchmark case id: {case_id}")
        seen.add(case_id)
        cases.append(BenchmarkCase(
            id=case_id,
            observation=str(observation),
            category=str(category),
            expected_type=str(expected_type),
            acceptable_types=tuple(acceptable_types or ()),
            trap=trap,
        ))
    return cases


def evaluate_results(cases: Iterable[BenchmarkCase], records: Iterable[dict]) -> dict[str, dict]:
    case_map = {case.id: case for case in cases}
    grouped: dict[str, list[dict]] = {}
    counters: dict[str, dict[str, float]] = {}

    for record in records:
        case_id = str(record["case_id"])
        if case_id not in case_map:
            raise ValueError(f"unknown benchmark case: {case_id}")
        regime = str(record["regime"])
        scored = score_case(case_map[case_id], Decision(type=record.get("type")))
        scored.update({
            "canonical": bool(record.get("canonical", False)),
            "consensus": bool(record.get("consensus", False)),
            "operational_failure": bool(record.get("operational_failure", False)),
        })
        grouped.setdefault(regime, []).append(scored)
        counter = counters.setdefault(regime, {"model_calls": 0, "resolver_calls": 0, "cost_usd": 0.0})
        counter["model_calls"] += int(record.get("model_calls", 0))
        counter["resolver_calls"] += int(record.get("resolver_calls", 0))
        counter["cost_usd"] += float(record.get("cost_usd", 0.0))

    report: dict[str, dict] = {}
    for regime, rows in grouped.items():
        summary = summarize(rows)
        summary.update(counters[regime])
        report[regime] = summary
    return report


def build_report(regime_summaries: dict[str, dict]) -> dict:
    report = {"regimes": regime_summaries}
    simple = regime_summaries.get("simple")
    testgraph = regime_summaries.get("testgraph")
    if simple is not None and testgraph is not None:
        report["kill_test"] = kill_verdict(simple, testgraph)
    return report
