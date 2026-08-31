import json

from benchmarks.kill_test import BenchmarkCase
from benchmarks.providers import ModelReply, parse_classification_reply
from benchmarks.runner import collect_testgraph_case, run_cases, run_simple_case, run_single_case


class FakeProvider:
    def __init__(self, name, replies):
        self.name = name
        self._replies = list(replies)
        self.prompts = []

    def classify(self, prompt):
        self.prompts.append(prompt)
        return self._replies.pop(0)


def reply(model, subject_type, reason="because", raw=None):
    text = raw or json.dumps({"type": subject_type, "reason": reason})
    return ModelReply(model=model, text=text, input_tokens=10, output_tokens=4, raw={"text": text})


def case(case_id="e001"):
    return BenchmarkCase(case_id, "red Ford Fiesta", "easy", "car")


def test_parse_classification_reply_accepts_json_and_code_fence():
    parsed = parse_classification_reply('{"type":"car","reason":"vehicle"}')
    assert parsed.type == "car"
    assert parsed.reason == "vehicle"

    fenced = parse_classification_reply('```json\n{"type":"sports car","reason":"compound"}\n```')
    assert fenced.type == "sports car"


def test_parse_classification_reply_rejects_missing_type():
    try:
        parse_classification_reply('{"reason":"missing"}')
    except ValueError as exc:
        assert "type" in str(exc).lower()
    else:
        raise AssertionError("expected invalid provider reply")


def test_single_case_records_one_model_call():
    provider = FakeProvider("a", [reply("model-a", "car")])
    record, audit = run_single_case(case(), provider)
    assert record["regime"] == "single"
    assert record["type"] == "car"
    assert record["canonical"] is True
    assert record["model_calls"] == 1
    assert audit["first"]["model"] == "model-a"


def test_simple_agreement_does_not_call_resolver_and_is_blind():
    first = FakeProvider("a", [reply("model-a", "car")])
    second = FakeProvider("b", [reply("model-b", "car")])
    resolver = FakeProvider("r", [reply("resolver", "car")])

    record, audit = run_simple_case(case(), first, second, resolver)

    assert record["type"] == "car"
    assert record["consensus"] is True
    assert record["model_calls"] == 2
    assert record["resolver_calls"] == 0
    assert resolver.prompts == []
    assert "model-a" not in second.prompts[0]
    assert audit["second"]["model"] == "model-b"


def test_simple_disagreement_calls_resolver_with_both_answers():
    first = FakeProvider("a", [reply("model-a", "car")])
    second = FakeProvider("b", [reply("model-b", "vehicle")])
    resolver = FakeProvider("r", [reply("resolver", "car")])

    record, _ = run_simple_case(case(), first, second, resolver)

    assert record["type"] == "car"
    assert record["consensus"] is False
    assert record["model_calls"] == 3
    assert record["resolver_calls"] == 1
    assert "car" in resolver.prompts[0]
    assert "vehicle" in resolver.prompts[0]


def test_collect_testgraph_case_keeps_first_decisions_blind_and_unscored():
    first = FakeProvider("a", [reply("model-a", "car")])
    second = FakeProvider("b", [reply("model-b", "vehicle")])

    bundle, audit = collect_testgraph_case(case(), first, second)

    assert bundle["regime"] == "testgraph-collect"
    assert bundle["first_type"] == "car"
    assert bundle["second_type"] == "vehicle"
    assert bundle["pending_replay"] is True
    assert "car" not in second.prompts[0].lower()
    assert audit["first"]["model"] == "model-a"


def test_run_cases_shakedown_is_deterministic_and_limits_to_ten():
    cases = [BenchmarkCase(f"c{i:03d}", f"item {i}", "easy", "thing") for i in range(20)]
    first = FakeProvider("a", [reply("model-a", "thing") for _ in range(10)])
    records, _ = run_cases(cases, "single", first=first, shakedown=10)
    assert [row["case_id"] for row in records] == [f"c{i:03d}" for i in range(10)]


def test_provider_parse_failure_is_recorded_as_operational_failure():
    first = FakeProvider("a", [ModelReply(model="model-a", text="not json", raw={})])
    record, audit = run_single_case(case(), first)
    assert record["canonical"] is False
    assert record["operational_failure"] is True
    assert record["type"] is None
    assert "error" in audit
