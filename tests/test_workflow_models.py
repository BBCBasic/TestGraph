from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.workflow import McpInteraction, WorkflowEvent, WorkflowRun


def test_workflow_and_mcp_audit_models_persist():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        run = WorkflowRun(
            workflow_type="enrich_subject",
            state="classification_review_required",
            current_step="classification_review",
            required_actor="current_model",
            context_json={"subject_type": "vehicle"},
        )
        db.add(run)
        db.flush()
        event = WorkflowEvent(
            workflow_run_id=run.id,
            event_type="state_changed",
            step="classification_review",
            actor_client="pytest",
            details_json={"from": "enrichment_applied", "to": "classification_review_required"},
        )
        interaction = McpInteraction(
            request_id="req-1",
            client_id="pytest",
            tool_name="enrich_subject",
            outcome="success",
            arguments_summary={"subject_id": "abc"},
            result_summary={"changed": True},
            latency_ms=12,
            server_version="test",
            build_sha="test-sha",
        )
        db.add_all([event, interaction])
        db.commit()

        assert db.get(WorkflowRun, run.id).workflow_type == "enrich_subject"
        assert db.get(WorkflowEvent, event.id).details_json["to"] == "classification_review_required"
        assert db.get(McpInteraction, interaction.id).outcome == "success"
