from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import IdempotencyRecord
from app.models.v2 import SubjectClassificationDecision, SubjectType, V2Subject
from app.models.workflow import WorkflowEvent, WorkflowRun
from app.services.workflows import start_or_resume_enrichment_workflow, sync_enrichment_classification_workflow, workflow_body
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


def test_enrichment_workflow_requires_classification_review_for_unconfirmed_subject():
    with _session() as db:
        subject, _ = _subject(db)
        run = start_or_resume_enrichment_workflow(db, subject, owner_id=None, actor_client="pytest")
        body = workflow_body(run)
        assert body["state"] == "classification_review_required"
        assert body["next_action"] == "submit_classification_decision"
        assert db.scalar(select(WorkflowEvent).where(WorkflowEvent.workflow_run_id == run.id)) is not None


def test_enrichment_workflow_waits_durably_for_second_model():
    with _session() as db:
        subject, target = _subject(db)
        run = start_or_resume_enrichment_workflow(db, subject, owner_id=None, actor_client="pytest")
        _decision(db, subject, target, "model-a")
        run = sync_enrichment_classification_workflow(db, subject, actor_client="pytest", actor_model="model-a")
        assert run.id is not None
        assert run.state == "awaiting_second_model"
        assert workflow_body(run)["next_action"] == "await_independent_model"


def test_enrichment_workflow_completes_when_classification_confirmed():
    with _session() as db:
        subject, _ = _subject(db, status="confirmed")
        run = start_or_resume_enrichment_workflow(db, subject, owner_id=None, actor_client="pytest")
        assert run.state == "completed"
        assert run.completed_at is not None
        assert workflow_body(run)["next_action"] is None


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
        assert workflow_body(run)["next_action"] == "resolve_disagreement"


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
