from __future__ import annotations

import json

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.v2 import SubjectClassificationDecision, SubjectType, V2Subject


class ResolverDecision(BaseModel):
    target_subject_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    action: str


def _extract_output_text(payload: dict) -> str:
    for output in payload.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("Resolver response did not contain output_text")


def resolve_classification_dispute(db: Session, subject: V2Subject) -> ResolverDecision | None:
    settings = get_settings()
    if not settings.tg_ai_resolver_enabled or not settings.openai_api_key:
        return None

    decisions = list(db.scalars(select(SubjectClassificationDecision).where(
        SubjectClassificationDecision.subject_id == subject.id,
        SubjectClassificationDecision.classification_version == subject.classification_version,
        SubjectClassificationDecision.outcome == "candidate",
    ).order_by(SubjectClassificationDecision.created_at)).all())
    if len(decisions) < 2:
        return None

    candidate_types = {}
    case_decisions = []
    for decision in decisions:
        target = db.get(SubjectType, decision.target_type_id)
        if target is None:
            continue
        candidate_types[target.canonical_name] = target
        case_decisions.append({
            "target_subject_type": target.canonical_name,
            "source_model": decision.source_model,
            "reason": decision.reason,
            "evidence": decision.evidence_json,
        })
    if len(candidate_types) < 2:
        return None

    current = db.get(SubjectType, subject.subject_type_id)
    case = {
        "subject": {"id": str(subject.id), "name": subject.name},
        "current_subject_type": current.canonical_name if current else None,
        "candidate_subject_types": list(candidate_types),
        "decisions": case_decisions,
    }
    prompt = (
        "You are TestGraph's independent classification resolver. Choose only one of the supplied "
        "candidate_subject_types. Return JSON only with target_subject_type, confidence (0 to 1), "
        "reason, and action set to select_candidate. Do not invent a new type.\n\n"
        + json.dumps(case, ensure_ascii=False)
    )
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={"model": settings.tg_ai_resolver_model, "input": prompt},
        timeout=settings.tg_ai_resolver_timeout_seconds,
    )
    response.raise_for_status()
    decision = ResolverDecision.model_validate_json(_extract_output_text(response.json()))
    if decision.action != "select_candidate":
        raise ValueError("Resolver action must be select_candidate")
    if decision.target_subject_type not in candidate_types:
        raise ValueError("Resolver selected a type outside the current candidate set")
    return decision
