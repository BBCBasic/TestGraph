from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.workflow import McpInteraction
from app.services.mcp_audit import record_mcp_interaction, redact_arguments


def test_redact_arguments_removes_secrets_and_summarises_large_text():
    payload = {
        "subject_id": "subject-123",
        "version_check": "v" * 64,
        "authorization": "Bearer secret",
        "api_key": "secret-key",
        "password": "secret-password",
        "reason": "because " + ("x" * 400),
        "evidence": {"notes": "y" * 500, "registration": "WO68 LCJ"},
        "nested": {"oauth_code": "secret-code", "name": "Renault Zoe"},
    }

    redacted = redact_arguments(payload)

    assert redacted["subject_id"] == "subject-123"
    assert redacted["version_check"] == "[redacted]"
    assert redacted["authorization"] == "[redacted]"
    assert redacted["api_key"] == "[redacted]"
    assert redacted["password"] == "[redacted]"
    assert redacted["nested"]["oauth_code"] == "[redacted]"
    assert redacted["nested"]["name"] == "Renault Zoe"
    assert redacted["reason"]["redacted_text"] is True
    assert redacted["reason"]["length"] > 400
    assert redacted["evidence"]["redacted_payload"] is True


def test_record_mcp_interaction_persists_structured_summary():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        record_mcp_interaction(
            db,
            request_id="req-1",
            user_id=None,
            client_id="pytest",
            source_model="gpt-test",
            tool_name="enrich_subject",
            arguments={"subject_id": "abc", "version_check": "x" * 64},
            result={"structuredContent": {"changed": True}},
            outcome="success",
            latency_ms=8,
            server_version="test",
            build_sha="sha",
        )
        row = db.scalar(select(McpInteraction))
        assert row is not None
        assert row.arguments_summary["version_check"] == "[redacted]"
        assert row.result_summary["changed"] is True
        assert row.outcome == "success"
