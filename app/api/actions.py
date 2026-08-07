from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.capability import CapabilityReviewCreate, _credential, _headers, _save_review
from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import Experience, SchemaDefinition, Subject
from app.schemas.common import CommonExperienceData

router = APIRouter(prefix="/actions", tags=["ChatGPT Actions"])


def _base() -> str:
    return get_settings().public_base_url.rstrip("/")


def _action_credential(db: Session, authorization: str | None):
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Use Authorization: Bearer <TasteGraph capability key>")
    try:
        return _credential(db, token.strip()), token.strip()
    except HTTPException as exc:
        raise HTTPException(401, "Invalid TasteGraph API key") from exc


def _review_payload(exp: Experience, subject: Subject) -> dict:
    return {
        "id": str(exp.id),
        "subject": {
            "id": str(subject.id),
            "type": subject.subject_type,
            "name": subject.name,
            "canonical_key": subject.canonical_key,
            "canonical_identifiers": subject.canonical_identifiers,
            "metadata": subject.metadata_json,
        },
        "headline": exp.headline,
        "summary": exp.summary,
        "common_data": exp.common_data,
        "domain_data": exp.domain_data,
        "visibility": exp.visibility,
        "publication_status": exp.publication_status,
        "created_at": exp.created_at.isoformat(),
        "updated_at": exp.updated_at.isoformat(),
    }


@router.get("/reviews", operation_id="searchTasteGraphReviews")
def search_reviews(
    q: str = Query(default="", description="Optional words from subject, headline, or review. Leave blank for recent reviews."),
    subject_type: Literal["recipe", "restaurant"] | None = None,
    limit: int = Query(default=10, ge=1, le=20),
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    cred, _ = _action_credential(db, authorization)
    stmt = select(Experience, Subject).join(Subject, Experience.subject_id == Subject.id).where(
        Experience.owner_id == cred.user_id,
        Experience.deleted_at.is_(None),
        Subject.deleted_at.is_(None),
    )
    if subject_type:
        stmt = stmt.where(Experience.subject_type == subject_type)
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Subject.name.ilike(pattern),
            Subject.canonical_key.ilike(pattern),
            Experience.headline.ilike(pattern),
            Experience.summary.ilike(pattern),
        ))
    rows = db.execute(stmt.order_by(Experience.created_at.desc()).limit(limit)).all()
    results = [{
        "id": str(exp.id),
        "subject_type": exp.subject_type,
        "subject_name": subject.name,
        "headline": exp.headline,
        "summary": exp.summary,
        "visibility": exp.visibility,
        "publication_status": exp.publication_status,
        "url": f"{_base()}/actions/reviews/{exp.id}",
    } for exp, subject in rows]
    return JSONResponse({"count": len(results), "results": results}, headers=_headers())


@router.get("/reviews/{experience_id}", operation_id="fetchTasteGraphReview")
def fetch_review(
    experience_id: uuid.UUID,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    cred, _ = _action_credential(db, authorization)
    row = db.execute(select(Experience, Subject).join(Subject, Experience.subject_id == Subject.id).where(
        Experience.id == experience_id,
        Experience.owner_id == cred.user_id,
        Experience.deleted_at.is_(None),
        Subject.deleted_at.is_(None),
    )).first()
    if not row:
        raise HTTPException(404, "Review not found")
    exp, subject = row
    return JSONResponse(_review_payload(exp, subject), headers=_headers())


@router.get("/schemas/{subject_type}", operation_id="getTasteGraphSchema")
def get_schema(
    subject_type: Literal["recipe", "restaurant"],
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    _action_credential(db, authorization)
    row = db.scalar(select(SchemaDefinition).where(
        SchemaDefinition.subject_type == subject_type,
        SchemaDefinition.status == "stable",
    ).order_by(SchemaDefinition.version.desc()))
    if not row:
        raise HTTPException(404, "Schema not found")
    return JSONResponse({
        "subject_type": row.subject_type,
        "version": row.version,
        "common_data_schema": CommonExperienceData.model_json_schema(),
        "domain_data_schema": row.json_schema,
        "instruction": "Before saving, use only fields permitted by these schemas. Omit unknown fields rather than inventing them.",
    }, headers=_headers())


@router.post("/reviews", operation_id="saveTasteGraphReview", status_code=201)
def save_review(
    payload: CapabilityReviewCreate,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    cred, key = _action_credential(db, authorization)
    try:
        body, status = _save_review(db, cred, key, payload, request.state.request_id)
    except ValidationError as exc:
        db.rollback()
        raise HTTPException(422, {"message": "Review validation failed", "errors": exc.errors()}) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, f"Review validation failed: {exc}") from exc
    safe = dict(body)
    safe["url"] = f"{_base()}/actions/reviews/{safe['experience_id']}"
    safe["read_back"] = safe["url"]
    safe["instruction"] = "Fetch read_back to verify the stored review."
    return JSONResponse(safe, status_code=status, headers=_headers())


@router.get("/openapi.json", include_in_schema=False)
def actions_openapi():
    """OpenAPI document kept simple enough for ChatGPT Actions import."""
    rating = {"type": "number", "minimum": 0, "maximum": 10}
    observation = {
        "type": "object",
        "additionalProperties": False,
        "required": ["category", "statement", "confidence"],
        "properties": {
            "category": {"type": "string"},
            "statement": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_type": {"type": "string", "enum": ["asserted", "elicited", "assented", "inferred"], "default": "asserted"},
            "supporting_quote": {"type": "string"},
            "source_reference": {"type": "string"}
        }
    }
    impression = {
        "type": "object",
        "additionalProperties": False,
        "required": ["category", "statement", "sentiment", "importance_to_reviewer"],
        "properties": {
            "category": {"type": "string"},
            "statement": {"type": "string"},
            "sentiment": {"type": "number", "minimum": -1, "maximum": 1},
            "importance_to_reviewer": {"type": "number", "minimum": 0, "maximum": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8},
            "evidence_type": {"type": "string", "enum": ["asserted", "elicited", "assented", "inferred"], "default": "asserted"},
            "supporting_quote": {"type": "string"},
            "source_reference": {"type": "string"}
        }
    }
    dish = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "rating": rating,
            "notes": {"type": "string"},
            "shared": {"type": "boolean", "default": False},
            "would_order_again": {"type": "boolean"}
        }
    }
    review_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "subject_type", "subject_name", "canonical_key", "headline", "summary",
            "common_data", "domain_data", "user_approved", "idempotency_key"
        ],
        "properties": {
            "subject_type": {"type": "string", "enum": ["recipe", "restaurant"]},
            "subject_name": {"type": "string"},
            "canonical_key": {"type": "string"},
            "canonical_identifiers": {"type": "object", "additionalProperties": True},
            "subject_metadata": {"type": "object", "additionalProperties": True},
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "common_data": {
                "type": "object",
                "description": "Common review structure. Empty arrays/objects are valid when no common observations were captured.",
                "additionalProperties": False,
                "properties": {
                    "observations": {"type": "array", "items": observation, "default": []},
                    "subjective_impressions": {"type": "array", "items": impression, "default": []},
                    "strengths": {"type": "array", "items": {"type": "string"}, "default": []},
                    "weaknesses": {"type": "array", "items": {"type": "string"}, "default": []},
                    "would_repeat": {"type": "boolean"},
                    "special_journey_worthy": {"type": "boolean"},
                    "confidence": {"type": "object", "additionalProperties": {"type": "number"}, "default": {}}
                }
            },
            "domain_data": {
                "type": "object",
                "description": "Structured domain fields. Call getTasteGraphSchema first. Restaurant fields include overall_rating, food and service. Recipe fields include overall_rating, flavour and difficulty.",
                "additionalProperties": False,
                "properties": {
                    "overall_rating": rating,
                    "food": rating,
                    "service": rating,
                    "atmosphere": rating,
                    "value": rating,
                    "noise_comfort": rating,
                    "meal_pacing": rating,
                    "drinks": rating,
                    "visit_date": {"type": "string", "format": "date"},
                    "meal_type": {"type": "string", "enum": ["breakfast", "brunch", "lunch", "afternoon_tea", "dinner", "late_night", "other"]},
                    "party_size": {"type": "integer", "minimum": 1, "maximum": 100},
                    "occasion": {"type": "string"},
                    "wait_minutes": {"type": "integer", "minimum": 0, "maximum": 1440},
                    "spend_per_person": {"type": "number", "minimum": 0},
                    "currency": {"type": "string", "minLength": 3, "maxLength": 3},
                    "dishes": {"type": "array", "items": dish, "default": []},
                    "standout_dishes": {"type": "array", "items": {"type": "string"}, "default": []},
                    "disappointing_dishes": {"type": "array", "items": {"type": "string"}, "default": []},
                    "flavour": rating,
                    "instruction_clarity": rating,
                    "preparation_time_accuracy": rating,
                    "ingredient_availability": rating,
                    "difficulty": rating,
                    "repeat_worthiness": rating,
                    "modifications": {"type": "array", "items": {"type": "string"}, "default": []}
                }
            },
            "visibility": {"type": "string", "enum": ["private", "unlisted", "public", "aggregate_only"], "default": "private"},
            "user_approved": {"type": "boolean", "description": "Must be true only after explicit user approval."},
            "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
            "source_client": {"type": "string", "default": "chatgpt-action"}
        }
    }
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "TasteGraph ChatGPT Actions",
            "version": "1.0.2",
            "description": "Private read/write access to a user's TasteGraph review memory. Saving requires explicit user approval in the conversation."
        },
        "servers": [{"url": _base()}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "description": "TasteGraph capability key beginning tg_"}
            },
            "schemas": {"ReviewCreate": review_schema}
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/actions/reviews": {
                "get": {
                    "operationId": "searchTasteGraphReviews",
                    "summary": "Search or list the user's TasteGraph reviews",
                    "description": "Use for prior reviews or recent review memory. Leave q blank to get recent reviews.",
                    "parameters": [
                        {"name": "q", "in": "query", "required": False, "schema": {"type": "string", "default": ""}},
                        {"name": "subject_type", "in": "query", "required": False, "schema": {"type": "string", "enum": ["recipe", "restaurant"]}},
                        {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}}
                    ],
                    "responses": {"200": {"description": "Matching reviews"}}
                },
                "post": {
                    "operationId": "saveTasteGraphReview",
                    "summary": "Save an explicitly approved TasteGraph review",
                    "description": "Call only after the user has explicitly approved the completed review. common_data and domain_data are required. Set user_approved=true. Use getTasteGraphSchema first and reuse the same idempotency_key when retrying the same review.",
                    "x-openai-isConsequential": True,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReviewCreate"}}}
                    },
                    "responses": {"201": {"description": "Review saved"}, "422": {"description": "Schema validation error"}}
                }
            },
            "/actions/reviews/{experience_id}": {
                "get": {
                    "operationId": "fetchTasteGraphReview",
                    "summary": "Fetch one complete TasteGraph review",
                    "parameters": [{"name": "experience_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                    "responses": {"200": {"description": "Complete stored review"}, "404": {"description": "Not found"}}
                }
            },
            "/actions/schemas/{subject_type}": {
                "get": {
                    "operationId": "getTasteGraphSchema",
                    "summary": "Get the exact common and domain schemas before saving",
                    "description": "Use before saveTasteGraphReview whenever fields are uncertain.",
                    "parameters": [{"name": "subject_type", "in": "path", "required": True, "schema": {"type": "string", "enum": ["recipe", "restaurant"]}}],
                    "responses": {"200": {"description": "Common and domain JSON schemas"}}
                }
            }
        }
    }
    return JSONResponse(spec, headers={"Cache-Control": "no-store"})
