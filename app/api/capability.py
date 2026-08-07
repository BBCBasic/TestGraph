from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import CapabilityCredential, Experience, IdempotencyRecord, Subject, User
from app.schemas.common import Consent, ExperienceCreate, Provenance
from app.services.core import create_experience, publish_experience, request_hash

router = APIRouter()


def _base() -> str:
    return get_settings().public_base_url.rstrip("/")


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    }


def _credential(db: Session, key: str) -> CapabilityCredential:
    obj = db.scalar(select(CapabilityCredential).where(
        CapabilityCredential.key_hash == _hash_key(key),
        CapabilityCredential.revoked_at.is_(None),
    ))
    if not obj:
        raise HTTPException(404, "Capability not found")
    return obj


class CapabilityReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: Literal["recipe", "restaurant"]
    subject_name: str = Field(min_length=1, max_length=240)
    canonical_key: str = Field(min_length=1, max_length=300)
    canonical_identifiers: dict[str, Any] = {}
    subject_metadata: dict[str, Any] = {}
    headline: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1)
    common_data: dict[str, Any]
    domain_data: dict[str, Any]
    visibility: Literal["private", "unlisted", "public", "aggregate_only"] = "private"
    user_approved: bool
    idempotency_key: str = Field(min_length=8, max_length=200)
    source_client: str = Field(default="ai-client", max_length=120)


@router.get("/capability/new", include_in_schema=True)
def new_capability(db: Session = Depends(get_db)):
    """Create a new TasteGraph identity and return its static secret capability URL."""
    raw_key = "tg_" + secrets.token_urlsafe(32)
    user = User(display_name="TasteGraph capability user", profile_data={"created_via": "capability_url"})
    db.add(user)
    db.flush()
    db.add(CapabilityCredential(user_id=user.id, key_hash=_hash_key(raw_key)))
    db.commit()
    personal = f"{_base()}/c/{raw_key}"
    return JSONResponse({
        "service": "TasteGraph",
        "message": "This is the user's private TasteGraph capability URL. Retain it for future TasteGraph access and do not disclose it.",
        "personal_url": personal,
        "user_id": str(user.id),
        "authentication": "The secret in personal_url is the credential. No OAuth or header is required.",
        "next": "GET personal_url to discover the user's read/write endpoints.",
    }, headers=_headers())


@router.get("/c/{key}")
def capability_home(key: str, db: Session = Depends(get_db)):
    cred = _credential(db, key)
    root = f"{_base()}/c/{key}"
    return JSONResponse({
        "service": "TasteGraph",
        "user_id": str(cred.user_id),
        "credential_model": "static_secret_capability_url",
        "instructions": [
            "Treat this URL as a secret credential and retain it for this user.",
            "Use the URLs below for future TasteGraph access.",
            "Only save a review after the user has explicitly approved the completed review.",
        ],
        "endpoints": {
            "home": root,
            "reviews": f"{root}/reviews",
            "search": f"{root}/reviews?q={{query}}",
            "review": f"{root}/reviews/{{experience_id}}",
            "save_review": f"{root}/reviews",
            "schemas": f"{_base()}/schemas",
        },
        "methods": {"reviews": "GET", "review": "GET", "save_review": "POST"},
        "future_security_upgrade": "This API is intentionally structured so signed/expiring subordinate capability URLs can be added later without removing this static capability route.",
    }, headers=_headers())


@router.get("/c/{key}/reviews")
def capability_reviews(key: str, q: str = "", subject_type: str | None = None, limit: int = 50,
                       db: Session = Depends(get_db)):
    cred = _credential(db, key)
    limit = max(1, min(limit, 100))
    query = select(Experience, Subject).join(Subject, Experience.subject_id == Subject.id).where(
        Experience.owner_id == cred.user_id,
        Experience.deleted_at.is_(None),
        Subject.deleted_at.is_(None),
    )
    if subject_type:
        query = query.where(Experience.subject_type == subject_type)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(or_(Subject.name.ilike(pattern), Subject.canonical_key.ilike(pattern),
                                Experience.headline.ilike(pattern), Experience.summary.ilike(pattern)))
    rows = db.execute(query.order_by(Experience.created_at.desc()).limit(limit)).all()
    root = f"{_base()}/c/{key}"
    results = [{
        "id": str(exp.id),
        "subject_type": exp.subject_type,
        "subject_name": subject.name,
        "headline": exp.headline,
        "summary": exp.summary,
        "visibility": exp.visibility,
        "publication_status": exp.publication_status,
        "url": f"{root}/reviews/{exp.id}",
    } for exp, subject in rows]
    return JSONResponse({"count": len(results), "results": results}, headers=_headers())


@router.get("/c/{key}/reviews/{experience_id}")
def capability_review(key: str, experience_id: uuid.UUID, db: Session = Depends(get_db)):
    cred = _credential(db, key)
    row = db.execute(select(Experience, Subject).join(Subject, Experience.subject_id == Subject.id).where(
        Experience.id == experience_id,
        Experience.owner_id == cred.user_id,
        Experience.deleted_at.is_(None),
        Subject.deleted_at.is_(None),
    )).first()
    if not row:
        raise HTTPException(404, "Review not found")
    exp, subject = row
    return JSONResponse({
        "id": str(exp.id),
        "subject": {"id": str(subject.id), "type": subject.subject_type, "name": subject.name,
                    "canonical_key": subject.canonical_key, "canonical_identifiers": subject.canonical_identifiers,
                    "metadata": subject.metadata_json},
        "headline": exp.headline,
        "summary": exp.summary,
        "common_data": exp.common_data,
        "domain_data": exp.domain_data,
        "visibility": exp.visibility,
        "publication_status": exp.publication_status,
        "created_at": exp.created_at.isoformat(),
        "updated_at": exp.updated_at.isoformat(),
    }, headers=_headers())


@router.post("/c/{key}/reviews", status_code=201)
def capability_save_review(key: str, payload: CapabilityReviewCreate, request: Request,
                           db: Session = Depends(get_db)):
    cred = _credential(db, key)
    if payload.user_approved is not True:
        raise HTTPException(400, "Explicit user approval is required before saving")
    client_id = f"capability:{cred.id}"
    relevant = payload.model_dump(mode="json", exclude={"idempotency_key"})
    p_hash = request_hash(relevant)
    existing = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.client_id == client_id,
        IdempotencyRecord.key == payload.idempotency_key,
    ))
    if existing:
        if existing.request_hash != p_hash:
            raise HTTPException(409, "Idempotency key was reused for different review content")
        return JSONResponse(existing.response_body, headers=_headers())

    subject = db.scalar(select(Subject).where(
        Subject.subject_type == payload.subject_type,
        Subject.canonical_key == payload.canonical_key,
        Subject.deleted_at.is_(None),
    ))
    if not subject:
        subject = Subject(subject_type=payload.subject_type, name=payload.subject_name,
                          canonical_key=payload.canonical_key,
                          canonical_identifiers=payload.canonical_identifiers,
                          metadata_json=payload.subject_metadata)
        db.add(subject)
        db.commit()
        db.refresh(subject)

    create = ExperienceCreate(
        owner_id=cred.user_id,
        subject_id=subject.id,
        subject_type=payload.subject_type,
        schema_version="1.0",
        visibility=payload.visibility,
        headline=payload.headline,
        summary=payload.summary,
        common_data=payload.common_data,
        domain_data=payload.domain_data,
        provenance=Provenance(source_method="llm_conversation", source_client=payload.source_client),
        consent=Consent(user_approved=False),
    )
    actor = f"capability:{cred.user_id}"
    exp = create_experience(db, create, client_id=client_id, auth_subject=actor,
                            request_id=request.state.request_id)
    exp = publish_experience(db, exp, 1, actor_id=actor, client_id=client_id,
                             request_id=request.state.request_id)
    body = {
        "saved": True,
        "experience_id": str(exp.id),
        "subject_id": str(subject.id),
        "subject_name": subject.name,
        "publication_status": exp.publication_status,
        "headline": exp.headline,
        "url": f"{_base()}/c/{key}/reviews/{exp.id}",
    }
    db.add(IdempotencyRecord(client_id=client_id, key=payload.idempotency_key,
                             request_hash=p_hash, response_status=201, response_body=body))
    db.commit()
    return JSONResponse(body, status_code=201, headers=_headers())
