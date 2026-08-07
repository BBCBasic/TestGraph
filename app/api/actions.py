from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.capability import _credential, _headers
from app.core.config import get_settings
from app.db.session import get_db
from app.models.v2 import Concept, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, FieldProposal, SubjectEnsure
from app.services.v2 import create_assessment, create_experience, ensure_concept, ensure_subject, vocabulary

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
        return _credential(db, token.strip())
    except HTTPException as exc:
        raise HTTPException(401, "Invalid TasteGraph API key") from exc


def _experience_payload(exp: V2Experience, subject: V2Subject, concept: Concept) -> dict:
    return {
        "id": str(exp.id),
        "subject": {"id": str(subject.id), "name": subject.name, "canonical_key": subject.canonical_key, "concept_path": concept.path, "identifiers": subject.identifiers_json, "attributes": subject.attributes_json},
        "headline": exp.headline,
        "summary": exp.summary,
        "raw_text": exp.raw_text,
        "structured_data": exp.structured_data,
        "submitted_data": exp.submitted_data,
        "normalization_log": exp.normalization_log,
        "visibility": exp.visibility,
        "provenance": exp.provenance,
        "created_at": exp.created_at.isoformat(),
    }


@router.get("/experiences", operation_id="searchTasteGraphExperiences")
def search_experiences(q: str = "", concept_path: str | None = None, limit: int = Query(default=10, ge=1, le=20), authorization: str | None = Header(default=None, alias="Authorization"), db: Session = Depends(get_db)):
    cred = _action_credential(db, authorization)
    stmt = select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.owner_id == cred.user_id, V2Experience.deleted_at.is_(None))
    if concept_path:
        stmt = stmt.where(Concept.path == concept_path)
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(V2Subject.name.ilike(pattern), V2Subject.canonical_key.ilike(pattern), V2Experience.headline.ilike(pattern), V2Experience.summary.ilike(pattern)))
    rows = db.execute(stmt.order_by(V2Experience.created_at.desc()).limit(limit)).all()
    return JSONResponse({"count": len(rows), "results": [{"id": str(exp.id), "subject_id": str(subject.id), "subject_name": subject.name, "concept_path": concept.path, "headline": exp.headline, "summary": exp.summary} for exp, subject, concept in rows]}, headers=_headers())


@router.get("/experiences/{experience_id}", operation_id="fetchTasteGraphExperience")
def fetch_experience(experience_id: uuid.UUID, authorization: str | None = Header(default=None, alias="Authorization"), db: Session = Depends(get_db)):
    cred = _action_credential(db, authorization)
    row = db.execute(select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.id == experience_id, V2Experience.owner_id == cred.user_id, V2Experience.deleted_at.is_(None))).first()
    if not row:
        raise HTTPException(404, "Experience not found")
    return JSONResponse(_experience_payload(*row), headers=_headers())


@router.get("/concepts", operation_id="getTasteGraphConcept")
def get_concept(path: str, authorization: str | None = Header(default=None, alias="Authorization"), db: Session = Depends(get_db)):
    _action_credential(db, authorization)
    from app.services.v2 import normalise_path
    canonical = normalise_path(path)
    concept = db.scalar(select(Concept).where(Concept.path == canonical, Concept.status == "active"))
    if not concept:
        return JSONResponse({"found": False, "path": canonical, "instruction": "A new concept can be created when saving. Reuse existing canonical fields where possible; include proposed_fields only for genuinely new dimensions."}, headers=_headers())
    vocab = vocabulary(db, concept)
    unique = {field.id: field for field in vocab["fields"].values()}
    return JSONResponse({"found": True, "path": concept.path, "version": concept.version, "description": concept.description, "fields": [{"canonical_name": f.canonical_name, "data_type": f.data_type, "description": f.description, "unit": f.unit, "origin": vocab["origins"].get(f.canonical_name)} for f in unique.values()], "aliases": vocab["aliases"]}, headers=_headers())


@router.post("/experiences", operation_id="saveTasteGraphExperience", status_code=201)
def save_experience(payload: dict, request: Request, authorization: str | None = Header(default=None, alias="Authorization"), db: Session = Depends(get_db)):
    cred = _action_credential(db, authorization)
    if payload.get("user_approved") is not True:
        raise HTTPException(400, "Explicit user approval is required before saving a direct experience")
    try:
        proposals = [FieldProposal.model_validate(p) for p in payload.get("proposed_fields", [])]
        concept = ensure_concept(db, ConceptEnsure(path=payload["concept_path"], description=payload.get("concept_description"), proposed_fields=proposals, created_by=payload.get("source_client", "chatgpt-action")))
        subject = ensure_subject(db, SubjectEnsure(concept_path=concept.path, name=payload["subject_name"], canonical_key=payload["canonical_key"], identifiers=payload.get("identifiers", {}), attributes=payload.get("subject_attributes", {})), "chatgpt-action")
        create = ExperienceCreate(owner_id=cred.user_id, subject_id=subject.id, headline=payload["headline"], summary=payload["summary"], raw_text=payload.get("raw_text"), structured_data=payload.get("structured_data", {}), proposed_fields=proposals, visibility=payload.get("visibility", "private"), user_approved=True, source_client=payload.get("source_client", "chatgpt-action"))
        exp = create_experience(db, create, f"capability:{cred.id}")
    except (KeyError, ValueError) as exc:
        db.rollback(); raise HTTPException(422, exc.args[0] if exc.args else str(exc))
    return JSONResponse({"saved": True, "experience_id": str(exp.id), "subject_id": str(subject.id), "concept_path": concept.path, "canonical_data": exp.structured_data, "normalization_log": exp.normalization_log, "read_back": f"{_base()}/actions/experiences/{exp.id}"}, status_code=201, headers=_headers())


@router.post("/assessments", operation_id="saveTasteGraphAssessment", status_code=201)
def save_assessment(payload: dict, authorization: str | None = Header(default=None, alias="Authorization"), db: Session = Depends(get_db)):
    cred = _action_credential(db, authorization)
    try:
        create = AssessmentCreate(subject_id=payload["subject_id"], user_id=cred.user_id, assessment_type=payload["assessment_type"], evidence=payload.get("evidence", {}), analysis=payload.get("analysis", {}), conclusion=payload.get("conclusion"), confidence=payload.get("confidence"), source_model=payload.get("source_model", "chatgpt"), provenance=payload.get("provenance", {}))
        obj = create_assessment(db, create)
    except (KeyError, ValueError) as exc:
        db.rollback(); raise HTTPException(422, str(exc))
    return JSONResponse({"saved": True, "assessment_id": str(obj.id), "subject_id": str(obj.subject_id), "provenance_kind": obj.provenance.get("kind")}, status_code=201, headers=_headers())


@router.get("/openapi.json", include_in_schema=False)
def actions_openapi():
    field_proposal = {
        "type": "object", "additionalProperties": False,
        "required": ["submitted_name", "canonical_name"],
        "properties": {"submitted_name": {"type": "string"}, "canonical_name": {"type": "string"}, "data_type": {"type": "string", "default": "any"}, "description": {"type": "string"}, "unit": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}}}
    }
    experience = {
        "type": "object", "additionalProperties": False,
        "required": ["concept_path", "subject_name", "canonical_key", "headline", "summary", "user_approved"],
        "properties": {
            "concept_path": {"type": "string", "description": "Hierarchical path e.g. place.restaurant or product.electronics.camera.action_camera"},
            "concept_description": {"type": "string"}, "subject_name": {"type": "string"}, "canonical_key": {"type": "string"},
            "identifiers": {"type": "object", "additionalProperties": True}, "subject_attributes": {"type": "object", "additionalProperties": True},
            "headline": {"type": "string"}, "summary": {"type": "string"}, "raw_text": {"type": "string"},
            "structured_data": {"type": "object", "additionalProperties": True, "description": "AI's best structured interpretation. TasteGraph canonicalises field names before storage."},
            "proposed_fields": {"type": "array", "items": field_proposal, "description": "Only for genuinely new dimensions after checking the concept vocabulary."},
            "visibility": {"type": "string", "enum": ["private", "unlisted", "public", "aggregate_only"], "default": "private"},
            "user_approved": {"type": "boolean"}, "source_client": {"type": "string", "default": "chatgpt-action"}
        }
    }
    assessment = {
        "type": "object", "additionalProperties": False, "required": ["subject_id", "assessment_type"],
        "properties": {"subject_id": {"type": "string", "format": "uuid"}, "assessment_type": {"type": "string"}, "evidence": {"type": "object", "additionalProperties": True}, "analysis": {"type": "object", "additionalProperties": True}, "conclusion": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "source_model": {"type": "string"}, "provenance": {"type": "object", "additionalProperties": True}}
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "TasteGraph v2 ChatGPT Action", "version": "2.0.0-alpha", "description": "Cross-AI experience memory with a self-organising concept registry. Direct experiences require explicit approval; AI-derived assessments remain distinct from user experiences."},
        "servers": [{"url": _base()}],
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "description": "TasteGraph capability key beginning tg_"}}, "schemas": {"ExperienceCreate": experience, "AssessmentCreate": assessment}},
        "security": [{"bearerAuth": []}],
        "paths": {
            "/actions/experiences": {
                "get": {"operationId": "searchTasteGraphExperiences", "summary": "Search the user's experiences", "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}, {"name": "concept_path", "in": "query", "schema": {"type": "string"}}, {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20}}], "responses": {"200": {"description": "Experiences"}}},
                "post": {"operationId": "saveTasteGraphExperience", "summary": "Save an approved direct user experience", "description": "Check getTasteGraphConcept first when possible. Only call after explicit user approval. TasteGraph normalises submitted fields; use proposed_fields only for genuinely new concepts.", "x-openai-isConsequential": True, "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ExperienceCreate"}}}}, "responses": {"201": {"description": "Experience saved"}, "422": {"description": "Canonicalisation error"}}}
            },
            "/actions/experiences/{experience_id}": {"get": {"operationId": "fetchTasteGraphExperience", "summary": "Fetch a complete experience", "parameters": [{"name": "experience_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}], "responses": {"200": {"description": "Experience"}}}},
            "/actions/concepts": {"get": {"operationId": "getTasteGraphConcept", "summary": "Get canonical concept vocabulary", "parameters": [{"name": "path", "in": "query", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "Concept vocabulary or not-found guidance"}}}},
            "/actions/assessments": {"post": {"operationId": "saveTasteGraphAssessment", "summary": "Save AI-derived analysis separately from user experience", "x-openai-isConsequential": True, "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AssessmentCreate"}}}}, "responses": {"201": {"description": "Assessment saved"}}}}
        }
    }
