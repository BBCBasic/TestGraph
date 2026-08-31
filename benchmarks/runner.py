from __future__ import annotations

from dataclasses import asdict
import json
from typing import Iterable

from benchmarks.kill_test import BenchmarkCase
from benchmarks.providers import ClassificationReply, ModelProvider, ModelReply, parse_classification_reply


def _norm(value: str) -> str:
    return " ".join(value.strip().lower().split())


def classification_prompt(case: BenchmarkCase) -> str:
    return (
        "Classify what the subject fundamentally IS. Descriptive modifiers such as colour, material, quantity, "
        "arrangement, state, location, or purpose should normally be attributes rather than the subject type, but "
        "legitimate compound concepts must remain distinct when the combined phrase names a materially distinct class. "
        "Do not invent details not present in the observation. Return ONLY JSON with exactly two string fields: "
        '{"type":"...","reason":"..."}.\n\n'
        f"Observation: {case.observation}"
    )


def resolver_prompt(case: BenchmarkCase, first: ClassificationReply, second: ClassificationReply) -> str:
    return (
        "Two independent classifiers disagreed. Decide the best canonical subject type from the evidence. "
        "Do not decide by majority, model identity, or confidence theatre. Apply the same semantic-head rule: "
        "descriptive modifiers are normally attributes, while genuine compound concepts may be distinct types. "
        "Return ONLY JSON with exactly two string fields: {\"type\":\"...\",\"reason\":\"...\"}.\n\n"
        f"Observation: {case.observation}\n"
        f"First decision: type={first.type!r}; reason={first.reason!r}\n"
        f"Second decision: type={second.type!r}; reason={second.reason!r}"
    )


def _audit_reply(reply: ModelReply, parsed: ClassificationReply | None = None) -> dict:
    data = reply.audit_dict()
    if parsed is not None:
        data["parsed"] = asdict(parsed)
    return data


def _failure_record(case: BenchmarkCase, regime: str, model_calls: int, resolver_calls: int = 0) -> dict:
    return {
        "case_id": case.id,
        "regime": regime,
        "type": None,
        "canonical": False,
        "consensus": False,
        "operational_failure": True,
        "model_calls": model_calls,
        "resolver_calls": resolver_calls,
        "cost_usd": 0.0,
    }


def run_single_case(case: BenchmarkCase, provider: ModelProvider) -> tuple[dict, dict]:
    audit = {"case_id": case.id, "regime": "single"}
    try:
        raw = provider.classify(classification_prompt(case))
        parsed = parse_classification_reply(raw.text)
        audit["first"] = _audit_reply(raw, parsed)
        return ({
            "case_id": case.id,
            "regime": "single",
            "type": parsed.type,
            "canonical": True,
            "consensus": False,
            "operational_failure": False,
            "model_calls": 1,
            "resolver_calls": 0,
            "cost_usd": raw.cost_usd,
        }, audit)
    except Exception as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        return _failure_record(case, "single", 1), audit


def run_simple_case(
    case: BenchmarkCase,
    first_provider: ModelProvider,
    second_provider: ModelProvider,
    resolver_provider: ModelProvider,
) -> tuple[dict, dict]:
    audit = {"case_id": case.id, "regime": "simple"}
    replies: list[ModelReply] = []
    try:
        prompt = classification_prompt(case)
        first_raw = first_provider.classify(prompt)
        replies.append(first_raw)
        first = parse_classification_reply(first_raw.text)
        audit["first"] = _audit_reply(first_raw, first)

        # The exact same prompt is sent independently; it contains no first-model answer.
        second_raw = second_provider.classify(prompt)
        replies.append(second_raw)
        second = parse_classification_reply(second_raw.text)
        audit["second"] = _audit_reply(second_raw, second)

        if _norm(first.type) == _norm(second.type):
            return ({
                "case_id": case.id,
                "regime": "simple",
                "type": first.type,
                "canonical": True,
                "consensus": True,
                "operational_failure": False,
                "model_calls": 2,
                "resolver_calls": 0,
                "cost_usd": sum(reply.cost_usd for reply in replies),
            }, audit)

        resolver_raw = resolver_provider.classify(resolver_prompt(case, first, second))
        replies.append(resolver_raw)
        resolved = parse_classification_reply(resolver_raw.text)
        audit["resolver"] = _audit_reply(resolver_raw, resolved)
        return ({
            "case_id": case.id,
            "regime": "simple",
            "type": resolved.type,
            "canonical": True,
            "consensus": False,
            "operational_failure": False,
            "model_calls": 3,
            "resolver_calls": 1,
            "cost_usd": sum(reply.cost_usd for reply in replies),
        }, audit)
    except Exception as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        return _failure_record(case, "simple", max(1, len(replies) + (0 if replies else 1))), audit


def collect_testgraph_case(
    case: BenchmarkCase,
    first_provider: ModelProvider,
    second_provider: ModelProvider,
) -> tuple[dict, dict]:
    audit = {"case_id": case.id, "regime": "testgraph-collect"}
    prompt = classification_prompt(case)
    try:
        first_raw = first_provider.classify(prompt)
        first = parse_classification_reply(first_raw.text)
        audit["first"] = _audit_reply(first_raw, first)

        # Blind second decision: the first decision is deliberately not in this prompt.
        second_raw = second_provider.classify(prompt)
        second = parse_classification_reply(second_raw.text)
        audit["second"] = _audit_reply(second_raw, second)
        return ({
            "case_id": case.id,
            "regime": "testgraph-collect",
            "first_type": first.type,
            "first_reason": first.reason,
            "first_model": first_raw.model,
            "second_type": second.type,
            "second_reason": second.reason,
            "second_model": second_raw.model,
            "pending_replay": True,
            "model_calls": 2,
            "cost_usd": first_raw.cost_usd + second_raw.cost_usd,
        }, audit)
    except Exception as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        return ({
            "case_id": case.id,
            "regime": "testgraph-collect",
            "pending_replay": False,
            "operational_failure": True,
        }, audit)


def run_cases(
    cases: Iterable[BenchmarkCase],
    regime: str,
    *,
    first: ModelProvider,
    second: ModelProvider | None = None,
    resolver: ModelProvider | None = None,
    shakedown: int | None = None,
) -> tuple[list[dict], list[dict]]:
    selected = list(cases)
    if shakedown is not None:
        selected = selected[:shakedown]
    records: list[dict] = []
    audits: list[dict] = []
    for case in selected:
        if regime == "single":
            record, audit = run_single_case(case, first)
        elif regime == "simple":
            if second is None or resolver is None:
                raise ValueError("simple regime requires second and resolver providers")
            record, audit = run_simple_case(case, first, second, resolver)
        elif regime == "testgraph-collect":
            if second is None:
                raise ValueError("testgraph-collect regime requires a second provider")
            record, audit = collect_testgraph_case(case, first, second)
        else:
            raise ValueError(f"unknown regime: {regime}")
        records.append(record)
        audits.append(audit)
    return records, audits


def write_jsonl(path: str, rows: Iterable[dict]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
