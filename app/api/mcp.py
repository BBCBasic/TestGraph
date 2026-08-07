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
from app.models.v2 import Concept, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, FieldProposal, SubjectEnsure
from app.services.v2 import create_assessment, create_experience, ensure_concept, ensure_subject, vocabulary

router = APIRouter()
PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "2.0.0-alpha"
READ_SECURITY = [{"type": "oauth2", "scopes": ["reviews:read"]}]
WRITE_SECURITY = [{"type": "oauth2", "scopes": ["reviews:write"]}]


def _tool_security(schemes: list[dict]) -> dict:
    return {"securitySchemes": schemes, "_meta": {"securitySchemes": schemes}}


def _proposal_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "submitted_name": {"type": "string"},
            "canonical_name": {"type": "string"},
            "data_type": {"type": "string", "default": "any"},
            "description": {"type": "string"},
            "unit": {"type": "string"},
            "allowed_values": {"type": "array", "items": {}},
            "aliases": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["submitted_name", "canonical_name"],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "search",
        "title": "Search TasteGraph experiences",
        "description": "Search the connected user's direct experiences across any concept/domain.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "concept_path": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}}, "additionalProperties": False},
        **_tool_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "fetch",
        "title": "Fetch a TasteGraph experience",
        "description": "Fetch one complete direct user experience including original and canonical structured data.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False},
        **_tool_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "get_concept",
        "title": "Get TasteGraph concept vocabulary",
        "description": "Look up a concept path and its inherited canonical fields and aliases before writing. If it does not exist, save_experience may create it using proposed_fields.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        **_tool_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "save_experience",
        "title": "Save an approved TasteGraph experience",
        "description": "Save a direct user experience in any domain. Only call after explicit user approval. TasteGraph normalises aliases before writing. For genuinely new dimensions include proposed_fields; do not create synonyms when an existing canonical field fits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept_path": {"type": "string", "description": "Hierarchical concept such as place.restaurant or product.electronics.camera.action_camera"},
                "concept_description": {"type": "string"},
                "subject_name": {"type": "string"},
                "canonical_key": {"type": "string"},
                "identifiers": {"type": "object", "additionalProperties": True, "default": {}},
                "subject_attributes": {"type": "object", "additionalProperties": True, "default": {}},
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "raw_text": {"type": "string"},
                "structured_data": {"type": "object", "additionalProperties": True, "default": {}},
                "proposed_fields": {"type": "array", "items": _proposal_schema(), "default": []},
                "visibility": {"type": "string", "enum": ["private", "unlisted", "public", "aggregate_only"], "default": "private"},
                "user_approved": {"type": "boolean"},
                "source_client": {"type": "string", "default": "mcp-client"},
            },
            "required": ["concept_path", "subject_name", "canonical_key", "headline", "summary", "user_approved"],
            "additionalProperties": False,
        },
        **_tool_security(WRITE_SECURITY),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "save_assessment",
        "title": "Save AI-derived TasteGraph assessment",
        "description": "Save derived knowledge about a subject. Use for AI analysis of external evidence such as open reviews. This is never represented as the user's own experience.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_id": {"type": "string"},
                "assessment_type": {"type": "string"},
                "evidence": {"type": "object", "additionalProperties": True, "default": {}},
                "analysis": {"type": "object", "additionalProperties": True, "default": {}},
                "conclusion": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "source_model": {"type": "string"},
                "provenance": {"type": "object", "additionalProperties": True, "default": {}},
            },
            "required": ["subject_id", "assessment_type"],
            "additionalProperties": False,
        },
        **_tool_security(WRITE_SECURITY),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
]


def _base() -> str:
    return get_settings().public_base_url.rstrip("/")


def _text(payload: dict) -> list[dict]:
    return [{"type": "text", "text": json.dumps(payload, default=str, separators=(",", ":"))}]


def _result(payload: dict) -> dict:
    return {"content": _text(payload), "structuredContent": payload}


def _tool_error(message: str, *, details=None) -> dict:
    payload = {"error": message}
    if details is not None:
        payload["details"] = details
    return {"content": _text(payload), "structuredContent": payload, "isError": True}


def _auth_result(message: str) -> dict:
    challenge = f'Bearer resource_metadata="{_base()}/.well-known/oauth-protected-resource", error="insufficient_scope", error_description="{message}"'
    return {"content": [{"type": "text", "text": f"Authentication required: {message}."}], "isError": True, "_meta": {"mcp/www_authenticate": [challenge]}}


def _principal(request: Request, scope: str) -> Principal:
    return principal_from_authorization(request.headers.get("authorization"), scope)


def _search(db: Session, principal: Principal, args: dict) -> dict:
    query = str(args.get("query", "")).strip()
    limit = max(1, min(int(args.get("limit", 10)), 20))
    stmt = select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.owner_id == principal.user_id, V2Experience.deleted_at.is_(None))
    if args.get("concept_path"):
        stmt = stmt.where(Concept.path == args["concept_path"])
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(V2Subject.name.ilike(pattern), V2Subject.canonical_key.ilike(pattern), V2Experience.headline.ilike(pattern), V2Experience.summary.ilike(pattern)))
    rows = db.execute(stmt.order_by(V2Experience.created_at.desc()).limit(limit)).all()
    return _result({"count": len(rows), "results": [{"id": str(exp.id), "subject_id": str(subject.id), "concept_path": concept.path, "subject_name": subject.name, "headline": exp.headline, "summary": exp.summary} for exp, subject, concept in rows]})


def _fetch(db: Session, principal: Principal, args: dict) -> dict:
    try:
        exp_id = uuid.UUID(str(args.get("id", "")))
    except ValueError:
        return _tool_error("Invalid experience ID")
    row = db.execute(select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.id == exp_id, V2Experience.owner_id == principal.user_id, V2Experience.deleted_at.is_(None))).first()
    if not row:
        return _tool_error("Experience not found")
    exp, subject, concept = row
    return _result({"id": str(exp.id), "subject": {"id": str(subject.id), "name": subject.name, "canonical_key": subject.canonical_key, "concept_path": concept.path, "identifiers": subject.identifiers_json, "attributes": subject.attributes_json}, "headline": exp.headline, "summary": exp.summary, "raw_text": exp.raw_text, "structured_data": exp.structured_data, "submitted_data": exp.submitted_data, "normalization_log": exp.normalization_log, "provenance": exp.provenance, "created_at": exp.created_at.isoformat()})


def _get_concept(db: Session, args: dict) -> dict:
    from app.services.v2 import normalise_path
    try:
        path = normalise_path(str(args.get("path", "")))
    except ValueError as exc:
        return _tool_error(str(exc))
    concept = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
    if not concept:
        return _tool_error("Concept not found", details={"path": path, "instruction": "save_experience may create this concept; include proposed_fields for genuinely new dimensions"})
    vocab = vocabulary(db, concept)
    unique = {field.id: field for field in vocab["fields"].values()}
    return _result({"path": concept.path, "version": concept.version, "description": concept.description, "fields": [{"canonical_name": f.canonical_name, "data_type": f.data_type, "description": f.description, "unit": f.unit, "origin": vocab["origins"].get(f.canonical_name)} for f in unique.values()], "aliases": vocab["aliases"]})


def _save_experience(db: Session, principal: Principal, args: dict) -> dict:
    if args.get("user_approved") is not True:
        return _tool_error("Explicit user approval is required before saving a direct experience")
    proposals = [FieldProposal.model_validate(p) for p in args.get("proposed_fields", [])]
    concept = ensure_concept(db, ConceptEnsure(path=args["concept_path"], description=args.get("concept_description"), proposed_fields=proposals, created_by=args.get("source_client", "mcp-client")))
    subject = ensure_subject(db, SubjectEnsure(concept_path=concept.path, name=args["subject_name"], canonical_key=args["canonical_key"], identifiers=args.get("identifiers", {}), attributes=args.get("subject_attributes", {})), principal.client_id)
    payload = ExperienceCreate(owner_id=principal.user_id, subject_id=subject.id, headline=args["headline"], summary=args["summary"], raw_text=args.get("raw_text"), structured_data=args.get("structured_data", {}), proposed_fields=proposals, visibility=args.get("visibility", "private"), user_approved=True, source_client=args.get("source_client", "mcp-client"))
    exp = create_experience(db, payload, principal.client_id)
    return _result({"saved": True, "experience_id": str(exp.id), "subject_id": str(subject.id), "concept_path": concept.path, "canonical_data": exp.structured_data, "normalization_log": exp.normalization_log})


def _save_assessment(db: Session, principal: Principal, args: dict) -> dict:
    try:
        subject_id = uuid.UUID(str(args["subject_id"]))
    except (ValueError, KeyError):
        return _tool_error("Invalid subject_id")
    payload = AssessmentCreate(subject_id=subject_id, user_id=principal.user_id, assessment_type=args["assessment_type"], evidence=args.get("evidence", {}), analysis=args.get("analysis", {}), conclusion=args.get("conclusion"), confidence=args.get("confidence"), source_model=args.get("source_model"), provenance=args.get("provenance", {}))
    obj = create_assessment(db, payload)
    return _result({"saved": True, "assessment_id": str(obj.id), "subject_id": str(obj.subject_id), "provenance_kind": obj.provenance.get("kind")})


@router.post("/mcp")
async def mcp(request: Request, db: Session = Depends(get_db)):
    body = await request.json(); rpc_id = body.get("id"); method = body.get("method")
    if method and method.startswith("notifications/"):
        return Response(status_code=202)
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "TasteGraph", "version": SERVER_VERSION}, "instructions": "TasteGraph is a cross-AI experience memory. Use get_concept before writes when possible. Save direct user experiences only after approval. Store AI analysis of external evidence as assessments, never as user experiences."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params") or {}; name = params.get("name"); args = params.get("arguments") or {}
        required = "reviews:write" if name in {"save_experience", "save_assessment"} else "reviews:read"
        try:
            principal = _principal(request, required)
        except TokenError as exc:
            result = _auth_result(str(exc))
        else:
            try:
                if name == "search": result = _search(db, principal, args)
                elif name == "fetch": result = _fetch(db, principal, args)
                elif name == "get_concept": result = _get_concept(db, args)
                elif name == "save_experience": result = _save_experience(db, principal, args)
                elif name == "save_assessment": result = _save_assessment(db, principal, args)
                else: return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Unknown tool"}})
            except Exception as exc:
                db.rollback(); result = _tool_error("TasteGraph server error while executing tool", details={"type": type(exc).__name__, "message": str(exc)})
    else:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}})
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


@router.get("/mcp")
def mcp_get():
    return JSONResponse({"service": "TasteGraph MCP", "version": SERVER_VERSION, "transport": "Streamable HTTP", "method": "POST", "oauth_resource_metadata": f"{_base()}/.well-known/oauth-protected-resource", "tools": [tool["name"] for tool in TOOLS]}, status_code=405, headers={"Allow": "POST"})
