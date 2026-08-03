from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.entities import AuditEvent, Experience, PairwiseAlignment, User
from app.schemas.common import ExperienceCreate
from app.schemas.domains import DOMAIN_MODELS


def validate_domain(subject_type: str, version: str, payload: dict) -> dict:
    model = DOMAIN_MODELS.get(subject_type)
    if not model:
        raise ValueError(f"Unsupported subject_type: {subject_type}")
    return model.model_validate(payload).model_dump(mode="json")


def request_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def audit(db: Session, *, actor_id: str, client_id: str, action: str, object_type: str, object_id: str, request_id: str, details: dict | None = None):
    db.add(AuditEvent(actor_id=actor_id, client_id=client_id, action=action, object_type=object_type, object_id=object_id, request_id=request_id, details=details or {}))


def create_experience(db: Session, payload: ExperienceCreate, *, client_id: str, auth_subject: str, request_id: str) -> Experience:
    domain_data = validate_domain(payload.subject_type, payload.schema_version, payload.domain_data)
    obj = Experience(
        owner_id=payload.owner_id, subject_id=payload.subject_id, subject_type=payload.subject_type,
        schema_version=payload.schema_version, visibility=payload.visibility, headline=payload.headline,
        summary=payload.summary, common_data=payload.common_data.model_dump(mode="json"), domain_data=domain_data,
        provenance=payload.provenance.model_dump(mode="json"), consent=payload.consent.model_dump(mode="json"),
        created_by_client=client_id, auth_subject=auth_subject,
    )
    db.add(obj); db.flush()
    audit(db, actor_id=auth_subject, client_id=client_id, action="draft_created", object_type="experience", object_id=str(obj.id), request_id=request_id)
    db.commit(); db.refresh(obj)
    return obj


def publish_experience(db: Session, obj: Experience, approved_version: int, *, actor_id: str, client_id: str, request_id: str) -> Experience:
    if obj.version != approved_version:
        raise ValueError("approved_version does not match current draft version")
    consent = dict(obj.consent or {})
    consent.update({"user_approved": True, "approved_at": datetime.now(timezone.utc).isoformat(), "approved_version": approved_version})
    obj.consent = consent
    obj.publication_status = "published"
    obj.published_at = datetime.now(timezone.utc)
    audit(db, actor_id=actor_id, client_id=client_id, action="review_published", object_type="experience", object_id=str(obj.id), request_id=request_id)
    db.commit(); db.refresh(obj)
    return obj


def personalised(db: Session, obj: Experience, reader: User) -> dict:
    source = db.get(User, obj.owner_id)
    alignment = db.scalar(select(PairwiseAlignment).where(PairwiseAlignment.source_user_id == source.id, PairwiseAlignment.target_user_id == reader.id))
    pair = alignment.dimensions if alignment else {}
    reader_weights = (reader.profile_data or {}).get("dimension_importance", {})
    dims = []
    for imp in (obj.common_data or {}).get("subjective_impressions", []):
        d = imp["category"]
        reviewer_sentiment = float(imp.get("sentiment", 0))
        reader_importance = float(reader_weights.get(d, 0.5))
        pairwise = float(pair.get(d, 0.5))
        relevance = round(0.55 * reader_importance + 0.45 * pairwise, 3)
        level = "High" if relevance >= 0.7 else "Low" if relevance < 0.4 else "Moderate"
        dims.append({"dimension": d, "reviewer_sentiment": reviewer_sentiment, "reader_importance": reader_importance, "pairwise_alignment": pairwise, "relevance": relevance, "explanation": f"{level} relevance for {reader.display_name}: importance {reader_importance:.2f}, alignment {pairwise:.2f}."})
    dims.sort(key=lambda x: x["relevance"], reverse=True)
    overall = round(sum(d["relevance"] for d in dims) / len(dims), 3) if dims else 0.0
    top = ", ".join(d["dimension"] for d in dims[:2]) or "the available evidence"
    low = ", ".join(d["dimension"] for d in dims[-2:] if d["relevance"] < 0.4)
    conclusion = f"Give most weight to {top}." + (f" Give less weight to {low}." if low else "")
    return {"experience_id": obj.id, "reader_id": reader.id, "overall_relevance": overall, "reader_specific_conclusion": conclusion, "dimensions": dims}
