from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import actions_v2, mcp_v2
from app.db.base import Base
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write


def test_mcp_v2_write_tools_are_retry_safe():
    tools = {tool["name"]: tool for tool in mcp_v2.TOOLS}
    for name in ("save_experience", "save_assessment"):
        schema = tools[name]["inputSchema"]
        assert "idempotency_key" in schema["required"]
        assert tools[name]["annotations"]["idempotentHint"] is True


def test_action_v2_openapi_is_generic_and_separates_assessments():
    spec = actions_v2.openapi()
    assert "/actions-v2/experiences" in spec["paths"]
    assert "/actions-v2/assessments" in spec["paths"]
    experience = spec["components"]["schemas"]["ExperienceCreate"]
    assert experience["properties"]["structured_data"]["additionalProperties"] is True
    assert "idempotency_key" in experience["required"]
    assessment = spec["components"]["schemas"]["AssessmentCreate"]
    assert "idempotency_key" in assessment["required"]


def test_idempotency_replays_same_write_and_rejects_changed_content():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        payload_hash, prior = begin_idempotent_write(db, client_id="client:v2", key="experience:abcdefgh", payload={"x": 1})
        assert prior is None
        finish_idempotent_write(db, client_id="client:v2", key="experience:abcdefgh", payload_hash=payload_hash, response_body={"saved": True, "id": "one"})
        _, prior = begin_idempotent_write(db, client_id="client:v2", key="experience:abcdefgh", payload={"x": 1})
        assert prior == {"saved": True, "id": "one"}

        try:
            begin_idempotent_write(db, client_id="client:v2", key="experience:abcdefgh", payload={"x": 2})
        except ValueError as exc:
            assert "different content" in str(exc)
        else:
            raise AssertionError("changed payload should have been rejected")
