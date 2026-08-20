import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _idempotency_conflict_error
from app.db.base import Base
from app.services.write_safety import (
    IdempotencyKeyConflictError, begin_idempotent_write, finish_idempotent_write,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _record_first_write(db):
    payload_hash, prior = begin_idempotent_write(
        db,
        client_id="test-client:v3",
        key="assessment:stable-test-key",
        payload={"conclusion": "original"},
    )
    assert prior is None
    finish_idempotent_write(
        db,
        client_id="test-client:v3",
        key="assessment:stable-test-key",
        payload_hash=payload_hash,
        response_body={"saved": True, "assessment_id": "assessment-1"},
    )


def test_conflicting_idempotency_key_raises_typed_error(db):
    _record_first_write(db)

    with pytest.raises(IdempotencyKeyConflictError) as raised:
        begin_idempotent_write(
            db,
            client_id="test-client:v3",
            key="assessment:stable-test-key",
            payload={"conclusion": "changed"},
        )

    assert raised.value.operation_scope == "assessment"
    assert str(raised.value) == "Idempotency key is already bound to different content"


def test_mcp_conflict_response_tells_ai_how_to_recover(db):
    _record_first_write(db)

    with pytest.raises(IdempotencyKeyConflictError) as raised:
        begin_idempotent_write(
            db,
            client_id="test-client:v3",
            key="assessment:stable-test-key",
            payload={"conclusion": "changed"},
        )

    result = _idempotency_conflict_error(raised.value)
    payload = result["structuredContent"]
    details = payload["details"]

    assert result["isError"] is True
    assert payload["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert details["operation_scope"] == "assessment"
    assert details["retry_current_request_with_same_key"] is False
    assert details["safe_to_retry_original_request"] is True
    assert "exact original payload unchanged" in details["action_required"]["if_retry"]
    assert "new deterministic idempotency_key" in details["action_required"]["if_content_changed"]
    assert "ValueError" not in str(payload)
    assert "TasteGraph server error" not in str(payload)
