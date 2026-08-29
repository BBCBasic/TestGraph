from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import OAuthClient
from app.models.workflow import McpInteraction
from app.services.mcp_audit import record_mcp_interaction, redact_arguments
from app.services.workflow_inspection import list_mcp_interactions


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
        assert row.arguments_summary["_attribution"]["source_model"] == "gpt-test"
        assert row.arguments_summary["_attribution"]["source_model_source"] == "tool_argument"
        assert row.result_summary["changed"] is True
        assert row.outcome == "success"


def test_oauth_client_identity_is_resolved_without_inventing_exact_model():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(OAuthClient(
            client_id="chatgpt-client-1",
            redirect_uris=["https://example.test/callback"],
            client_name="ChatGPT",
            token_endpoint_auth_method="none",
        ))
        db.commit()
        record_mcp_interaction(
            db,
            request_id="req-client",
            user_id=None,
            client_id="chatgpt-client-1",
            source_model=None,
            tool_name="search",
            arguments={"query": "zoe"},
            result={"structuredContent": {"count": 0}},
            outcome="success",
            latency_ms=3,
            server_version="test",
            build_sha="sha",
        )
        row = db.scalar(select(McpInteraction))
        attribution = row.arguments_summary["_attribution"]
        assert attribution["oauth_client_name"] == "ChatGPT"
        assert attribution["client_family"] == "chatgpt"
        assert attribution["source_model"] is None
        assert attribution["model_attribution_status"] == "unknown"


def test_log_inspection_result_is_stored_as_a_fixed_size_summary():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        record_mcp_interaction(
            db,
            request_id="req-log-read",
            user_id=None,
            client_id="pytest",
            source_model=None,
            tool_name="list_my_mcp_interactions",
            arguments={"limit": 10},
            result={
                "structuredContent": {
                    "count": 2,
                    "items": [
                        {"interaction_id": "first", "result_summary": {"changed": True}},
                        {"interaction_id": "second", "result_summary": {"changed": False}},
                    ],
                    "privacy": "structured_redacted_no_raw_conversation",
                }
            },
            outcome="success",
            latency_ms=4,
            server_version="test",
            build_sha="sha",
        )

        row = db.scalar(select(McpInteraction))
        assert row.result_summary == {
            "count": 2,
            "items": {"redacted_payload": True, "type": "array", "length": 2},
            "privacy": "structured_redacted_no_raw_conversation",
        }
