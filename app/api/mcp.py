from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Principal, TokenError, principal_from_authorization
from app.db.session import get_db
from app.models.entities import Experience, IdempotencyRecord, SchemaDefinition, Subject
from app.schemas.common import CommonExperienceData, Consent, ExperienceCreate, Provenance
from app.schemas.domains import DOMAIN_MODELS
from app.services.core import create_experience, publish_experience, request_hash

router = APIRouter()
PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "1.3.0"

READ_SECURITY = [{"type": "oauth2", "scopes": ["reviews:read"]}]
WRITE_SECURITY = [{"type": "oauth2", "scopes": ["reviews:write"]}]


def _tool_security(schemes: list[dict]) -> dict:
    return {"securitySchemes": schemes, "_meta": {"securitySchemes": schemes}}


def _common_schema() -> dict:
    return CommonExperienceData.model_json_schema()


TOOLS = [
    {
        "name": "search",
        "title": "Search TasteGraph reviews",
        "description": "Use this when the user asks what they have reviewed, wants recent reviews, wants a prior experience, or needs taste evidence for a recommendation. Query may be omitted for recent reviews.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": ""},
                "subject_type": {"type": "string", "enum": ["recipe", "restaurant"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            },
            "additionalProperties": False,
        },
        **_tool_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "fetch",
        "title": "Fetch a TasteGraph review",
        "description": "Use this when a prior search returned a review that needs to be inspected in full.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "TasteGraph experience ID returned by search or save_review."}},
            "required": ["id"],
            "additionalProperties": False,
        },
        **_tool_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "get_schema",
        "title": "Get a TasteGraph review schema",
        "description": "Use this before save_review to get the exact common_data and domain_data fields accepted for a recipe or restaurant review.",
        "inputSchema": {
            "type": "object",
            "properties": {"subject_type": {"type": "string", "enum": ["recipe", "restaurant"]}},
            "required": ["subject_type"],
            "additionalProperties": False,
        },
        **_tool_security(READ_SECURITY),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "save_review",
        "title": "Save an approved TasteGraph review",
        "description": "Use this only after the user explicitly approves the completed review. Call get_schema first when constructing common_data or domain_data. Empty objects are valid when no structured dimensions were supplied by the user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_type": {"type": "string", "enum": ["recipe", "restaurant"]},
                "subject_name": {"type": "string", "minLength": 1, "maxLength": 240},
                "canonical_key": {"type": "string", "minLength": 1, "maxLength": 300},
                "canonical_identifiers": {"type": "object", "default": {}},
                "subject_metadata": {"type": "object", "default": {}},
                "headline": {"type": "string", "minLength": 1, "maxLength": 240},
                "summary": {"type": "string", "minLength": 1},
                "common_data": {"type": "object", "description": "Must match common_schema returned by get_schema. {} is valid."},
                "domain_data": {"type": "object", "description": "Must match domain_schema returned by get_schema. {} is valid."},
                "visibility": {"type": "string", "enum": ["private", "unlisted", "public", "aggregate_only"], "default": "private"},
                "user_approved": {"type": "boolean", "description": "Must be true only after explicit approval in the conversation."},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                "source_client": {"type": "string", "maxLength": 120, "default": "mcp-client"},
            },
            "required": ["subject_type", "subject_name", "canonical_key", "headline", "summary", "common_data", "domain_data", "user_approved", "idempotency_key"],
            "additionalProperties": False,
        },
        **_tool_security(WRITE_SECURITY),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
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
    return {
        "content": [{"type": "text", "text": f"Authentication required: {message}."}],
        "isError": True,
        "_meta": {"mcp/www_authenticate": [challenge]},
    }


def _principal(request: Request, scope: str) -> Principal:
    return principal_from_authorization(request.headers.get("authorization"), scope)


def _search(db: Session, principal: Principal, args: dict) -> dict:
    query = str(args.get("query", "")).strip()
    limit = max(1, min(int(args.get("limit", 10)), 20))
    stmt = select(Experience, Subject).join(Subject, Experience.subject_id == Subject.id).where(
        Experience.owner_id == principal.user_id,
        Experience.deleted_at.is_(None),
        Subject.deleted_at.is_(None),
    )
    if args.get("subject_type"):
        stmt = stmt.where(Experience.subject_type == args["subject_type"])
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(
            Subject.name.ilike(pattern), Subject.canonical_key.ilike(pattern),
            Experience.headline.ilike(pattern), Experience.summary.ilike(pattern),
        ))
    rows = db.execute(stmt.order_by(Experience.created_at.desc()).limit(limit)).all()
    results = [{
        "id": str(exp.id), "title": f"{subject.name} — {exp.headline}",
        "subject_type": exp.subject_type, "subject_name": subject.name,
        "headline": exp.headline, "summary": exp.summary,
        "url": f"{_base()}/api/v1/experiences/{exp.id}",
    } for exp, subject in rows]
    return _result({"count": len(results), "results": results})


def _fetch(db: Session, principal: Principal, args: dict) -> dict:
    try:
        experience_id = uuid.UUID(str(args.get("id", "")))
    except ValueError:
        return _tool_error("Invalid review ID")
    row = db.execute(select(Experience, Subject).join(Subject, Experience.subject_id == Subject.id).where(
        Experience.id == experience_id,
        Experience.owner_id == principal.user_id,
        Experience.deleted_at.is_(None),
        Subject.deleted_at.is_(None),
    )).first()
    if not row:
        return _tool_error("Review not found")
    exp, subject = row
    return _result({
        "id": str(exp.id),
        "title": f"{subject.name} — {exp.headline}",
        "text": exp.summary,
        "url": f"{_base()}/api/v1/experiences/{exp.id}",
        "metadata": {
            "subject": {"id": str(subject.id), "type": subject.subject_type, "name": subject.name,
                        "canonical_key": subject.canonical_key, "identifiers": subject.canonical_identifiers,
                        "metadata": subject.metadata_json},
            "headline": exp.headline, "common_data": exp.common_data, "domain_data": exp.domain_data,
            "visibility": exp.visibility, "publication_status": exp.publication_status,
            "created_at": exp.created_at.isoformat(),
        },
    })


def _get_schema(db: Session, args: dict) -> dict:
    subject_type = args.get("subject_type")
    row = db.scalar(select(SchemaDefinition).where(
        SchemaDefinition.subject_type == subject_type,
        SchemaDefinition.status == "stable",
    ).order_by(SchemaDefinition.version.desc()))
    model = DOMAIN_MODELS.get(subject_type)
    if not row or not model:
        return _tool_error("Schema not found")
    return _result({
        "subject_type": row.subject_type,
        "version": row.version,
        "status": row.status,
        "common_schema": _common_schema(),
        "domain_schema": model.model_json_schema(),
    })


def _save(db: Session, principal: Principal, args: dict, request_id: str) -> dict:
    if args.get("user_approved") is not True:
        return _tool_error("Explicit user approval is required before saving")

    subject_type = args.get("subject_type")
    domain_model = DOMAIN_MODELS.get(subject_type)
    if not domain_model:
        return _tool_error(f"Unsupported subject_type: {subject_type}")

    try:
        common = CommonExperienceData.model_validate(args.get("common_data") or {})
        domain = domain_model.model_validate(args.get("domain_data") or {})
    except ValidationError as exc:
        return _tool_error("Review data failed TasteGraph schema validation", details=exc.errors(include_url=False))

    idempotency_key = str(args.get("idempotency_key", ""))
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    p_hash = request_hash(relevant)
    existing = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.client_id == principal.client_id,
        IdempotencyRecord.key == idempotency_key,
    ))
    if existing:
        if existing.request_hash != p_hash:
            return _tool_error("Idempotency key was reused for different review content")
        return _result(existing.response_body)

    subject = db.scalar(select(Subject).where(
        Subject.subject_type == subject_type,
        Subject.canonical_key == args["canonical_key"],
        Subject.deleted_at.is_(None),
    ))
    if not subject:
        subject = Subject(
            subject_type=subject_type,
            name=args["subject_name"],
            canonical_key=args["canonical_key"],
            canonical_identifiers=args.get("canonical_identifiers", {}),
            metadata_json=args.get("subject_metadata", {}),
        )
        db.add(subject)
        db.commit()
        db.refresh(subject)

    payload = ExperienceCreate(
        owner_id=principal.user_id,
        subject_id=subject.id,
        subject_type=subject_type,
        schema_version="1.0",
        visibility=args.get("visibility", "private"),
        headline=args["headline"],
        summary=args["summary"],
        common_data=common,
        domain_data=domain.model_dump(mode="json", exclude_none=True),
        provenance=Provenance(source_method="llm_conversation", source_client=args.get("source_client", "mcp-client")),
        consent=Consent(user_approved=False),
    )
    exp = create_experience(db, payload, client_id=principal.client_id, auth_subject=principal.subject, request_id=request_id)
    exp = publish_experience(db, exp, 1, actor_id=principal.subject, client_id=principal.client_id, request_id=request_id)
    body = {
        "saved": True, "experience_id": str(exp.id), "subject_id": str(subject.id),
        "subject_name": subject.name, "publication_status": exp.publication_status,
        "headline": exp.headline, "url": f"{_base()}/api/v1/experiences/{exp.id}",
    }
    db.add(IdempotencyRecord(
        client_id=principal.client_id, key=idempotency_key, request_hash=p_hash,
        response_status=200, response_body=body,
    ))
    db.commit()
    return _result(body)


@router.post("/mcp")
async def mcp(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    rpc_id = body.get("id")
    method = body.get("method")
    if method and method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "TasteGraph", "version": SERVER_VERSION},
            "instructions": "TasteGraph is the connected user's structured review memory. Search and fetch prior reviews when relevant. Call get_schema before constructing structured review fields. Save only a completed review the user explicitly approved.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        required = "reviews:write" if name == "save_review" else "reviews:read"
        try:
            principal = _principal(request, required)
        except TokenError as exc:
            result = _auth_result(str(exc))
        else:
            try:
                if name == "search":
                    result = _search(db, principal, args)
                elif name == "fetch":
                    result = _fetch(db, principal, args)
                elif name == "get_schema":
                    result = _get_schema(db, args)
                elif name == "save_review":
                    result = _save(db, principal, args, request.state.request_id)
                else:
                    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Unknown tool"}})
            except Exception as exc:
                db.rollback()
                result = _tool_error("TasteGraph server error while executing tool", details={"type": type(exc).__name__, "message": str(exc)})
    else:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}})

    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


@router.get("/mcp")
def mcp_get():
    return JSONResponse(
        {
            "service": "TasteGraph MCP",
            "transport": "Streamable HTTP",
            "method": "POST",
            "oauth_resource_metadata": f"{_base()}/.well-known/oauth-protected-resource",
            "tools": [tool["name"] for tool in TOOLS],
        },
        status_code=405,
        headers={"Allow": "POST"},
    )
