from benchmarks.kill_test import BenchmarkCase
from benchmarks.replay import replay_case, replay_records
from app.services.tg_ai_resolver import ResolverDecision


def case(case_id="e001"):
    return BenchmarkCase(case_id, "red Ford Fiesta", "easy", "car")


def collected(first="car", second="car"):
    return {
        "case_id": "e001",
        "regime": "testgraph-collect",
        "first_type": first,
        "first_reason": "first reason",
        "first_model": "model-a",
        "second_type": second,
        "second_reason": "second reason",
        "second_model": "model-b",
        "pending_replay": True,
        "model_calls": 2,
        "cost_usd": 0.0,
    }


def test_replay_agreement_confirms_real_server_state(tmp_path):
    rows, audits = replay_records([case()], [collected()], f"sqlite+pysqlite:///{tmp_path / 'agree.db'}")
    row = rows[0]
    assert row["regime"] == "testgraph"
    assert row["type"] == "car"
    assert row["canonical"] is True
    assert row["consensus"] is True
    assert row["operational_failure"] is False
    assert row["model_calls"] == 2
    assert audits[0]["final_state"]["status"] == "confirmed"
    assert {d["source_model"] for d in audits[0]["final_state"]["active_decisions"]} == {"model-a", "model-b"}


def test_replay_disagreement_uses_tg_ai_hook(monkeypatch, tmp_path):
    def resolve(_db, _subject):
        return ResolverDecision(
            target_subject_type="car",
            confidence=0.9,
            reason="car is the better candidate",
            action="select_candidate",
        )

    monkeypatch.setattr("app.services.tg_ai_resolver.resolve_classification_dispute", resolve)
    rows, audits = replay_records(
        [case()], [collected("car", "vehicle")], f"sqlite+pysqlite:///{tmp_path / 'dispute.db'}"
    )
    row = rows[0]
    assert row["type"] == "car"
    assert row["canonical"] is True
    assert row["consensus"] is False
    assert row["resolver_calls"] == 1
    assert audits[0]["final_state"]["status"] == "confirmed"
    assert any(d["source_model"].startswith("tg-ai:") for d in audits[0]["final_state"]["active_decisions"])


def test_unresolved_dispute_is_operational_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.tg_ai_resolver.resolve_classification_dispute", lambda _db, _subject: None)
    rows, audits = replay_records(
        [case()], [collected("car", "vehicle")], f"sqlite+pysqlite:///{tmp_path / 'unresolved.db'}"
    )
    assert rows[0]["canonical"] is False
    assert rows[0]["operational_failure"] is True
    assert rows[0]["type"] == "benchmark entity"
    assert audits[0]["final_state"]["status"] == "disputed"


def test_replay_semantic_guard_rejection_is_operational_failure(tmp_path):
    rows, audits = replay_records(
        [case()], [collected("red car", "red car")], f"sqlite+pysqlite:///{tmp_path / 'guard.db'}"
    )
    assert rows[0]["operational_failure"] is True
    assert rows[0]["canonical"] is False
    assert "error" in audits[0]


def test_replay_records_rejects_missing_case_collection(tmp_path):
    try:
        replay_records([case()], [], f"sqlite+pysqlite:///{tmp_path / 'missing.db'}")
    except ValueError as exc:
        assert "missing collected decision" in str(exc).lower()
    else:
        raise AssertionError("expected missing collection failure")
