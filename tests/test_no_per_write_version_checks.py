from copy import deepcopy
import uuid

from app.api.mcp_v2 import TOOLS
from app.models.workflow import WorkflowRun
from app.services.mcp_v2_guidance_policy import WRITE_TOOL_NAMES, apply_guidance_tool_policy
from app.services.workflows import workflow_body


def test_write_tools_do_not_require_per_call_version_check():
    tools = deepcopy(TOOLS)
    apply_guidance_tool_policy(tools)

    for tool in tools:
        if tool.get("name") not in WRITE_TOOL_NAMES:
            continue
        schema = tool.get("inputSchema") or {}
        assert "version_check" not in (schema.get("properties") or {})
        assert "version_check" not in (schema.get("required") or [])
        assert "immediately before" not in tool.get("description", "").lower()


def test_classification_workflow_does_not_add_version_probe_between_steps():
    subject_id = uuid.uuid4()
    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_type="enrich_subject",
        subject_id=subject_id,
        state="awaiting_second_model",
        current_step="second_model_classification",
        context_json={},
    )

    body = workflow_body(run)

    assert body["next_action"] == "get_subject_classification"
    assert body["version_tool"] is None
    assert "get_server_info" not in body["next_action_instruction"]
    assert "version_check" not in body["next_action_instruction"]


def test_dispute_workflow_points_directly_to_reconciliation():
    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_type="enrich_subject",
        subject_id=uuid.uuid4(),
        state="disputed",
        current_step="classification_disagreement",
        context_json={},
    )

    body = workflow_body(run)

    assert body["next_action"] == "create_deliberation"
    assert body["version_tool"] is None
    assert "version_check" not in body["next_action_instruction"]
