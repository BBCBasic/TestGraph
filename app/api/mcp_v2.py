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
from app.services.semantic import list_alias_candidates, propose_alias
from app.services.v2 import (
    create_assessment,
    create_experience,
    ensure_concept,
    ensure_subject,
    list_field_proposals,
    normalise_path,
    propose_concept_fields,
    vocabulary,
)
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write

router = APIRouter()
PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "2.1.0-alpha"
READ_SECURITY = [{"type": "oauth2", "scopes": ["reviews:read"]}]
WRITE_SECURITY = [{"type": "oauth2", "scopes": ["reviews:write"]}]


def _security(schemes):
    return {"securitySchemes": schemes, "_meta": {"securitySchemes": schemes}}


def _proposal_schema():
    return {
        "type": "object",
        "properties": {
            "submitted_name": {"type": "string"},
            "canonical_name": {"type": "string"},
            "json_schema": {
                "type": "object",
                "description": "Complete JSON Schema for this field, including object properties and array items.",
                "additionalProperties": True,
            },
            "description": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["submitted_name", "canonical_name", "json_schema"],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "search",
        "title": "Search TasteGraph v2 experiences",
        "description": "Search the connected user's direct experiences across any domain.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "concept_path": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}}, "additionalProperties": False},
        **_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "fetch",
        "title": "Fetch a TasteGraph v2 experience",
        "description": "Fetch one complete direct experience, including submitted and canonical structured data.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False},
        **_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "get_concept",
        "title": "Get canonical concept vocabulary",
        "description": "Check canonical fields, accepted aliases, and unresolved client proposals before writing. TasteGraph does not infer semantic equivalence itself.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        **_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "propose_concept_fields",
        "title": "Propose canonical concept fields",
        "description": "Propose fully specified JSON Schema fields without creating an experience. Proposals remain pending until the user approves them on the development concept-fields page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept_path": {"type": "string"},
                "concept_description": {"type": "string"},
                "fields": {"type": "array", "minItems": 1, "items": _proposal_schema()},
            },
            "required": ["concept_path", "fields"],
            "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "propose_alias",
        "title": "Propose a semantic alias mapping",
        "description": "Use your own language understanding to propose that an unfamiliar term means an existing canonical field. TasteGraph records your authenticated client identity, detects disagreement, and accepts the alias only after independent client consensus.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept_path": {"type": "string"},
                "alias": {"type": "string"},
                "canonical_name": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
            },
            "required": ["concept_path", "alias", "canonical_name"],
            "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "save_experience",
        "title": "Save an approved direct experience",
        "description": "Only call after explicit user approval. The concept and every structured field must already be approved. This tool never changes the schema. Reuse idempotency_key on retry.",
        "inputSchema": {"type": "object", "properties": {
            "concept_path": {"type": "string"}, "subject_name": {"type": "string"},
            "canonical_key": {"type": "string"}, "identifiers": {"type": "object", "additionalProperties": True, "default": {}},
            "subject_attributes": {"type": "object", "additionalProperties": True, "default": {}}, "headline": {"type": "string"},
            "summary": {"type": "string"}, "raw_text": {"type": "string", "minLength": 1}, "structured_data": {"type": "object", "additionalProperties": True, "default": {}},
            "visibility": {"type": "string", "enum": ["private", "unlisted", "public", "aggregate_only"], "default": "private"},
            "user_approved": {"type": "boolean"},
            "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200}},
            "required": ["concept_path", "subject_name", "canonical_key", "headline", "summary", "raw_text", "user_approved", "idempotency_key"],
            "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "save_assessment",
        "title": "Save AI-derived assessment",
        "description": "Save AI analysis of external evidence separately from user experiences. Reuse idempotency_key on retry.",
        "inputSchema": {"type": "object", "properties": {
            "subject_id": {"type": "string"}, "assessment_type": {"type": "string"},
            "evidence": {"type": "object", "additionalProperties": True, "default": {}}, "analysis": {"type": "object", "additionalProperties": True, "default": {}},
            "conclusion": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "source_model": {"type": "string"},
            "provenance": {"type": "object", "additionalProperties": True, "default": {}}, "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200}},
            "required": ["subject_id", "assessment_type", "idempotency_key"], "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    },
]


def _base(): return get_settings().public_base_url.rstrip("/")
def _text(payload): return [{"type": "text", "text": json.dumps(payload, default=str, separators=(",", ":"))}]
def _result(payload): return {"content": _text(payload), "structuredContent": payload}
def _error(message, details=None):
    payload = {"error": message}
    if details is not None: payload["details"] = details
    return {"content": _text(payload), "structuredContent": payload, "isError": True}


def _auth_error(message):
    challenge = f'Bearer resource_metadata="{_base()}/.well-known/oauth-protected-resource/mcp-v2", error="insufficient_scope", error_description="{message}"'
    return {"content": [{"type": "text", "text": f"Authentication required: {message}."}], "isError": True, "_meta": {"mcp/www_authenticate": [challenge]}}


def _principal(request: Request, scope: str) -> Principal:
    return principal_from_authorization(request.headers.get("authorization"), scope, expected_resource=f"{_base()}/mcp-v2")


def _search(db, principal, args):
    q = str(args.get("query", "")).strip(); limit = max(1, min(int(args.get("limit", 10)), 20))
    stmt = select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.owner_id == principal.user_id, V2Experience.deleted_at.is_(None))
    if args.get("concept_path"): stmt = stmt.where(Concept.path == args["concept_path"])
    if q:
        p = f"%{q}%"; stmt = stmt.where(or_(V2Subject.name.ilike(p), V2Subject.canonical_key.ilike(p), V2Experience.headline.ilike(p), V2Experience.summary.ilike(p)))
    rows = db.execute(stmt.order_by(V2Experience.created_at.desc()).limit(limit)).all()
    return _result({"count": len(rows), "results": [{"id": str(e.id), "subject_id": str(s.id), "concept_path": c.path, "subject_name": s.name, "headline": e.headline, "summary": e.summary} for e,s,c in rows]})


def _fetch(db, principal, args):
    try: exp_id = uuid.UUID(str(args.get("id", "")))
    except ValueError: return _error("Invalid experience ID")
    row = db.execute(select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.id == exp_id, V2Experience.owner_id == principal.user_id, V2Experience.deleted_at.is_(None))).first()
    if not row: return _error("Experience not found")
    e,s,c = row
    return _result({"id": str(e.id), "subject": {"id": str(s.id), "name": s.name, "canonical_key": s.canonical_key, "concept_path": c.path, "identifiers": s.identifiers_json, "attributes": s.attributes_json}, "headline": e.headline, "summary": e.summary, "raw_text": e.raw_text, "structured_data": e.structured_data, "submitted_data": e.submitted_data, "normalization_log": e.normalization_log, "provenance": e.provenance, "created_at": e.created_at.isoformat()})


def _get_concept(db, args):
    from app.services.v2 import normalise_path
    try: path = normalise_path(str(args.get("path", "")))
    except ValueError as exc: return _error(str(exc))
    concept = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
    if not concept:
        return _error("Concept not found", {"path": path, "instruction": "A new concept may be created during save. Do not invent a new field when an existing canonical field already expresses the meaning."})
    vocab = vocabulary(db, concept); unique = {f.id: f for f in vocab["fields"].values()}
    return _result({
        "path": concept.path,
        "version": concept.version,
        "description": concept.description,
        "fields": [{"canonical_name": f.canonical_name, "json_schema": (f.metadata_json or {}).get("json_schema", {"type": f.data_type}), "description": f.description, "origin": vocab["origins"].get(f.canonical_name)} for f in unique.values()],
        "pending_field_proposals": [{
            "id": str(p.id),
            "submitted_name": p.submitted_name,
            "canonical_name": p.canonical_name,
            "json_schema": p.json_schema,
            "description": p.description,
            "aliases": p.aliases_json,
            "proposed_by": p.proposer_client_id,
            "status": p.status,
        } for p in list_field_proposals(db, concept, status="pending")],
        "accepted_aliases": vocab["aliases"],
        "alias_candidates": list_alias_candidates(db, concept),
        "semantic_policy": "New fields remain pending until explicit user approval. Experience writes never change the concept schema.",
    })


def _propose_concept_fields(db, principal, args):
    proposals = [FieldProposal.model_validate(item) for item in args.get("fields", [])]
    if not proposals:
        return _error("At least one field proposal is required")
    client_id = f"{principal.client_id}:v2"
    concept = ensure_concept(
        db,
        ConceptEnsure(
            path=args["concept_path"],
            description=args.get("concept_description"),
            created_by=client_id,
        ),
    )
    rows = propose_concept_fields(
        db,
        concept=concept,
        proposals=proposals,
        proposer_client_id=client_id,
    )
    return _result({
        "concept_path": concept.path,
        "concept_version": concept.version,
        "proposals": [{
            "id": str(row.id),
            "canonical_name": row.canonical_name,
            "json_schema": row.json_schema,
            "status": row.status,
        } for row in rows],
        "approval_url": f"{_base()}/development/concept-fields",
        "experience_created": False,
    })


def _propose_alias(db, principal, args):
    from app.services.v2 import normalise_path
    path = normalise_path(str(args.get("concept_path", "")))
    concept = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
    if not concept: return _error("Concept not found", {"path": path})
    status = propose_alias(
        db,
        concept=concept,
        alias=str(args.get("alias", "")),
        canonical_name=str(args.get("canonical_name", "")),
        proposer_client_id=f"{principal.client_id}:v2",
        confidence=args.get("confidence"),
        rationale=args.get("rationale"),
    )
    db.commit()
    return _result(status)


def _save_experience(db, principal, args):
    if args.get("user_approved") is not True:
        return _error("Explicit user approval is required before saving a direct experience")
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    client_id = f"{principal.client_id}:v2"
    payload_hash, prior = begin_idempotent_write(
        db,
        client_id=client_id,
        key=f"experience:{args['idempotency_key']}",
        payload=relevant,
    )
    if prior is not None:
        return _result(prior)

    path = normalise_path(str(args["concept_path"]))
    concept = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
    if not concept:
        return _error(
            "Concept does not exist",
            {"concept_path": path, "instruction": "Use propose_concept_fields and approve its fields before saving."},
        )
    subject = ensure_subject(
        db,
        SubjectEnsure(
            concept_path=concept.path,
            name=args["subject_name"],
            canonical_key=args["canonical_key"],
            identifiers=args.get("identifiers", {}),
            attributes=args.get("subject_attributes", {}),
            create_concept_if_missing=False,
        ),
        client_id,
    )
    exp = create_experience(
        db,
        ExperienceCreate(
            owner_id=principal.user_id,
            subject_id=subject.id,
            headline=args["headline"],
            summary=args["summary"],
            raw_text=args["raw_text"],
            structured_data=args.get("structured_data", {}),
            visibility=args.get("visibility", "private"),
            user_approved=True,
            source_client=client_id,
        ),
        client_id,
    )
    body = {
        "saved": True,
        "experience_id": str(exp.id),
        "subject_id": str(subject.id),
        "concept_path": concept.path,
        "canonical_data": exp.structured_data,
        "normalization_log": exp.normalization_log,
        "alias_candidates": list_alias_candidates(db, concept),
    }
    finish_idempotent_write(
        db,
        client_id=client_id,
        key=f"experience:{args['idempotency_key']}",
        payload_hash=payload_hash,
        response_body=body,
    )
    return _result(body)
def _save_assessment(db, principal, args):
    try: subject_id = uuid.UUID(str(args["subject_id"]))
    except (ValueError, KeyError): return _error("Invalid subject_id")
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    client_id = f"{principal.client_id}:v2"
    payload_hash, prior = begin_idempotent_write(db, client_id=client_id, key=f"assessment:{args['idempotency_key']}", payload=relevant)
    if prior is not None: return _result(prior)
    obj = create_assessment(db, AssessmentCreate(subject_id=subject_id, user_id=principal.user_id, assessment_type=args["assessment_type"], evidence=args.get("evidence", {}), analysis=args.get("analysis", {}), conclusion=args.get("conclusion"), confidence=args.get("confidence"), source_model=args.get("source_model"), provenance=args.get("provenance", {})))
    body = {"saved": True, "assessment_id": str(obj.id), "subject_id": str(obj.subject_id), "provenance_kind": obj.provenance.get("kind")}
    finish_idempotent_write(db, client_id=client_id, key=f"assessment:{args['idempotency_key']}", payload_hash=payload_hash, response_body=body)
    return _result(body)


@router.post("/mcp-v2")
async def mcp_v2(request: Request, db: Session = Depends(get_db)):
    body = await request.json(); rpc_id = body.get("id"); method = body.get("method")
    if method and method.startswith("notifications/"): return Response(status_code=202)
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "TasteGraph v2", "version": SERVER_VERSION}, "instructions": "Before writing, inspect the concept vocabulary. New fields must be proposed separately and explicitly approved by the user; save_experience never changes a concept schema. Preserve the user's exact words in raw_text and keep AI-derived interpretation in save_assessment."}
    elif method == "ping": result = {}
    elif method == "tools/list": result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params") or {}; name = params.get("name"); args = params.get("arguments") or {}
        scope = "reviews:write" if name in {"propose_concept_fields", "propose_alias", "save_experience", "save_assessment"} else "reviews:read"
        try: principal = _principal(request, scope)
        except TokenError as exc: result = _auth_error(str(exc))
        else:
            try:
                if name == "search": result = _search(db, principal, args)
                elif name == "fetch": result = _fetch(db, principal, args)
                elif name == "get_concept": result = _get_concept(db, args)
                elif name == "propose_concept_fields": result = _propose_concept_fields(db, principal, args)
                elif name == "propose_alias": result = _propose_alias(db, principal, args)
                elif name == "save_experience": result = _save_experience(db, principal, args)
                elif name == "save_assessment": result = _save_assessment(db, principal, args)
                else: return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Unknown tool"}})
            except Exception as exc:
                db.rollback(); result = _error("TasteGraph v2 server error", {"type": type(exc).__name__, "message": str(exc)})
    else:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}})
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


@router.get("/mcp-v2")
def mcp_v2_get():
    return JSONResponse({"service": "TasteGraph v2 MCP", "version": SERVER_VERSION, "transport": "Streamable HTTP", "method": "POST", "oauth_resource_metadata": f"{_base()}/.well-known/oauth-protected-resource/mcp-v2", "tools": [x["name"] for x in TOOLS]}, status_code=405, headers={"Allow": "POST"})
