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
    subject_id = str(run.subject_id) if run.subject_id else None
    decision_tools = {}
    next_action_arguments = {}
    next_action_instruction = None
    version_tool = None

    if run.state in {"classification_review_required", "awaiting_second_model"}:
        next_action = "get_subject_classification"
        next_action_arguments = {"subject_id": subject_id}
        decision_tools = {
            "agree": "affirm_subject_classification",
            "different_type": "propose_subject_reclassification",
        }
        version_tool = "get_server_info"
        actor_instruction = (
            "An independent model must inspect the current classification"
            if run.state == "awaiting_second_model"
            else "The current model must inspect the current classification"
        )
        independence_instruction = (
            " source_model must differ from every active_decisions[].source_model returned by the inspection. "
            "If you are not a distinct model, stop and hand this workflow to another model."
            if run.state == "awaiting_second_model"
            else ""
        )
        next_action_instruction = (
            f"{actor_instruction} by calling next_action with next_action_arguments, then call exactly one "
            "of decision_tools: agree when the existing type is supported, or different_type when evidence "
            "supports another type. Immediately before that write, call version_tool and pass its "
            "write_version_token unchanged as version_check. Use the acting model's own stable source_model identity."
            f"{independence_instruction}"
        )
    elif run.state == "disputed":
        next_action = "get_server_info"
        decision_tools = {"reconcile": "create_deliberation"}
        next_action_instruction = (
            "Call next_action to obtain a live write_version_token, then call decision_tools.reconcile to preserve "
            "and reconcile the classification disagreement; do not silently choose either candidate. Include the "
            f"subject_id {subject_id} and workflow_run_id {run.id} in its context. Supply canonical_key, title, "
            "question and idempotency_key, and pass the token unchanged as version_check."
        )
    elif run.state == "blocked":
        next_action = None
        next_action_instruction = (
            "No automatic MCP action is available for this blocker. Do not retry or invent an unblocking write; "
            "the blocker must be resolved explicitly before this workflow can continue."
        )
    else:
        next_action = None

    return {
        "workflow_run_id": str(run.id),
        "workflow_type": run.workflow_type,
        "state": run.state,
        "current_step": run.current_step,
        "required_actor": run.required_actor,
        "next_action": next_action,
        "next_action_arguments": next_action_arguments,
        "next_action_instruction": next_action_instruction,
        "decision_tools": decision_tools,
        "version_tool": version_tool,
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
register_write_finalize_hook("experience", _finalize_subject_enrichment)
