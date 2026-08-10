from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Principal, TokenError, principal_from_authorization
from app.db.session import get_db
from app.models.v2 import Assessment, Concept, ConceptFieldProposal, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, FieldProposal, SubjectEnsure
from app.services.semantic import list_alias_candidates, propose_alias
from app.services.v2 import (
    create_assessment,
    create_experience,
    ensure_subject,
    list_field_proposals,
    normalise_path,
    normalise_token,
    propose_concept_fields,
    reject_field_proposal,
    vocabulary,
)
from app.services.vocabulary_governance import ensure_proposed_concept, verify_field_proposal, vocabulary_index
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write

router = APIRouter()
PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "2.3.0-alpha"
READ_SECURITY = [{"type": "oauth2", "scopes": ["reviews:read"]}]
WRITE_SECURITY = [{"type": "oauth2", "scopes": ["reviews:write"]}]


def _security(schemes):
    return {"securitySchemes": schemes, "_meta": {"securitySchemes": schemes}}


def _base():
    return get_settings().public_base_url.rstrip("/")


def _text(payload):
    return [{"type": "text", "text": json.dumps(payload, default=str, separators=(",", ":"))}]


def _result(payload):
    return {"content": _text(payload), "structuredContent": payload}


def _error(message, details=None):
    payload = {"error": message}
    if details is not None:
        payload["details"] = details
    return {"content": _text(payload), "structuredContent": payload, "isError": True}


def _auth_error(message):
    challenge = (
        f'Bearer resource_metadata="{_base()}/.well-known/oauth-protected-resource/mcp-v2", '
        f'error="insufficient_scope", error_description="{message}"'
    )
    return {
        "content": [{"type": "text", "text": f"Authentication required: {message}."}],
        "isError": True,
        "_meta": {"mcp/www_authenticate": [challenge]},
    }


def _principal(request: Request, scope: str) -> Principal:
    return principal_from_authorization(
        request.headers.get("authorization"),
        scope,
        expected_resource=f"{_base()}/mcp-v2",
    )


def _proposal_schema():
    return {
        "type": "object",
        "properties": {
            "submitted_name": {"type": "string"},
            "canonical_name": {"type": "string"},
            "json_schema": {
                "type": "object",
                "description": "Complete durable JSON Schema for this field, including object properties and array items.",
                "additionalProperties": True,
            },
            "description": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "generality_reason": {
                "type": "string",
                "description": "Why this field should recur across many experiences of this concept, rather than describe only the current review.",
            },
            "analytical_value": {
                "type": "string",
                "description": "What future search, comparison, recommendation or personalisation becomes materially better by structuring this field.",
            },
            "existing_field_check": {
                "type": "string",
                "description": "What canonical fields, aliases, pending proposals and parent concepts were checked to avoid a duplicate or overly narrow child field.",
            },
            "why_not_raw_text": {
                "type": "string",
                "description": "Why preserving this detail only in raw_text would lose useful reusable structure.",
            },
        },
        "required": [
            "submitted_name",
            "canonical_name",
            "json_schema",
            "generality_reason",
            "analytical_value",
            "existing_field_check",
            "why_not_raw_text",
        ],
        "additionalProperties": False,
    }


def _proposal_quality_issues(item: dict) -> list[str]:
    """Reject common schema-design mistakes before they reach the proposal queue."""
    issues: list[str] = []
    name = normalise_token(str(item.get("canonical_name", "")))
    schema = item.get("json_schema") or {}
    schema_type = schema.get("type")

    for key in ("generality_reason", "analytical_value", "existing_field_check", "why_not_raw_text"):
        value = str(item.get(key, "")).strip()
        if len(value) < 20:
            issues.append(f"{key} must contain a substantive schema-design justification")

    tokens = set(part for part in re.split(r"_+", name) if part)
    measurement_tokens = {
        "amount", "area", "cost", "distance", "duration", "fare", "fee", "height",
        "length", "price", "speed", "spend", "temperature", "time", "volume", "weight",
    }
    qualitative_tokens = {
        "enjoyment", "impression", "quality", "satisfaction", "sentiment", "value",
    }

    if schema_type == "string" and tokens & measurement_tokens:
        issues.append(
            "measurement/money fields must not be free-form strings; use a machine-readable numeric or object schema with explicit unit/currency where applicable"
        )
    if schema_type == "string" and tokens & qualitative_tokens and not schema.get("enum"):
        issues.append(
            "qualitative comparison fields must not be unconstrained strings; use a constrained enum/object or keep the wording in raw_text"
        )

    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            issues.append("object fields must define non-empty properties")

    return issues


def _existing_vocabulary_for_path(db: Session, path: str):
    """Return the closest existing concept so inherited vocabulary can be checked before creating a new path."""
    exact = db.scalar(select(Concept).where(Concept.path == path))
    if exact:
        return exact
    parts = path.split(".")
    for end in range(len(parts) - 1, 0, -1):
        candidate = db.scalar(select(Concept).where(Concept.path == ".".join(parts[:end])))
        if candidate:
            return candidate
    return None


TOOLS = [
    {"name": "search", "title": "Search TasteGraph v2 experiences", "description": "Search the connected user's direct experiences across any domain.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "concept_path": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}}, "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "fetch", "title": "Fetch a TasteGraph v2 experience", "description": "Fetch one complete direct experience, including submitted and canonical structured data.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "get_concept", "title": "Get canonical concept vocabulary", "description": "Check canonical fields, accepted aliases, and unresolved client proposals before writing.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "vocabulary_index", "title": "Search the global DNS vocabulary index", "description": "Find every canonical or pending position where a word occurs in the DNS-style concept structure. Omit word for the complete index. Pending proposals are included so another AI can discover and verify them.", "inputSchema": {"type": "object", "properties": {"word": {"type": "string"}}, "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "pending_vocabulary_proposals", "title": "Review pending vocabulary proposals from other AIs", "description": "List unresolved proposals from other authenticated AI clients. Review independently; approve only durable, reusable schema. Reject poor or over-specific proposals instead of leaving schema debris.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}}, "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "propose_concept_fields", "title": "Propose durable canonical concept fields", "description": "Schema design, not review extraction. Propose only the smallest set of broadly reusable fields that materially improve future search/comparison/personalisation. Check canonical vocabulary, aliases, pending proposals and parent concepts first. Prefer raw_text when uncertain. Measurements and money must use machine-readable schemas, not free-form strings. Each field requires explicit generality/value/duplicate/raw-text justification.", "inputSchema": {"type": "object", "properties": {"concept_path": {"type": "string"}, "concept_description": {"type": "string"}, "fields": {"type": "array", "minItems": 1, "items": _proposal_schema()}}, "required": ["concept_path", "fields"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "verify_concept_field_proposal", "title": "Verify and commit another AI's concept/field proposal", "description": "Independently promote a pending proposal only when placement, generality, analytical value and JSON schema are durable. The authenticated client cannot verify its own proposal.", "inputSchema": {"type": "object", "properties": {"proposal_id": {"type": "string", "format": "uuid"}, "rationale": {"type": "string"}}, "required": ["proposal_id", "rationale"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "reject_concept_field_proposal", "title": "Reject a poor pending vocabulary proposal", "description": "Reject a pending field proposal that is duplicate, over-specific, analytically weak or poorly typed. Give a concrete reason so a future AI can propose a better replacement if warranted.", "inputSchema": {"type": "object", "properties": {"proposal_id": {"type": "string", "format": "uuid"}, "reason": {"type": "string", "minLength": 10}}, "required": ["proposal_id", "reason"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "propose_alias", "title": "Propose a semantic alias mapping", "description": "Propose that an unfamiliar term means an existing canonical field. TasteGraph promotes an alias only after independent client consensus.", "inputSchema": {"type": "object", "properties": {"concept_path": {"type": "string"}, "alias": {"type": "string"}, "canonical_name": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "rationale": {"type": "string"}}, "required": ["concept_path", "alias", "canonical_name"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "save_experience", "title": "Save an approved direct experience", "description": "Only call after explicit user approval. The concept and every structured field must already be canonical. This tool never changes the schema. Reuse idempotency_key on retry.", "inputSchema": {"type": "object", "properties": {"concept_path": {"type": "string"}, "subject_name": {"type": "string"}, "canonical_key": {"type": "string"}, "identifiers": {"type": "object", "additionalProperties": True, "default": {}}, "subject_attributes": {"type": "object", "additionalProperties": True, "default": {}}, "headline": {"type": "string"}, "summary": {"type": "string"}, "raw_text": {"type": "string", "minLength": 1}, "structured_data": {"type": "object", "additionalProperties": True, "default": {}}, "visibility": {"type": "string", "enum": ["private", "unlisted", "public", "aggregate_only"], "default": "private"}, "user_approved": {"type": "boolean"}, "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200}}, "required": ["concept_path", "subject_name", "canonical_key", "headline", "summary", "raw_text", "user_approved", "idempotency_key"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "save_assessment", "title": "Save AI-derived assessment", "description": "Save AI-derived analysis against the exact experience it evaluates. Linkage and authenticated provenance are derived by the server. Reuse idempotency_key on retry.", "inputSchema": {"type": "object", "properties": {"experience_id": {"type": "string", "format": "uuid"}, "assessment_type": {"type": "string"}, "evidence": {"type": "object", "additionalProperties": True, "default": {}}, "analysis": {"type": "object", "additionalProperties": True, "default": {}}, "conclusion": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "source_model": {"type": "string"}, "provenance": {"type": "object", "additionalProperties": True, "default": {}}, "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200}}, "required": ["experience_id", "assessment_type", "idempotency_key"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
]


def _search(db, principal, args):
    q = str(args.get("query", "")).strip()
    limit = max(1, min(int(args.get("limit", 10)), 20))
    stmt = select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.owner_id == principal.user_id, V2Experience.deleted_at.is_(None))
    if args.get("concept_path"):
        stmt = stmt.where(Concept.path == args["concept_path"])
    if q:
        p = f"%{q}%"
        stmt = stmt.where(or_(V2Subject.name.ilike(p), V2Subject.canonical_key.ilike(p), V2Experience.headline.ilike(p), V2Experience.summary.ilike(p)))
    rows = db.execute(stmt.order_by(V2Experience.created_at.desc()).limit(limit)).all()
    return _result({"count": len(rows), "results": [{"id": str(e.id), "subject_id": str(s.id), "concept_path": c.path, "subject_name": s.name, "headline": e.headline, "summary": e.summary} for e, s, c in rows]})


def _fetch(db, principal, args):
    try:
        exp_id = uuid.UUID(str(args.get("id", "")))
    except ValueError:
        return _error("Invalid experience ID")
    row = db.execute(select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.id == exp_id, V2Experience.owner_id == principal.user_id, V2Experience.deleted_at.is_(None))).first()
    if not row:
        return _error("Experience not found")
    e, s, c = row
    assessments = list(db.scalars(select(Assessment).where(Assessment.experience_id == e.id).order_by(Assessment.created_at)).all())
    return _result({"id": str(e.id), "subject": {"id": str(s.id), "name": s.name, "canonical_key": s.canonical_key, "concept_path": c.path, "identifiers": s.identifiers_json, "attributes": s.attributes_json}, "headline": e.headline, "summary": e.summary, "raw_text": e.raw_text, "structured_data": e.structured_data, "submitted_data": e.submitted_data, "normalization_log": e.normalization_log, "provenance": e.provenance, "assessments": [{"id": str(a.id), "experience_id": str(a.experience_id), "assessment_type": a.assessment_type, "evidence": a.evidence_json, "analysis": a.analysis_json, "conclusion": a.conclusion, "confidence": a.confidence, "source_model": a.source_model, "provenance": a.provenance, "created_by_client": a.created_by_client, "created_at": a.created_at.isoformat()} for a in assessments], "created_at": e.created_at.isoformat()})


def _get_concept(db, args):
    try:
        path = normalise_path(str(args.get("path", "")))
    except ValueError as exc:
        return _error(str(exc))
    concept = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
    if not concept:
        pending = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "pending"))
        return _error("Concept not canonical", {"path": path, "pending": bool(pending), "instruction": "Search vocabulary_index and pending_vocabulary_proposals before proposing anything new."})
    vocab = vocabulary(db, concept)
    unique = {f.id: f for f in vocab["fields"].values()}
    return _result({"path": concept.path, "version": concept.version, "description": concept.description, "fields": [{"canonical_name": f.canonical_name, "json_schema": (f.metadata_json or {}).get("json_schema", {"type": f.data_type}), "description": f.description, "origin": vocab["origins"].get(f.canonical_name)} for f in unique.values()], "pending_field_proposals": [{"id": str(p.id), "submitted_name": p.submitted_name, "canonical_name": p.canonical_name, "json_schema": p.json_schema, "description": p.description, "aliases": p.aliases_json, "proposed_by": p.proposer_client_id, "status": p.status} for p in list_field_proposals(db, concept, status="pending")], "accepted_aliases": vocab["aliases"], "alias_candidates": list_alias_candidates(db, concept), "semantic_policy": "Propose sparingly; one AI proposes and a different AI verifies. Poor proposals should be rejected, not promoted."})


def _pending_vocabulary_proposals(db, principal, args):
    client_id = f"{principal.client_id}:v2"
    limit = max(1, min(int(args.get("limit", 20)), 50))
    rows = [proposal for proposal in list_field_proposals(db, status="pending") if proposal.proposer_client_id != client_id][:limit]
    proposals = []
    for proposal in rows:
        concept = db.get(Concept, proposal.concept_id)
        proposals.append({"proposal_id": str(proposal.id), "concept_path": concept.path if concept else None, "concept_status": concept.status if concept else None, "submitted_name": proposal.submitted_name, "canonical_name": proposal.canonical_name, "json_schema": proposal.json_schema, "description": proposal.description, "aliases": proposal.aliases_json, "proposed_by": proposal.proposer_client_id, "status": proposal.status, "instruction": "Review generality, analytical value, duplication/inheritance and schema quality. Verify only if durable; otherwise call reject_concept_field_proposal with a concrete reason."})
    return _result({"count": len(proposals), "proposals": proposals, "reviewer_client": client_id})


def _propose_concept_fields(db, principal, args):
    raw_fields = args.get("fields", [])
    if not raw_fields:
        return _error("At least one field proposal is required")

    path = normalise_path(str(args["concept_path"]))
    existing_context = _existing_vocabulary_for_path(db, path)
    existing_vocab = vocabulary(db, existing_context) if existing_context else {"fields": {}, "aliases": {}}
    existing_field_names = set(existing_vocab["fields"].keys())
    existing_aliases = set(existing_vocab["aliases"].keys())

    quality_failures = []
    proposals = []
    seen_batch: set[str] = set()
    for raw in raw_fields:
        issues = _proposal_quality_issues(raw)
        canonical_key = normalise_token(str(raw.get("canonical_name", "")))
        aliases = [normalise_token(str(alias)) for alias in raw.get("aliases", [])]
        if canonical_key in existing_field_names:
            issues.append("a canonical field with this meaning/name already exists in this concept or an ancestor")
        if canonical_key in existing_aliases:
            issues.append("this name is already an accepted alias for an inherited/existing canonical field")
        if canonical_key in seen_batch:
            issues.append("duplicate canonical field appears more than once in this proposal batch")
        if any(alias in existing_field_names or alias in existing_aliases for alias in aliases if alias):
            issues.append("one or more proposed aliases collide with existing inherited/canonical vocabulary")
        seen_batch.add(canonical_key)
        if issues:
            quality_failures.append({"canonical_name": raw.get("canonical_name"), "issues": issues})
            continue
        proposals.append(FieldProposal.model_validate({k: raw[k] for k in ("submitted_name", "canonical_name", "json_schema") } | {"description": raw.get("description"), "aliases": raw.get("aliases", [])}))

    if quality_failures:
        return _error("Vocabulary proposal failed schema-quality gates", {"concept_path": path, "rejected_fields": quality_failures, "instruction": "Do not turn every review detail into schema. Keep uncertain or one-off detail in raw_text; revise only fields that are broadly reusable and machine-typed."})

    client_id = f"{principal.client_id}:v2"
    concept = ensure_proposed_concept(db, ConceptEnsure(path=path, description=args.get("concept_description"), created_by=client_id))
    rows = propose_concept_fields(db, concept=concept, proposals=proposals, proposer_client_id=client_id)
    return _result({"concept_path": concept.path, "concept_status": concept.status, "concept_version": concept.version, "proposals": [{"id": str(row.id), "canonical_name": row.canonical_name, "json_schema": row.json_schema, "status": row.status} for row in rows], "verification_required": True, "instruction": "A different authenticated AI must independently review these. It should reject weak/over-specific proposals rather than approve them merely because they are pending.", "manual_approval_url": f"{_base()}/development/concept-fields", "experience_created": False})


def _verify_concept_field_proposal(db, principal, args):
    try:
        proposal_id = uuid.UUID(str(args.get("proposal_id", "")))
    except ValueError:
        return _error("Invalid proposal_id")
    rationale = str(args.get("rationale", "")).strip()
    if len(rationale) < 20:
        return _error("Verification rationale must explain why this is durable canonical vocabulary")
    proposal, field = verify_field_proposal(db, proposal_id, verifier_client_id=f"{principal.client_id}:v2", reason=rationale)
    concept = db.get(Concept, proposal.concept_id)
    return _result({"committed": True, "proposal_id": str(proposal.id), "status": proposal.status, "concept_path": concept.path if concept else None, "concept_status": concept.status if concept else None, "field_id": str(field.id), "canonical_name": field.canonical_name, "proposed_by": proposal.proposer_client_id, "verified_by": proposal.decision_by, "verification_reason": proposal.decision_reason})


def _reject_concept_field_proposal(db, principal, args):
    try:
        proposal_id = uuid.UUID(str(args.get("proposal_id", "")))
    except ValueError:
        return _error("Invalid proposal_id")
    reason = str(args.get("reason", "")).strip()
    if len(reason) < 10:
        return _error("A concrete rejection reason is required")
    proposal = db.get(ConceptFieldProposal, proposal_id)
    if not proposal:
        return _error("Field proposal not found")
    proposal = reject_field_proposal(db, proposal_id, reason=reason, decided_by=f"{principal.client_id}:v2")
    concept = db.get(Concept, proposal.concept_id)
    return _result({"rejected": True, "proposal_id": str(proposal.id), "status": proposal.status, "concept_path": concept.path if concept else None, "canonical_name": proposal.canonical_name, "proposed_by": proposal.proposer_client_id, "rejected_by": proposal.decision_by, "reason": proposal.decision_reason})


def _propose_alias(db, principal, args):
    path = normalise_path(str(args.get("concept_path", "")))
    concept = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
    if not concept:
        return _error("Concept not found", {"path": path})
    status = propose_alias(db, concept=concept, alias=str(args.get("alias", "")), canonical_name=str(args.get("canonical_name", "")), proposer_client_id=f"{principal.client_id}:v2", confidence=args.get("confidence"), rationale=args.get("rationale"))
    db.commit()
    return _result(status)


def _save_experience(db, principal, args):
    if args.get("user_approved") is not True:
        return _error("Explicit user approval is required before saving a direct experience")
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    client_id = f"{principal.client_id}:v2"
    payload_hash, prior = begin_idempotent_write(db, client_id=client_id, key=f"experience:{args['idempotency_key']}", payload=relevant)
    if prior is not None:
        return _result(prior)
    path = normalise_path(str(args["concept_path"]))
    concept = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
    if not concept:
        return _error("Concept is not canonical yet", {"concept_path": path, "instruction": "Use pending_vocabulary_proposals or vocabulary_index and have a different AI verify suitable pending proposals before saving."})
    subject = ensure_subject(db, SubjectEnsure(concept_path=concept.path, name=args["subject_name"], canonical_key=args["canonical_key"], identifiers=args.get("identifiers", {}), attributes=args.get("subject_attributes", {}), create_concept_if_missing=False), client_id)
    exp = create_experience(db, ExperienceCreate(owner_id=principal.user_id, subject_id=subject.id, headline=args["headline"], summary=args["summary"], raw_text=args["raw_text"], structured_data=args.get("structured_data", {}), visibility=args.get("visibility", "private"), user_approved=True, source_client=client_id), client_id)
    body = {"saved": True, "experience_id": str(exp.id), "subject_id": str(subject.id), "concept_path": concept.path, "canonical_data": exp.structured_data, "normalization_log": exp.normalization_log, "alias_candidates": list_alias_candidates(db, concept)}
    finish_idempotent_write(db, client_id=client_id, key=f"experience:{args['idempotency_key']}", payload_hash=payload_hash, response_body=body)
    return _result(body)


def _save_assessment(db, principal, args):
    try:
        experience_id = uuid.UUID(str(args["experience_id"]))
    except (ValueError, KeyError):
        return _error("Invalid experience_id")
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    client_id = f"{principal.client_id}:v2"
    payload_hash, prior = begin_idempotent_write(db, client_id=client_id, key=f"assessment:{args['idempotency_key']}", payload=relevant)
    if prior is not None:
        return _result(prior)
    obj = create_assessment(db, AssessmentCreate(experience_id=experience_id, assessment_type=args["assessment_type"], evidence=args.get("evidence", {}), analysis=args.get("analysis", {}), conclusion=args.get("conclusion"), confidence=args.get("confidence"), source_model=args.get("source_model"), provenance=args.get("provenance", {})), client_id=client_id, user_id=principal.user_id)
    body = {"saved": True, "assessment_id": str(obj.id), "experience_id": str(obj.experience_id), "subject_id": str(obj.subject_id), "provenance": obj.provenance, "created_by_client": obj.created_by_client}
    finish_idempotent_write(db, client_id=client_id, key=f"assessment:{args['idempotency_key']}", payload_hash=payload_hash, response_body=body)
    return _result(body)


@router.post("/mcp-v2")
async def mcp_v2(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    rpc_id = body.get("id")
    method = body.get("method")
    if method and method.startswith("notifications/"):
        return Response(status_code=202)
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "TasteGraph v2", "version": SERVER_VERSION},
            "instructions": (
                "Treat vocabulary changes as conservative schema design, never as automatic extraction from one review. "
                "Before proposing anything, inspect pending_vocabulary_proposals and vocabulary_index, including parent concepts. "
                "Only propose the smallest set of fields that are broadly reusable across many experiences and materially improve future search, comparison, recommendation or personalisation. "
                "If a detail is one-off, uncertain, narrative, or adequately preserved in raw_text, do not propose a field. "
                "Use durable machine-readable schemas: measurements and money must not be free-form strings. "
                "A different authenticated AI must independently verify each proposal; reject weak, duplicate, over-specific or poorly typed proposals with reject_concept_field_proposal. "
                "Never verify your own proposal and never approve merely because another AI proposed it. "
                "save_experience only accepts canonical concepts/fields. Preserve exact user words in raw_text and AI interpretation in save_assessment."
            ),
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        scope = "reviews:write" if name in {"propose_concept_fields", "verify_concept_field_proposal", "reject_concept_field_proposal", "propose_alias", "save_experience", "save_assessment"} else "reviews:read"
        try:
            principal = _principal(request, scope)
        except TokenError as exc:
            result = _auth_error(str(exc))
        else:
            try:
                if name == "search":
                    result = _search(db, principal, args)
                elif name == "fetch":
                    result = _fetch(db, principal, args)
                elif name == "get_concept":
                    result = _get_concept(db, args)
                elif name == "vocabulary_index":
                    result = _result(vocabulary_index(db, args.get("word")))
                elif name == "pending_vocabulary_proposals":
                    result = _pending_vocabulary_proposals(db, principal, args)
                elif name == "propose_concept_fields":
                    result = _propose_concept_fields(db, principal, args)
                elif name == "verify_concept_field_proposal":
                    result = _verify_concept_field_proposal(db, principal, args)
                elif name == "reject_concept_field_proposal":
                    result = _reject_concept_field_proposal(db, principal, args)
                elif name == "propose_alias":
                    result = _propose_alias(db, principal, args)
                elif name == "save_experience":
                    result = _save_experience(db, principal, args)
                elif name == "save_assessment":
                    result = _save_assessment(db, principal, args)
                else:
                    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Unknown tool"}})
            except Exception as exc:
                db.rollback()
                result = _error("TasteGraph v2 server error", {"type": type(exc).__name__, "message": str(exc)})
    else:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}})
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


@router.get("/mcp-v2")
def mcp_v2_get():
    return JSONResponse({"service": "TasteGraph v2 MCP", "version": SERVER_VERSION, "transport": "Streamable HTTP", "method": "POST", "oauth_resource_metadata": f"{_base()}/.well-known/oauth-protected-resource/mcp-v2", "tools": [x["name"] for x in TOOLS]}, status_code=405, headers={"Allow": "POST"})
