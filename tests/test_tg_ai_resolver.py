import json
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import User
from app.models.v2 import SubjectClassificationDecision, SubjectType, V2Subject
from app.services.classification import apply_resolver_arbitration, reopen_classification
from app.services.classification_proposals import record_classification_proposal
from app.services.tg_ai_resolver import ResolverDecision, check_resolver_connectivity, resolve_classification_dispute


def _subject_with_candidates(db):
    user = User(display_name=f"resolver-{uuid.uuid4()}", profile_data={})
    db.add(user); db.flush()
    current_name = f"parent-{uuid.uuid4()}"
    a_name = f"candidate-a-{uuid.uuid4()}"
    b_name = f"candidate-b-{uuid.uuid4()}"
    current = SubjectType(canonical_name=current_name, normalized_name=current_name, status="provisional", created_by="pytest")
    a = SubjectType(canonical_name=a_name, normalized_name=a_name, status="candidate", created_by="pytest")
    b = SubjectType(canonical_name=b_name, normalized_name=b_name, status="candidate", created_by="pytest")
    db.add_all([current, a, b]); db.flush()
    subject = V2Subject(subject_type_id=current.id, owner_id=user.id, name="Resolver subject", canonical_key=f"resolver-{uuid.uuid4()}", identifiers_json={}, attributes_json={}, provenance_json={}, classification_status="disputed")
    db.add(subject); db.flush()
    db.add_all([
        SubjectClassificationDecision(subject_id=subject.id, classification_version=1, from_type_id=current.id, target_type_id=a.id, source_model="model-a", source_client="client-a", reason="A fits", evidence_json={}, outcome="candidate"),
        SubjectClassificationDecision(subject_id=subject.id, classification_version=1, from_type_id=current.id, target_type_id=b.id, source_model="model-b", source_client="client-b", reason="B fits", evidence_json={}, outcome="candidate"),
    ])
    db.commit(); db.refresh(subject)
    return subject, a, b


def _subject_with_proposal_disagreement(db):
    user = User(display_name=f"proposal-resolver-{uuid.uuid4()}", profile_data={})
    db.add(user); db.flush()
    proposal_name = f"proposal-{uuid.uuid4()}"
    alternative_name = f"alternative-{uuid.uuid4()}"
    proposal_type = SubjectType(
        canonical_name=proposal_name,
        normalized_name=proposal_name,
        status="candidate",
        created_by="pytest",
    )
    alternative = SubjectType(
        canonical_name=alternative_name,
        normalized_name=alternative_name,
        status="candidate",
        created_by="pytest",
    )
    db.add_all([proposal_type, alternative]); db.flush()
    subject = V2Subject(
        subject_type_id=proposal_type.id,
        owner_id=user.id,
        name="Proposal resolver subject",
        canonical_key=f"proposal-resolver-{uuid.uuid4()}",
        identifiers_json={},
        attributes_json={},
        provenance_json={},
        classification_status="disputed",
    )
    record_classification_proposal(
        subject,
        source_client="creator-oauth-client",
        source_model="creator-model",
    )
    db.add(subject); db.flush()
    formal = SubjectClassificationDecision(
        subject_id=subject.id,
        classification_version=subject.classification_version,
        from_type_id=proposal_type.id,
        target_type_id=alternative.id,
        source_model="reviewer-model",
        source_client="reviewer-oauth-client",
        reason="The alternative is more specific",
        evidence_json={"basis": "review"},
        outcome="candidate",
    )
    db.add(formal); db.commit(); db.refresh(subject)
    return subject, proposal_type, alternative, formal


def test_resolver_disabled_returns_none(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_ai_resolver_enabled", False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    with SessionLocal() as db:
        subject, _, _ = _subject_with_candidates(db)
        assert resolve_classification_dispute(db, subject) is None


def test_resolver_rejects_unknown_candidate(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_ai_resolver_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"output": [{"content": [{"type": "output_text", "text": json.dumps({"target_subject_type": "not-a-candidate", "confidence": 0.9, "reason": "bad", "action": "select_candidate"})}]}]}

    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: FakeResponse())
    with SessionLocal() as db:
        subject, _, _ = _subject_with_candidates(db)
        with pytest.raises(ValueError, match="candidate"):
            resolve_classification_dispute(db, subject)


def test_resolver_accepts_valid_candidate(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_ai_resolver_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    with SessionLocal() as db:
        subject, a, _ = _subject_with_candidates(db)

        class FakeResponse:
            def raise_for_status(self):
                return None
            def json(self):
                return {"output": [{"content": [{"type": "output_text", "text": json.dumps({"target_subject_type": a.canonical_name, "confidence": 0.91, "reason": "A is better supported", "action": "select_candidate"})}]}]}

        monkeypatch.setattr("httpx.post", lambda *args, **kwargs: FakeResponse())
        result = resolve_classification_dispute(db, subject)
        assert isinstance(result, ResolverDecision)
        assert result.target_subject_type == a.canonical_name
        assert result.action == "select_candidate"


def test_resolver_includes_creation_proposal_as_separate_position(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_ai_resolver_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    calls = []

    with SessionLocal() as db:
        subject, proposal_type, alternative, _ = _subject_with_proposal_disagreement(db)

        class FakeResponse:
            def raise_for_status(self):
                return None
            def json(self):
                return {"output": [{"content": [{"type": "output_text", "text": json.dumps({"target_subject_type": proposal_type.canonical_name, "confidence": 0.9, "reason": "The proposal is better supported", "action": "select_candidate"})}]}]}

        def fake_post(*args, **kwargs):
            calls.append(kwargs)
            return FakeResponse()

        monkeypatch.setattr("httpx.post", fake_post)
        result = resolve_classification_dispute(db, subject)

        assert result.target_subject_type == proposal_type.canonical_name
        prompt_case = json.loads(calls[0]["json"]["input"].split("\n\n", 1)[1])
        assert set(prompt_case["candidate_subject_types"]) == {
            proposal_type.canonical_name,
            alternative.canonical_name,
        }
        assert prompt_case["creation_proposal"]["target_subject_type"] == proposal_type.canonical_name
        assert prompt_case["creation_proposal"]["source_client"] == "creator-oauth-client"
        assert len(prompt_case["decisions"]) == 1
        assert prompt_case["decisions"][0]["target_subject_type"] == alternative.canonical_name


def test_resolver_retries_one_transient_timeout(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_ai_resolver_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "tg_ai_resolver_timeout_seconds", 60.0)
    calls = []

    with SessionLocal() as db:
        subject, a, _ = _subject_with_candidates(db)

        class FakeResponse:
            def raise_for_status(self):
                return None
            def json(self):
                return {"output": [{"content": [{"type": "output_text", "text": json.dumps({"target_subject_type": a.canonical_name, "confidence": 0.91, "reason": "A is better supported", "action": "select_candidate"})}]}]}

        def fake_post(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise httpx.ReadTimeout("transient timeout")
            return FakeResponse()

        monkeypatch.setattr("httpx.post", fake_post)
        result = resolve_classification_dispute(db, subject)

        assert result.target_subject_type == a.canonical_name
        assert len(calls) == 2
        assert calls[0]["timeout"] == 60.0
        assert calls[1]["timeout"] == 60.0


def test_resolver_raises_after_second_timeout(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_ai_resolver_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout("still timing out")

    monkeypatch.setattr("httpx.post", fake_post)
    with SessionLocal() as db:
        subject, _, _ = _subject_with_candidates(db)
        with pytest.raises(httpx.ReadTimeout):
            resolve_classification_dispute(db, subject)

    assert len(calls) == 2


def test_resolver_arbitration_confirms_winner_and_preserves_audit():
    settings = get_settings()
    with SessionLocal() as db:
        subject, a, b = _subject_with_candidates(db)
        result = apply_resolver_arbitration(
            db,
            subject,
            resolver_decision=ResolverDecision(
                target_subject_type=a.canonical_name,
                confidence=0.95,
                reason="A is the more specific supported candidate",
                action="select_candidate",
            ),
        )

        assert result["status"] == "confirmed"
        assert result["subject_type"] == a.canonical_name
        assert result["locked_at"] is not None

        by_model = {item["source_model"]: item for item in result["active_decisions"]}
        assert by_model["model-a"]["outcome"] == "confirmed"
        assert by_model["model-b"]["outcome"] == "rejected_resolver"
        resolver = by_model[f"tg-ai:{settings.tg_ai_resolver_model}"]
        assert resolver["target_subject_type"] == a.canonical_name
        assert resolver["outcome"] == "resolver_selected"
        assert resolver["evidence"]["arbitration"] is True

        stored_subject = db.get(V2Subject, subject.id)
        assert stored_subject.subject_type_id == a.id
        assert stored_subject.classification_status == "confirmed"
        assert db.get(SubjectType, b.id).status == "candidate"


def test_resolver_arbitration_can_select_creation_proposal_without_formal_vote():
    settings = get_settings()
    with SessionLocal() as db:
        subject, proposal_type, _, formal = _subject_with_proposal_disagreement(db)

        result = apply_resolver_arbitration(
            db,
            subject,
            resolver_decision=ResolverDecision(
                target_subject_type=proposal_type.canonical_name,
                confidence=0.94,
                reason="The creation proposal is better supported",
                action="select_candidate",
            ),
        )

        assert result["status"] == "confirmed"
        assert result["subject_type"] == proposal_type.canonical_name
        by_model = {item["source_model"]: item for item in result["active_decisions"]}
        assert by_model["reviewer-model"]["outcome"] == "rejected_resolver"
        assert by_model[f"tg-ai:{settings.tg_ai_resolver_model}"]["outcome"] == "resolver_selected"
        assert "creator-model" not in by_model
        assert len(result["active_decisions"]) == 2
        db.refresh(formal)
        assert formal.outcome == "rejected_resolver"


def test_proposal_win_resolver_settlement_can_be_reopened():
    settings = get_settings()
    with SessionLocal() as db:
        subject, proposal_type, _, _ = _subject_with_proposal_disagreement(db)
        apply_resolver_arbitration(
            db,
            subject,
            resolver_decision=ResolverDecision(
                target_subject_type=proposal_type.canonical_name,
                confidence=0.94,
                reason="The creation proposal is better supported",
                action="select_candidate",
            ),
        )

        reopened = reopen_classification(
            db,
            subject,
            trigger="contradictory_evidence",
            reason="New evidence requires another round",
            evidence={"new_evidence": True},
            requested_by="reopening-oauth-client",
        )

        assert reopened["status"] == "provisional"
        assert reopened["version"] == 2
        assert reopened["subject_type"] == proposal_type.canonical_name
        assert reopened["creation_proposal"]["proposed_type_id"] == str(proposal_type.id)
        assert reopened["active_decisions"] == []
        assert {item["source_model"] for item in reopened["audit_history"]} == {
            "reviewer-model",
            f"tg-ai:{settings.tg_ai_resolver_model}",
        }


def test_formal_alternative_win_resolver_settlement_reopens_to_creation_baseline():
    with SessionLocal() as db:
        subject, proposal_type, alternative, _ = _subject_with_proposal_disagreement(db)
        settled = apply_resolver_arbitration(
            db,
            subject,
            resolver_decision=ResolverDecision(
                target_subject_type=alternative.canonical_name,
                confidence=0.93,
                reason="The formal alternative is better supported",
                action="select_candidate",
            ),
        )
        assert settled["subject_type"] == alternative.canonical_name

        reopened = reopen_classification(
            db,
            subject,
            trigger="contradictory_evidence",
            reason="New evidence requires another round",
            evidence={"new_evidence": True},
            requested_by="reopening-oauth-client",
        )

        assert reopened["status"] == "provisional"
        assert reopened["version"] == 2
        assert reopened["subject_type"] == proposal_type.canonical_name
        assert reopened["creation_proposal"]["proposed_type_id"] == str(proposal_type.id)
        assert reopened["active_decisions"] == []


def test_connectivity_check_reports_disabled_without_calling_openai(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_ai_resolver_enabled", False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: pytest.fail("OpenAI should not be called"))

    result = check_resolver_connectivity()

    assert result == {
        "ok": False,
        "enabled": False,
        "api_key_present": True,
        "model": settings.tg_ai_resolver_model,
        "error": "TG-AI resolver is disabled",
    }


def test_connectivity_check_makes_non_mutating_openai_request(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_ai_resolver_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"id": "resp_test", "output": [{"content": [{"type": "output_text", "text": "TG_AI_OK"}]}]}

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    result = check_resolver_connectivity()

    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["api_key_present"] is True
    assert result["model"] == settings.tg_ai_resolver_model
    assert result["response_id"] == "resp_test"
    assert result["response_text"] == "TG_AI_OK"
    assert len(calls) == 1
    assert calls[0][0][0] == "https://api.openai.com/v1/responses"
