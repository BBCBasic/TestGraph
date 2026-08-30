import json
import uuid

import pytest

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import User
from app.models.v2 import SubjectClassificationDecision, SubjectType, V2Subject
from app.services.tg_ai_resolver import ResolverDecision, resolve_classification_dispute


def _subject_with_candidates(db):
    user = User(display_name=f"resolver-{uuid.uuid4()}", profile_data={})
    db.add(user); db.flush()
    current = SubjectType(canonical_name=f"parent-{uuid.uuid4()}", normalized_name=f"parent-{uuid.uuid4()}", status="provisional", created_by="pytest")
    a = SubjectType(canonical_name=f"candidate-a-{uuid.uuid4()}", normalized_name=f"candidate-a-{uuid.uuid4()}", status="candidate", created_by="pytest")
    b = SubjectType(canonical_name=f"candidate-b-{uuid.uuid4()}", normalized_name=f"candidate-b-{uuid.uuid4()}", status="candidate", created_by="pytest")
    db.add_all([current, a, b]); db.flush()
    subject = V2Subject(subject_type_id=current.id, owner_id=user.id, name="Resolver subject", canonical_key=f"resolver-{uuid.uuid4()}", identifiers_json={}, attributes_json={}, provenance_json={}, classification_status="disputed")
    db.add(subject); db.flush()
    db.add_all([
        SubjectClassificationDecision(subject_id=subject.id, classification_version=1, from_type_id=current.id, target_type_id=a.id, source_model="model-a", source_client="pytest", reason="A fits", evidence_json={}, outcome="candidate"),
        SubjectClassificationDecision(subject_id=subject.id, classification_version=1, from_type_id=current.id, target_type_id=b.id, source_model="model-b", source_client="pytest", reason="B fits", evidence_json={}, outcome="candidate"),
    ])
    db.commit(); db.refresh(subject)
    return subject, a, b


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
