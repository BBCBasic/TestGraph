from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import SubjectClassificationDecision, V2Subject, now_utc
from app.models.workflow import WorkflowEvent, WorkflowRun
from app.services.write_safety import register_write_finalize_hook


_ACTIVE_STATES = {
    "enrichment_applied",
    "classification_review_required",
    "awaiting_second_model",
    "disputed",
    "blocked",
}


def _active_run(db: Session, subject: V2Subject) -> WorkflowRun | None:
    return db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.workflow_type == "enrich_subject",
            WorkflowRun.subject_id == subject.id,
            WorkflowRun.state.in_(_ACTIVE_STATES),
        ).order_by(WorkflowRun.created_at.desc())
    )


def _append_event(
    db: Session,
    run: WorkflowRun,
    *,
    event_type: str,
    step: str,
    actor_client: str | None,
    actor_model: str | None = None,
    details: dict | None = None,
) -> None:
    db.add(WorkflowEvent(
        workflow_run_id=run.id,
        event_type=event_type,
        step=step,
        actor_client=actor_client,
        actor_model=actor_model,
        details_json=details or {},
    ))


def _derive_state(db: Session, subject: V2Subject) -> tuple[str, str, str | None]:
    if subject.classification_status == "confirmed":
        return "completed", "classification_settled", None
    if subject.classification_status == "disputed":
        return "disputed", "classification_disagreement", "resolver"

    active = list(db.scalars(select(SubjectClassificationDecision).where(
        SubjectClassificationDecision.subject_id == subject.id,
        SubjectClassificationDecision.classification_version == subject.classification_version,
    )).all())
    candidate = [item for item in active if item.outcome == "candidate"]
    if candidate:
        counts = Counter(item.target_type_id for item in candidate)
        if len(counts) > 1:
            return "disputed", "classification_disagreement", "resolver"
        return "awaiting_second_model", "second_model_classification", "independent_model"
    return "classification_review_required", "classification_review", "current_model"


def _transition(
    db: Session,
    run: WorkflowRun,
    *,
    state: str,
    step: str,
    required_actor: str | None,
    actor_client: str | None,
    actor_model: str | None = None,
) -> WorkflowRun:
    previous = run.state
    previous_step = run.current_step
    run.state = state
    run.current_step = step
    run.required_actor = required_actor
    if state == "completed":
        run.completed_at = now_utc()
    elif run.completed_at is not None:
        run.completed_at = None
    if previous != state or previous_step != step:
        _append_event(
            db,
            run,
            event_type="state_changed",
            step=step,
            actor_client=actor_client,
            actor_model=actor_model,
            details={"from": previous, "to": state, "from_step": previous_step, "to_step": step},
        )
    db.add(run)
    db.flush()
    return run


def start_or_resume_enrichment_workflow(
    db: Session,
    subject: V2Subject,
    *,
    owner_id,
    actor_client: str | None,
) -> WorkflowRun:
    run = _active_run(db, subject)
    if run is None:
        run = WorkflowRun(
            workflow_type="enrich_subject",
            owner_id=owner_id,
            subject_id=subject.id,
            state="enrichment_applied",
            current_step="enrichment_persisted",
            required_actor=None,
            context_json={
                "subject_id": str(subject.id),
                "classification_version": subject.classification_version,
            },
        )
        db.add(run)
        db.flush()
        _append_event(
            db,
            run,
            event_type="workflow_started",
            step="enrichment_persisted",
            actor_client=actor_client,
            details={"state": "enrichment_applied"},
        )

    state, step, required_actor = _derive_state(db, subject)
    return _transition(
        db,
        run,
        state=state,
        step=step,
        required_actor=required_actor,
        actor_client=actor_client,
    )


def sync_enrichment_classification_workflow(
    db: Session,
    subject: V2Subject,
    *,
    actor_client: str | None,
    actor_model: str | None = None,
) -> WorkflowRun | None:
    run = _active_run(db, subject)
    if run is None:
        return None
    state, step, required_actor = _derive_state(db, subject)
    return _transition(
        db,
        run,
        state=state,
        step=step,
        required_actor=required_actor,
        actor_client=actor_client,
        actor_model=actor_model,
    )


def workflow_body(run: WorkflowRun) -> dict:
    next_action = {
        "classification_review_required": "submit_classification_decision",
        "awaiting_second_model": "await_independent_model",
        "disputed": "resolve_disagreement",
        "blocked": "resolve_blocker",
        "completed": None,
    }.get(run.state)
    return {
        "workflow_run_id": str(run.id),
        "workflow_type": run.workflow_type,
        "state": run.state,
        "current_step": run.current_step,
        "required_actor": run.required_actor,
        "next_action": next_action,
        "completed": run.state == "completed",
    }


def _finalize_subject_enrichment(db: Session, *, client_id: str, response_body: dict) -> None:
    raw_subject_id = response_body.get("subject_id")
    if not raw_subject_id:
        return
    try:
        import uuid
        subject_id = uuid.UUID(str(raw_subject_id))
    except (TypeError, ValueError):
        return
    subject = db.get(V2Subject, subject_id)
    if subject is None:
        return
    run = start_or_resume_enrichment_workflow(
        db,
        subject,
        owner_id=subject.owner_id,
        actor_client=client_id,
    )
    response_body["workflow"] = workflow_body(run)


register_write_finalize_hook("subject-enrichment", _finalize_subject_enrichment)
