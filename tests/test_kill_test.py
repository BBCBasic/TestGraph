from pathlib import Path
import json

from benchmarks.kill_test import (
    BenchmarkCase,
    Decision,
    build_report,
    evaluate_results,
    kill_verdict,
    load_cases,
    score_case,
    summarize,
)


def test_score_case_accepts_exact_and_allowed_type():
    case = BenchmarkCase(
        id="c1",
        observation="red Ford Fiesta",
        category="easy",
        expected_type="car",
        acceptable_types=("automobile",),
        trap=None,
    )
    assert score_case(case, Decision(type="car"))["correct"] is True
    assert score_case(case, Decision(type="automobile"))["correct"] is True
    assert score_case(case, Decision(type="red car"))["correct"] is False


def test_summarize_counts_wrong_canonical_and_false_consensus():
    rows = [
        {"correct": True, "canonical": True, "consensus": True, "operational_failure": False},
        {"correct": False, "canonical": True, "consensus": True, "operational_failure": False},
        {"correct": False, "canonical": False, "consensus": False, "operational_failure": True},
    ]
    summary = summarize(rows)
    assert summary["total"] == 3
    assert summary["correct_rate"] == 1 / 3
    assert summary["wrong_canonical_rate"] == 1 / 2
    assert summary["false_consensus_rate"] == 1 / 2
    assert summary["operational_failure_rate"] == 1 / 3


def test_kill_verdict_requires_half_wrong_canonical_rate():
    simple = {"wrong_canonical_rate": 0.08}
    tg_pass = {"wrong_canonical_rate": 0.04}
    tg_fail = {"wrong_canonical_rate": 0.041}
    assert kill_verdict(simple, tg_pass)["verdict"] == "CONTINUE"
    assert kill_verdict(simple, tg_fail)["verdict"] == "ARCHIVE"


def test_kill_verdict_handles_zero_baseline():
    verdict = kill_verdict({"wrong_canonical_rate": 0.0}, {"wrong_canonical_rate": 0.0})
    assert verdict["verdict"] == "ARCHIVE"
    assert verdict["reason"] == "baseline_has_no_wrong_canonicalisations"


def test_load_cases_rejects_duplicate_ids(tmp_path: Path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([
        {"id": "x", "observation": "a", "category": "easy", "expected_type": "car"},
        {"id": "x", "observation": "b", "category": "easy", "expected_type": "book"},
    ]), encoding="utf-8")
    try:
        load_cases(path)
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()
    else:
        raise AssertionError("expected duplicate-id validation failure")


def test_load_cases_accepts_compact_corpus(tmp_path: Path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"version": 1, "cases": [
        ["x", "red car", "easy", "car", ["automobile"], "modifier_as_type"]
    ]}), encoding="utf-8")
    cases = load_cases(path)
    assert cases == [BenchmarkCase("x", "red car", "easy", "car", ("automobile",), "modifier_as_type")]


def test_evaluate_results_groups_by_regime_and_accumulates_calls():
    cases = [BenchmarkCase("c1", "red car", "easy", "car"), BenchmarkCase("c2", "book", "easy", "book")]
    records = [
        {"case_id": "c1", "regime": "simple", "type": "car", "canonical": True, "consensus": True, "model_calls": 2, "resolver_calls": 0},
        {"case_id": "c2", "regime": "simple", "type": "book", "canonical": True, "consensus": True, "model_calls": 2, "resolver_calls": 0},
        {"case_id": "c1", "regime": "testgraph", "type": "car", "canonical": True, "consensus": True, "model_calls": 2, "resolver_calls": 0},
        {"case_id": "c2", "regime": "testgraph", "type": "novel", "canonical": True, "consensus": False, "model_calls": 3, "resolver_calls": 1},
    ]
    report = evaluate_results(cases, records)
    assert report["simple"]["correct_rate"] == 1.0
    assert report["simple"]["model_calls"] == 4
    assert report["testgraph"]["correct_rate"] == 0.5
    assert report["testgraph"]["resolver_calls"] == 1


def test_evaluate_results_rejects_unknown_case():
    cases = [BenchmarkCase("c1", "red car", "easy", "car")]
    try:
        evaluate_results(cases, [{"case_id": "missing", "regime": "simple", "type": "car"}])
    except ValueError as exc:
        assert "unknown benchmark case" in str(exc).lower()
    else:
        raise AssertionError("expected unknown case failure")


def test_build_report_includes_kill_verdict():
    report = build_report({
        "simple": {"wrong_canonical_rate": 0.10},
        "testgraph": {"wrong_canonical_rate": 0.04},
    })
    assert report["kill_test"]["verdict"] == "CONTINUE"
    assert report["kill_test"]["relative_reduction"] == 0.6
