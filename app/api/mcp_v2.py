from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Principal, TokenError, principal_from_authorization
from app.db.session import get_db
from app.models.v2 import Assessment, SubjectType, SubjectTypeAlias, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ExperienceCreate, FieldEnsure, SubjectEnsure
from app.services.v2 import (
    add_subject_type_alias, add_type_relationship, create_assessment, create_experience,
    descendant_type_ids, ensure_field, ensure_subject, ensure_subject_type,
    fields_for_type, resolve_subject_type, vocabulary_index,
)
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write

router = APIRouter()
PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "3.0.0-alpha"
READ_SECURITY = [{"type": "oauth2", "scopes": ["reviews:read"]}]
WRITE_SECURITY = [{"type": "oauth2", "scopes": ["reviews:write"]}]


def _security(schemes):
    return {"securitySchemes": schemes, "_meta": {"securitySchemes": schemes}}


def _base():
    return get_settings().public_base_url.rstrip("/")


def _result(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str, separators=(",", ":"))}], "structuredContent": payload}


def _error(message, details=None):
    payload = {"error": message}
    if details is not None:
        payload["details"] = details
    return {**_result(payload), "isError": True}


def _principal(request: Request, scope: str) -> Principal:
    return principal_from_authorization(request.headers.get("authorization"), scope, expected_resource=f"{_base()}/mcp-v2")


def _auth_error(message):
    challenge = f'Bearer resource_metadata="{_base()}/.well-known/oauth-protected-resource/mcp-v2", error="insufficient_scope"'
    return {"content": [{"type": "text", "text": f"Authentication required: {message}."}], "isError": True,
            "_meta": {"mcp/www_authenticate": [challenge]}}


TOOLS = [
    {"name": "search", "title": "Search reviews", "description": "Search by subject, review text, a canonical subject type or any broader category connected by belongs_to relationships.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "subject_type": {"type": "string"}, "include_related": {"type": "boolean", "default": True}, "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}, "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "fetch", "title": "Fetch a review", "description": "Fetch a complete review with its stable subject type, original words and AI assessments.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}}, "required": ["id"], "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "vocabulary_index", "title": "Inspect standard vocabulary", "description": "List canonical subject types, aliases, flexible relationships and reusable fields. There are no DNS paths or review leaf concepts.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "resolve_subject_type", "title": "Resolve a subject type", "description": "Resolve flexible input to one stable subject-type ID. Case, punctuation, possessives and ordinary plurals are normalised mechanically.", "inputSchema": {"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"], "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "register_subject_type_alias", "title": "Register a subject-type alias", "description": "Map a genuinely equivalent expression to an existing stable subject type. Never use this to express a category relationship.", "inputSchema": {"type": "object", "properties": {"subject_type": {"type": "string"}, "alias": {"type": "string"}}, "required": ["subject_type", "alias"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "set_type_relationship", "title": "Connect subject types", "description": "Add editable classification metadata such as ferry belongs_to transportation. Relationships improve broad search but never determine storage.", "inputSchema": {"type": "object", "properties": {"source_type": {"type": "string"}, "relationship": {"type": "string", "default": "belongs_to"}, "target_type": {"type": "string"}}, "required": ["source_type", "target_type"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "register_field", "title": "Register a reusable field", "description": "Register one globally canonical field and attach it to relevant subject types. Prefer raw_text for one-off narrative detail.", "inputSchema": {"type": "object", "properties": {"canonical_name": {"type": "string"}, "json_schema": {"type": "object", "additionalProperties": True}, "description": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}}, "subject_types": {"type": "array", "items": {"type": "string"}}}, "required": ["canonical_name", "json_schema", "subject_types"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "save_experience", "title": "Save an approved review", "description": "Save a review against a stable subject-type ID. Unknown types become provisional after a whole-dictionary lookup; they are never stored under a broad fallback category.", "inputSchema": {"type": "object", "properties": {"subject_type": {"type": "string"}, "subject_name": {"type": "string"}, "canonical_key": {"type": "string"}, "identifiers": {"type": "object", "additionalProperties": True, "default": {}}, "subject_attributes": {"type": "object", "additionalProperties": True, "default": {}}, "headline": {"type": "string"}, "summary": {"type": "string"}, "raw_text": {"type": "string", "minLength": 1}, "structured_data": {"type": "object", "additionalProperties": True, "default": {}}, "visibility": {"type": "string", "enum": ["private", "unlisted", "public", "aggregate_only"], "default": "private"}, "user_approved": {"type": "boolean"}, "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200}}, "required": ["subject_type", "subject_name", "canonical_key", "headline", "summary", "raw_text", "user_approved", "idempotency_key"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "save_assessment", "title": "Save AI-derived assessment", "description": "Save separately attributed AI analysis against the exact review it evaluates.", "inputSchema": {"type": "object", "properties": {"experience_id": {"type": "string", "format": "uuid"}, "assessment_type": {"type": "string"}, "evidence": {"type": "object", "additionalProperties": True, "default": {}}, "analysis": {"type": "object", "additionalProperties": True, "default": {}}, "conclusion": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "source_model": {"type": "string"}, "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200}}, "required": ["experience_id", "assessment_type", "idempotency_key"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
]


def _resolve(db, args):
    obj = resolve_subject_type(db, str(args.get("term", "")))
    if not obj:
        return _result({"found": False, "term": args.get("term"), "instruction": "A new provisional type will be created when an approved review is saved."})
    aliases = list(db.scalars(select(SubjectTypeAlias).where(SubjectTypeAlias.subject_type_id == obj.id)).all())
    return _result({"found": True, "id": str(obj.id), "canonical_name": obj.canonical_name, "status": obj.status, "aliases": [x.alias for x in aliases], "fields": [x.canonical_name for x in fields_for_type(db, obj)]})


def _search(db, principal, args):
    stmt = select(V2Experience, V2Subject, SubjectType).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(SubjectType, V2Subject.subject_type_id == SubjectType.id).where(V2Experience.owner_id == principal.user_id, V2Experience.deleted_at.is_(None))
    type_term = str(args.get("subject_type", "")).strip()
    if type_term:
        root = resolve_subject_type(db, type_term)
        if not root:
            return _result({"count": 0, "results": []})
        ids = descendant_type_ids(db, root) if args.get("include_related", True) else {root.id}
        stmt = stmt.where(SubjectType.id.in_(ids))
    q = str(args.get("query", "")).strip()
    if q:
        p = f"%{q}%"
        stmt = stmt.where(or_(V2Subject.name.ilike(p), V2Subject.canonical_key.ilike(p), V2Experience.headline.ilike(p), V2Experience.summary.ilike(p), V2Experience.raw_text.ilike(p)))
    limit = max(1, min(int(args.get("limit", 10)), 50))
    rows = db.execute(stmt.order_by(V2Experience.created_at.desc()).limit(limit)).all()
    return _result({"count": len(rows), "results": [{"id": str(e.id), "subject_id": str(s.id), "subject_name": s.name, "subject_type_id": str(t.id), "subject_type": t.canonical_name, "headline": e.headline, "summary": e.summary} for e, s, t in rows]})


def _fetch(db, principal, args):
    try:
        exp_id = uuid.UUID(str(args.get("id", "")))
    except ValueError:
        return _error("Invalid experience ID")
    row = db.execute(select(V2Experience, V2Subject, SubjectType).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(SubjectType, V2Subject.subject_type_id == SubjectType.id).where(V2Experience.id == exp_id, V2Experience.owner_id == principal.user_id, V2Experience.deleted_at.is_(None))).first()
    if not row:
        return _error("Experience not found")
    e, s, t = row
    assessments = list(db.scalars(select(Assessment).where(Assessment.experience_id == e.id).order_by(Assessment.created_at)).all())
    return _result({"id": str(e.id), "record_type": e.record_type, "subject": {"id": str(s.id), "name": s.name, "canonical_key": s.canonical_key, "subject_type_id": str(t.id), "subject_type": t.canonical_name, "identifiers": s.identifiers_json, "attributes": s.attributes_json}, "headline": e.headline, "summary": e.summary, "raw_text": e.raw_text, "structured_data": e.structured_data, "submitted_data": e.submitted_data, "normalization_log": e.normalization_log, "provenance": e.provenance, "assessments": [{"id": str(a.id), "assessment_type": a.assessment_type, "evidence": a.evidence_json, "analysis": a.analysis_json, "conclusion": a.conclusion, "confidence": a.confidence, "provenance": a.provenance} for a in assessments], "created_at": e.created_at.isoformat()})


def _save_experience(db, principal, args):
    if args.get("user_approved") is not True:
        return _error("Explicit user approval is required before saving a direct review")
    if principal.user_id is None:
        return _error("Authenticated TasteGraph user is required")
    client_id = f"{principal.client_id}:v3"
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    payload_hash, prior = begin_idempotent_write(db, client_id=client_id, key=f"experience:{args['idempotency_key']}", payload=relevant)
    if prior is not None:
        return _result(prior)
    subject_type, created, resolution = ensure_subject_type(db, args["subject_type"], created_by=client_id)
    subject = ensure_subject(db, SubjectEnsure(subject_type=subject_type.canonical_name, name=args["subject_name"], canonical_key=args["canonical_key"], identifiers=args.get("identifiers", {}), attributes=args.get("subject_attributes", {})), client_id)
    exp = create_experience(db, ExperienceCreate(owner_id=principal.user_id, subject_id=subject.id, headline=args["headline"], summary=args["summary"], raw_text=args["raw_text"], structured_data=args.get("structured_data", {}), visibility=args.get("visibility", "private"), user_approved=True, source_client=client_id), client_id)
    body = {"saved": True, "experience_id": str(exp.id), "subject_id": str(subject.id), "subject_type_id": str(subject_type.id), "subject_type": subject_type.canonical_name, "type_status": subject_type.status, "type_resolution": resolution, "type_created": created, "canonical_data": exp.structured_data, "normalization_log": exp.normalization_log}
    finish_idempotent_write(db, client_id=client_id, key=f"experience:{args['idempotency_key']}", payload_hash=payload_hash, response_body=body)
    return _result(body)


def _save_assessment(db, principal, args):
    client_id = f"{principal.client_id}:v3"
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    payload_hash, prior = begin_idempotent_write(db, client_id=client_id, key=f"assessment:{args['idempotency_key']}", payload=relevant)
    if prior is not None:
        return _result(prior)
    obj = create_assessment(db, AssessmentCreate(experience_id=args["experience_id"], assessment_type=args["assessment_type"], evidence=args.get("evidence", {}), analysis=args.get("analysis", {}), conclusion=args.get("conclusion"), confidence=args.get("confidence"), source_model=args.get("source_model")), client_id=client_id, user_id=principal.user_id)
    body = {"saved": True, "assessment_id": str(obj.id), "experience_id": str(obj.experience_id), "provenance": obj.provenance}
    finish_idempotent_write(db, client_id=client_id, key=f"assessment:{args['idempotency_key']}", payload_hash=payload_hash, response_body=body)
    return _result(body)


@router.post("/mcp-v2")
async def mcp_v2(request: Request, db: Session = Depends(get_db)):
    body = await request.json(); rpc_id = body.get("id"); method = body.get("method")
    if method and method.startswith("notifications/"):
        return Response(status_code=202)
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "TasteGraph v2", "version": SERVER_VERSION}, "instructions": "Flexible input is resolved through one standard vocabulary. Reviews store stable subject-type IDs. Unknown types become provisional; category relationships are separate editable metadata and never storage addresses. Preserve exact user wording in raw_text and AI analysis in save_assessment."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params") or {}; name = params.get("name"); args = params.get("arguments") or {}
        write_names = {"register_subject_type_alias", "set_type_relationship", "register_field", "save_experience", "save_assessment"}
        try:
            principal = _principal(request, "reviews:write" if name in write_names else "reviews:read")
        except TokenError as exc:
            result = _auth_error(str(exc))
        else:
            try:
                if name == "search": result = _search(db, principal, args)
                elif name == "fetch": result = _fetch(db, principal, args)
                elif name == "vocabulary_index": result = _result(vocabulary_index(db))
                elif name == "resolve_subject_type": result = _resolve(db, args)
                elif name == "register_subject_type_alias":
                    target = resolve_subject_type(db, args["subject_type"])
                    result = _error("Subject type not found") if not target else _result({"registered": True, "alias": add_subject_type_alias(db, target, args["alias"], source=f"{principal.client_id}:v3").alias, "subject_type_id": str(target.id), "canonical_name": target.canonical_name})
                elif name == "set_type_relationship":
                    source_type, _, _ = ensure_subject_type(db, args["source_type"], created_by=f"{principal.client_id}:v3")
                    target_type, _, _ = ensure_subject_type(db, args["target_type"], created_by=f"{principal.client_id}:v3")
                    rel = add_type_relationship(db, source_type, args.get("relationship", "belongs_to"), target_type, source=f"{principal.client_id}:v3")
                    result = _result({"registered": True, "id": str(rel.id), "source": source_type.canonical_name, "relationship": rel.relationship, "target": target_type.canonical_name})
                elif name == "register_field":
                    field = ensure_field(db, FieldEnsure.model_validate(args), source=f"{principal.client_id}:v3")
                    result = _result({"registered": True, "field_id": str(field.id), "canonical_name": field.canonical_name})
                elif name == "save_experience": result = _save_experience(db, principal, args)
                elif name == "save_assessment": result = _save_assessment(db, principal, args)
                else: return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Unknown tool"}})
            except Exception as exc:
                db.rollback(); result = _error("TasteGraph server error", {"type": type(exc).__name__, "message": str(exc)})
    else:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}})
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


@router.get("/mcp-v2")
def mcp_v2_get():
    return JSONResponse({"service": "TasteGraph MCP", "version": SERVER_VERSION, "method": "POST", "tools": [x["name"] for x in TOOLS]}, status_code=405, headers={"Allow": "POST"})
