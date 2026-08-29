import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.mcp_v2 import TOOLS
from app.db.base import Base
from app.models.workflow import McpInteraction, WorkflowRun
from app.services.mcp_v2_guidance_policy import apply_guidance_tool_policy


def test_guidance_policy_exposes_read_only_workflow_and_audit_tools():
    tools = [dict(tool) for tool in TOOLS]
    apply_guidance_tool_policy(tools)
    by_name = {tool["name"]: tool for tool in tools}
    assert "list_my_workflows" in by_name
    assert "list_my_mcp_interactions" in by_name
    assert by_name["list_my_workflows"]["annotations"]["readOnlyHint"] is True
    assert by_name["list_my_mcp_interactions"]["annotations"]["readOnlyHint"] is True


def test_inspection_queries_are_owner_scoped():
    from app.services.workflow_inspection import list_mcp_interactions, list_workflows

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    owner = uuid.uuid4()
    other = uuid.uuid4()
    with Session(engine) as db:
        db.add_all([
            WorkflowRun(workflow_type="enrich_subject", owner_id=owner, state="awaiting_second_model", current_step="second_model_classification", context_json={}),
            WorkflowRun(workflow_type="enrich_subject", owner_id=other, state="completed", current_step="classification_settled", context_json={}),
            McpInteraction(user_id=owner, client_id="chatgpt", tool_name="enrich_subject", arguments_summary={}, result_summary={}, outcome="success"),
            McpInteraction(user_id=other, client_id="claude", tool_name="search", arguments_summary={}, result_summary={}, outcome="success"),
        ])
        db.commit()

        workflows = list_workflows(db, owner_id=owner, limit=20)
        interactions = list_mcp_interactions(db, owner_id=owner, limit=20)

        assert len(workflows) == 1
        assert workflows[0]["state"] == "awaiting_second_model"
        assert len(interactions) == 1
        assert interactions[0]["client_id"] == "chatgpt"
        assert interactions[0]["tool_name"] == "enrich_subject"
