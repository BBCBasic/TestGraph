import uuid
from copy import deepcopy

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.mcp_v2 import TOOLS
from app.db.base import Base
from app.models.entities import IdempotencyRecord
from app.models.v2 import SubjectClassificationDecision, SubjectType, V2Subject
from app.models.workflow import WorkflowEvent, WorkflowRun
from app.services.workflows import start_or_resume_enrichment_workflow, sync_enrichment_classification_workflow, workflow_body
from app.services.classification_proposals import record_classification_proposal
from app.services.mcp_v2_guidance_policy import apply_guidance_tool_policy
from app.services.write_safety import finish_idempotent_write


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _subject(db, *, status="provisional"):
    subject_type = SubjectType(
        canonical_name="generic thing",
        normalized_name="generic thing",
        status="provisional",
        created_by="pytest",
    )
    db.add(subject_type); db.flush()
    subject = V2Subject(
        subject_type_id=subject_type.id,
        name="Example",
        canonical_key="example",
        identifiers_json={}, attributes_json={}, provenance_json={},
        classification_status=status,
    )
    db.add(subject); db.commit(); db.refresh(subject)
    return subject, subject_type


def _decision(db, subject, target, model, outcome="candidate"):
    row = SubjectClassificationDecision(
        subject_id=subject.id,
        classification_version=subject.classification_version,
        from_type_id=subject.subject_type_id,
        target_type_id=target.id,
        source_model=model,
        source_client=f"{model}-client",
        reason="test",
        evidence_json={},
        outcome=outcome,
    )
    db.add(row); db.commit()
    return row


def _exposed_tool_names():
    tools = deepcopy(TOOLS)
    apply_guidance_tool_policy(tools)
    return {tool["name"] for tool in tools}


def test_enrichment_workflow_requires_classification_review_for_unconfirmed_subject():
    with _session() as db:
        subject, _ = _subject(db)
        run = start_or_resume_enrichment_workflow(db, subject, owner_id=None, actor_client="pytest")
        body = workflow_body(run)
        assert body["state"] == "classification_review_required"
        assert body["workflow_action_required"] is True
        assert body["next_action"] == "get_subject_classification"
        assert body["next_action_arguments"] == {"subject_id": str(subject.id)}
        assert body["decision_tools"] == {
            "agree": "affirm_subject_classification",
            "different_type": "propose_subject_reclassification",
        }
        assert body["version_tool"] is None
        assert db.scalar(select(WorkflowEvent).where(WorkflowEvent.workflow_run_id == run.id)) is not None


def test_creation_proposal_workflow_hands_off_directly_to_different_client():
    with _session() as db:
        subject, _ = _subject(db)
        record_classification_proposal(
            subject,
            source_client="creator-oauth-client:v3",
            source_model="creator-model",
        )
        db.commit()

        run = start_or_resume_enrichment_workflow(
            db,
            subject,
            owner_id=None,
            actor_client="creator-oauth-client:v3",
        )
        body = workflow_body(run)

        assert run.required_actor == "independent_model"
        assert "different authenticated client" in body["next_action_instruction"].lower()


def test_enrichment_workflow_keeps_creator_self_review_at_classification_review_required():
    with _session() as db:
        subject, target = _subject(db)
        record_classification_proposal(
            subject,
            source_client="creator-oauth-client",
            source_model="creator-model-a",
        )
        db.commit()
        run = start_or_resume_enrichment_workflow(db, subject, owner_id=None, actor_client="pytest")
        decision = _decision(db, subject, target, "creator-model-b")
        decision.source_client = "creator-oauth-client"
        subject.classification_status = "candidate"
        db.commit()
        run = sync_enrichment_classification_workflow(
            db,
            subject,
            actor_client="creator-oauth-client",
            actor_model="creator-model-b",
        )
        assert run.id is not None
        assert run.state == "classification_review_required"
        assert run.required_actor == "independent_model"
        body = workflow_body(run)
        assert body["next_action"] == "get_subject_classification"
        assert body["next_action_arguments"] == {"subject_id": str(subject.id)}
        assert body["version_tool"] is None
        instruction = body["next_action_instruction"].lower()
        assert "different authenticated client" in instruction
        assert "stop" in instruction
        assert "durable" in instruction
        assert "current model must inspect" not in instruction


def test_enrichment_workflow_completes_when_classification_confirmed():
    with _session() as db:
        subject, _ = _subject(db, status="confirmed")
        run = start_or_resume_enrichment_workflow(db, subject, owner_id=None, actor_client="pytest")
        body = workflow_body(run)
        assert run.state == "completed"
        assert run.completed_at is not None
        assert body["workflow_action_required"] is False
        assert body["next_action"] is None
        assert body["next_action_instruction"] is None
        assert body["decision_tools"] == {}
        assert body["version_tool"] is None


def test_enrichment_workflow_marks_disagreement():
    with _session() as db:
        subject, target = _subject(db)
        other = SubjectType(
            canonical_name="other thing",
            normalized_name="other thing",
            status="provisional",
            created_by="pytest",
        )
        db.add(other); db.commit()
        start_or_resume_enrichment_workflow(db, subject, owner_id=None, actor_client="pytest")
        _decision(db, subject, target, "model-a")
        _decision(db, subject, other, "model-b")
        subject.classification_status = "disputed"
        db.commit()
        run = sync_enrichment_classification_workflow(db, subject, actor_client="pytest", actor_model="model-b")
        assert run.state == "disputed"
        body = workflow_body(run)
        assert body["next_action"] == "create_deliberation"
        assert body["decision_tools"] == {}
        for required in ("canonical_key", "title", "question", "idempotency_key"):
            assert required in body["next_action_instruction"]
        assert "version_check" not in body["next_action_instruction"]


def test_blocked_workflow_does_not_point_back_to_an_uninformative_tool():
    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_type="enrich_subject",
        subject_id=uuid.uuid4(),
        state="blocked",
        current_step="test",
        context_json={"blocker": "test blocker"},
    )

    body = workflow_body(run)

    assert body["next_action"] is None
    assert "no automatic mcp action" in body["next_action_instruction"].lower()


def test_every_workflow_next_action_names_an_exposed_mcp_tool():
    exposed = _exposed_tool_names()
    for state in (
        "classification_review_required",
        "awaiting_second_model",
        "disputed",
        "blocked",
    ):
        run = WorkflowRun(
            id=uuid.uuid4(),
            workflow_type="enrich_subject",
            subject_id=uuid.uuid4(),
            state=state,
            current_step="test",
            context_json={},
        )
        body = workflow_body(run)
        referenced = {
            body["next_action"],
            body.get("version_tool"),
            *body.get("decision_tools", {}).values(),
        } - {None}
        assert referenced <= exposed


def test_subject_enrichment_idempotent_commit_includes_initial_workflow_checkpoint():
    with _session() as db:
        subject, _ = _subject(db)
        body = {"subject_id": str(subject.id), "changed": True}
        finish_idempotent_write(
            db,
            client_id="pytest:v3",
            key="subject-enrichment:atomic-test",
            payload_hash="hash",
            response_body=body,
        )

        run = db.scalar(select(WorkflowRun).where(WorkflowRun.subject_id == subject.id))
        stored = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "subject-enrichment:atomic-test"))
        assert run is not None
        assert body["workflow"]["workflow_run_id"] == str(run.id)
        assert stored.response_body["workflow"]["workflow_run_id"] == str(run.id)


def test_save_experience_idempotent_commit_includes_initial_workflow_checkpoint():
    with _session() as db:
        subject, _ = _subject(db)
        body = {"subject_id": str(subject.id), "experience_id": str(uuid.uuid4())}
        finish_idempotent_write(
            db,
            client_id="pytest:v3",
            key="experience:atomic-test",
            payload_hash="hash",
            response_body=body,
        )

        run = db.scalar(select(WorkflowRun).where(WorkflowRun.subject_id == subject.id))
        stored = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "experience:atomic-test"))
        assert run is not None
        assert body["workflow"]["workflow_run_id"] == str(run.id)
        assert stored.response_body["workflow"]["workflow_run_id"] == str(run.id)
