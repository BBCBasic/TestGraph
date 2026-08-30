from __future__ import annotations

import json
import logging

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.v2 import SubjectClassificationDecision, SubjectType, V2Subject


logger = logging.getLogger(__name__)
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


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


def _openai_post(*, input_text: str):
    settings = get_settings()
    logger.info("TG-AI OpenAI request sending model=%s", settings.tg_ai_resolver_model)
    response = httpx.post(
        OPENAI_RESPONSES_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={"model": settings.tg_ai_resolver_model, "input": input_text},
        timeout=settings.tg_ai_resolver_timeout_seconds,
    )
    logger.info("TG-AI OpenAI response received status=%s", getattr(response, "status_code", "unknown"))
    response.raise_for_status()
    return response


def check_resolver_connectivity() -> dict:
    """Make one small, non-mutating resolver API call and return safe diagnostics."""
    settings = get_settings()
    base = {
        "ok": False,
        "enabled": settings.tg_ai_resolver_enabled,
        "api_key_present": bool(settings.openai_api_key),
        "model": settings.tg_ai_resolver_model,
    }
    if not settings.tg_ai_resolver_enabled:
        logger.warning("TG-AI connectivity check skipped: resolver disabled")
        return {**base, "error": "TG-AI resolver is disabled"}
    if not settings.openai_api_key:
        logger.error("TG-AI connectivity check skipped: OPENAI_API_KEY missing")
        return {**base, "error": "OPENAI_API_KEY is not configured"}

    logger.info("TG-AI connectivity check started model=%s", settings.tg_ai_resolver_model)
    try:
        response = _openai_post(input_text="Reply exactly TG_AI_OK")
        payload = response.json()
        text = _extract_output_text(payload)
        result = {
            **base,
            "ok": True,
            "response_id": payload.get("id"),
            "response_text": text,
        }
        logger.info(
            "TG-AI connectivity check succeeded response_id=%s response_text=%r",
            result["response_id"], text[:80],
        )
        return result
    except Exception as exc:
        logger.exception("TG-AI connectivity check failed stage=request_or_response")
        return {**base, "error": f"{type(exc).__name__}: {exc}"}


def resolve_classification_dispute(db: Session, subject: V2Subject) -> ResolverDecision | None:
    settings = get_settings()
    logger.info(
        "TG-AI resolver entered subject=%s enabled=%s api_key_present=%s model=%s",
        subject.id,
        settings.tg_ai_resolver_enabled,
        bool(settings.openai_api_key),
        settings.tg_ai_resolver_model,
    )
    if not settings.tg_ai_resolver_enabled:
        logger.warning("TG-AI resolver skipped subject=%s reason=disabled", subject.id)
        return None
    if not settings.openai_api_key:
        logger.error("TG-AI resolver skipped subject=%s reason=missing_api_key", subject.id)
        return None

    decisions = list(db.scalars(select(SubjectClassificationDecision).where(
        SubjectClassificationDecision.subject_id == subject.id,
        SubjectClassificationDecision.classification_version == subject.classification_version,
        SubjectClassificationDecision.outcome == "candidate",
    ).order_by(SubjectClassificationDecision.created_at)).all())
    logger.info("TG-AI resolver candidates loaded subject=%s count=%s", subject.id, len(decisions))
    if len(decisions) < 2:
        logger.warning("TG-AI resolver skipped subject=%s reason=insufficient_candidate_decisions", subject.id)
        return None

    candidate_types = {}
    case_decisions = []
    for decision in decisions:
        target = db.get(SubjectType, decision.target_type_id)
        if target is None:
            logger.warning(
                "TG-AI resolver candidate target missing subject=%s decision=%s target_type_id=%s",
                subject.id, decision.id, decision.target_type_id,
            )
            continue
        candidate_types[target.canonical_name] = target
        case_decisions.append({
            "target_subject_type": target.canonical_name,
            "source_model": decision.source_model,
            "reason": decision.reason,
            "evidence": decision.evidence_json,
        })
    if len(candidate_types) < 2:
        logger.warning("TG-AI resolver skipped subject=%s reason=insufficient_distinct_candidates", subject.id)
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
    logger.info(
        "TG-AI resolver request prepared subject=%s candidates=%s",
        subject.id, list(candidate_types),
    )
    response = _openai_post(input_text=prompt)
    payload = response.json()
    logger.info(
        "TG-AI resolver response decoding subject=%s response_id=%s",
        subject.id, payload.get("id"),
    )
    output_text = _extract_output_text(payload)
    logger.info("TG-AI resolver output extracted subject=%s chars=%s", subject.id, len(output_text))
    decision = ResolverDecision.model_validate_json(output_text)
    logger.info(
        "TG-AI resolver output validated subject=%s target=%s confidence=%s action=%s",
        subject.id, decision.target_subject_type, decision.confidence, decision.action,
    )
    if decision.action != "select_candidate":
        raise ValueError("Resolver action must be select_candidate")
    if decision.target_subject_type not in candidate_types:
        raise ValueError("Resolver selected a type outside the current candidate set")
    logger.info("TG-AI resolver decision accepted subject=%s target=%s", subject.id, decision.target_subject_type)
    return decision
