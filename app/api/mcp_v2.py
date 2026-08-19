from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Principal, TokenError, principal_from_authorization
from app.db.session import get_db
from app.models.v2 import Assessment, SubjectRelationship, SubjectType, SubjectTypeAlias, V2Experience, V2Subject
from app.schemas.v2 import (
    AssessmentCreate, ExperienceCreate, FieldEnsure, SubjectContextEnsure,
    SubjectEnrichmentCheck, SubjectEnsure,
)
from app.services.semantic import add_semantic_relationship, resolve_subject_hierarchy, retire_semantic_relationship
from app.services.v2 import (
    add_subject_type_alias, create_assessment, create_experience,
    descendant_type_ids, ensure_field, ensure_subject, ensure_subject_context,
    fields_for_type, resolve_subject_type, vocabulary_index,
)
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write

router = APIRouter()
PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "3.4.1-alpha"
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
    {"name": "search", "title": "Search reviews and known subjects", "description": "Search reviews plus matching reviewed or unreviewed subjects. Known subjects include immediate subject-to-subject connections so a location, organisation, variant or sibling discovered earlier can inform recommendations without being misrepresented as reviewed. For a location-based recommendation, do not stop when the target-town query has no direct result: also search the relevant subject type without a text query, follow reviewed subjects to parent organisations, and inspect each parent's official branch directory for the requested location before concluding there is no useful connection. Routine chain expansion does not require user confirmation.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "subject_type": {"type": "string"}, "include_related": {"type": "boolean", "default": True}, "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}, "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "fetch", "title": "Fetch a review", "description": "Fetch a complete review with its stable subject type, original words and AI assessments.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}}, "required": ["id"], "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "vocabulary_index", "title": "Inspect standard vocabulary", "description": "List canonical subject types, aliases, flexible relationships and reusable fields. Inspect this before classifying any unknown subject type. There are no DNS storage paths or review leaf concepts.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "resolve_subject_type", "title": "Resolve a subject type", "description": "Resolve flexible input to one stable subject-type ID. Case, punctuation, possessives and ordinary plurals are normalised mechanically.", "inputSchema": {"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"], "additionalProperties": False}, **_security(READ_SECURITY), "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "resolve_subject_hierarchy", "title": "Resolve a semantic subject hierarchy", "description": "Use after vocabulary_index when the specific subject type does not yet exist. Submit terms broad-to-specific, for example ['food','recipe']. The server reuses existing dictionary entries, creates only missing provisional nodes in context, adds belongs_to relationships and rejects cycles. Do not include 'review': review is the record type, not a subject category. Semantic placement must be based on meaning, never on which review arrived first.", "inputSchema": {"type": "object", "properties": {"terms": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string", "minLength": 1}}}, "required": ["terms"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "register_subject_type_alias", "title": "Register a subject-type alias", "description": "Map a genuinely equivalent expression to an existing stable subject type. Never use this to express a category relationship.", "inputSchema": {"type": "object", "properties": {"subject_type": {"type": "string"}, "alias": {"type": "string"}}, "required": ["subject_type", "alias"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "set_type_relationship", "title": "Connect existing subject types", "description": "Add editable classification metadata between existing subject types, such as ferry belongs_to transportation. Unknown types must first be resolved with resolve_subject_hierarchy. Relationships improve broad search but never determine storage IDs.", "inputSchema": {"type": "object", "properties": {"source_type": {"type": "string"}, "relationship": {"type": "string", "default": "belongs_to"}, "target_type": {"type": "string"}}, "required": ["source_type", "target_type"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "retire_type_relationship", "title": "Retire an incorrect subject classification", "description": "Retire one exact semantic relationship while preserving the subject type, subjects and reviews. The retired edge remains as a rejection tombstone, so another AI cannot silently recreate it.", "inputSchema": {"type": "object", "properties": {"source_type": {"type": "string"}, "relationship": {"type": "string", "default": "belongs_to"}, "target_type": {"type": "string"}, "reason": {"type": "string", "minLength": 1}}, "required": ["source_type", "target_type", "reason"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}},
    {"name": "register_field", "title": "Register a reusable field", "description": "Register a genuinely new globally canonical field, or explicitly pre-attach one to subject types. Do not ask the user for routine confirmation to reuse an existing canonical field: a valid existing field is attached automatically on first use. Prefer raw_text for one-off narrative detail.", "inputSchema": {"type": "object", "properties": {"canonical_name": {"type": "string"}, "json_schema": {"type": "object", "additionalProperties": True}, "description": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}}, "subject_types": {"type": "array", "items": {"type": "string"}}}, "required": ["canonical_name", "json_schema", "subject_types"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {
        "name": "enrich_subject",
        "title": "Enrich an existing subject",
        "description": (
            "Add missing identifiers, attributes, provenance and related unreviewed subjects to an existing "
            "subject without creating another review. Use this proactively when authoritative information was "
            "missed during the original save. Search for the official website yourself; for a multi-location "
            "organisation also preserve its official branch-directory URL so future location searches can expand "
            "the chain on demand. Do not ask the user for a URL or routine lookup permission unless automatic "
            "lookup is unavailable or the identity is genuinely ambiguous. Existing "
            "conflicting values are preserved rather than silently overwritten."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_type": {"type": "string"},
                "canonical_key": {"type": "string"},
                "identifiers": {"type": "object", "additionalProperties": True, "default": {}},
                "attributes": {"type": "object", "additionalProperties": True, "default": {}},
                "provenance": {"type": "object", "additionalProperties": True, "default": {}},
                "subject_context": {
                    "type": "object",
                    "description": (
                        "Optional related subjects and relationships. Use subject as the reserved ref "
                        "for the existing subject being enriched."
                    ),
                    "properties": {
                        "subjects": {
                            "type": "array", "maxItems": 50, "default": [],
                            "items": {
                                "type": "object",
                                "properties": {
                                    "ref": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"},
                                    "subject_type": {"type": "string"},
                                    "name": {"type": "string"},
                                    "canonical_key": {"type": "string"},
                                    "identifiers": {"type": "object", "additionalProperties": True, "default": {}},
                                    "attributes": {"type": "object", "additionalProperties": True, "default": {}},
                                    "provenance": {"type": "object", "additionalProperties": True, "default": {}},
                                },
                                "required": ["ref", "subject_type", "name", "canonical_key"],
                                "additionalProperties": False,
                            },
                        },
                        "relationships": {
                            "type": "array", "maxItems": 100, "default": [],
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source_ref": {"type": "string"},
                                    "relationship": {"type": "string"},
                                    "target_ref": {"type": "string"},
                                    "provenance": {"type": "object", "additionalProperties": True, "default": {}},
                                },
                                "required": ["source_ref", "relationship", "target_ref"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "additionalProperties": False,
                },
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
            },
            "required": ["subject_type", "canonical_key", "idempotency_key"],
            "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    },
    {
        "name": "save_experience",
        "title": "Save an approved review",
        "description": (
            "Save a review against an already-resolved stable subject type. Before saving, perform a generic "
            "subject enrichment check using authoritative or primary sources when available. This applies to any "
            "kind of subject and does not require a website, location, address or relationship. Submit the result "
            "in subject_enrichment_check. Perform routine checking and retry automatically rather than asking the "
            "user. Ask the user only when the subject identity is genuinely ambiguous. Add useful discoveries in "
            "identifiers, subject_attributes and subject_context with source provenance, while attaching the review "
            "only to what was actually experienced. A completed check requires at least one source, and every "
            "source must be reconciled: list the request paths populated from it in applied_fields, or explain in "
            "unapplied_sources why it yielded no stored discovery. A subject's own canonical URL is a stable "
            "identifier and must be stored in identifiers when found. If enrichment cannot be found, use unavailable "
            "with a reason and the searches attempted. Use not_applicable with a "
            "reason when external enrichment has no sensible application. Location is optional; never invent facts "
            "or silently geocode coordinates. The experience date defaults to creation time unless experienced_at "
            "is explicit. All context subject types must already be resolved. Existing globally registered fields "
            "such as rating are automatically attached to this subject type on first valid use; preserve them in "
            "structured_data and do not ask for routine confirmation or discard them into raw_text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_type": {"type": "string"},
                "subject_name": {"type": "string"},
                "canonical_key": {"type": "string"},
                "identifiers": {"type": "object", "additionalProperties": True, "default": {}},
                "subject_attributes": {"type": "object", "additionalProperties": True, "default": {}},
                "subject_provenance": {"type": "object", "additionalProperties": True, "default": {}},
                "subject_enrichment_check": {
                    "type": "object",
                    "description": (
                        "Generic pre-save check. completed requires sources; unavailable requires a reason and "
                        "attempts; not_applicable requires a reason; ambiguous stops the save for clarification."
                    ),
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["completed", "unavailable", "not_applicable", "ambiguous"],
                        },
                        "sources": {
                            "type": "array", "maxItems": 50, "default": [],
                            "items": {"type": "string", "minLength": 1},
                        },
                        "applied_fields": {
                            "type": "object",
                            "description": (
                                "Map each source to the save-request paths populated from it, for example "
                                "{'https://example.test': ['identifiers.website', 'subject_attributes.address']}."
                            ),
                            "additionalProperties": {
                                "type": "array", "minItems": 1,
                                "items": {"type": "string", "minLength": 1},
                            },
                            "default": {},
                        },
                        "unapplied_sources": {
                            "type": "object",
                            "description": "Map each source that yielded no stored discovery to a concise reason.",
                            "additionalProperties": {"type": "string", "minLength": 1},
                            "default": {},
                        },
                        "attempts": {
                            "type": "array", "maxItems": 50, "default": [],
                            "items": {"type": "string", "minLength": 1},
                        },
                        "reason": {"type": "string", "minLength": 1},
                        "candidate_identities": {
                            "type": "array", "maxItems": 20, "default": [],
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                    "required": ["status"],
                    "additionalProperties": False,
                },
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "raw_text": {"type": "string", "minLength": 1},
                "structured_data": {"type": "object", "additionalProperties": True, "default": {}},
                "experienced_at": {"type": "string", "format": "date-time"},
                "subject_context": {
                    "type": "object",
                    "description": (
                        "Optional graph enrichment discovered while identifying the reviewed subject. "
                        "Use reviewed_subject as the reserved ref for the subject receiving the review."
                    ),
                    "properties": {
                        "subjects": {
                            "type": "array", "maxItems": 50, "default": [],
                            "items": {
                                "type": "object",
                                "properties": {
                                    "ref": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"},
                                    "subject_type": {"type": "string"},
                                    "name": {"type": "string"},
                                    "canonical_key": {"type": "string"},
                                    "identifiers": {"type": "object", "additionalProperties": True, "default": {}},
                                    "attributes": {"type": "object", "additionalProperties": True, "default": {}},
                                    "provenance": {"type": "object", "additionalProperties": True, "default": {}},
                                },
                                "required": ["ref", "subject_type", "name", "canonical_key"],
                                "additionalProperties": False,
                            },
                        },
                        "relationships": {
                            "type": "array", "maxItems": 100, "default": [],
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source_ref": {"type": "string"},
                                    "relationship": {"type": "string"},
                                    "target_ref": {"type": "string"},
                                    "provenance": {"type": "object", "additionalProperties": True, "default": {}},
                                },
                                "required": ["source_ref", "relationship", "target_ref"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "additionalProperties": False,
                },
                "visibility": {
                    "type": "string",
                    "enum": ["private", "unlisted", "public", "aggregate_only"],
                    "default": "private",
                },
                "user_approved": {"type": "boolean"},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
            },
            "required": [
                "subject_type", "subject_name", "canonical_key", "headline", "summary",
                "raw_text", "subject_enrichment_check", "user_approved", "idempotency_key",
            ],
            "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    },
    {"name": "save_assessment", "title": "Save AI-derived assessment", "description": "Save separately attributed AI analysis against the exact review it evaluates.", "inputSchema": {"type": "object", "properties": {"experience_id": {"type": "string", "format": "uuid"}, "assessment_type": {"type": "string"}, "evidence": {"type": "object", "additionalProperties": True, "default": {}}, "analysis": {"type": "object", "additionalProperties": True, "default": {}}, "conclusion": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "source_model": {"type": "string"}, "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200}}, "required": ["experience_id", "assessment_type", "idempotency_key"], "additionalProperties": False}, **_security(WRITE_SECURITY), "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
]


def _resolve(db, args):
    obj = resolve_subject_type(db, str(args.get("term", "")))
    if not obj:
        return _result({"found": False, "term": args.get("term"), "instruction": "Inspect vocabulary_index, choose the best semantic parent, then call resolve_subject_hierarchy before saving."})
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
    known_subjects = []
    if q or type_term:
        subject_stmt = (
            select(V2Subject, SubjectType)
            .join(SubjectType, V2Subject.subject_type_id == SubjectType.id)
            .where(V2Subject.deleted_at.is_(None))
        )
        if type_term:
            subject_stmt = subject_stmt.where(SubjectType.id.in_(ids))
        if q:
            subject_stmt = subject_stmt.where(or_(
                V2Subject.name.ilike(p), V2Subject.canonical_key.ilike(p),
                cast(V2Subject.identifiers_json, String).ilike(p),
                cast(V2Subject.attributes_json, String).ilike(p),
            ))
        subject_rows = db.execute(subject_stmt.order_by(V2Subject.name).limit(limit)).all()
        for known, known_type in subject_rows:
            review_count = db.scalar(select(func.count(V2Experience.id)).where(
                V2Experience.subject_id == known.id,
                V2Experience.owner_id == principal.user_id,
                V2Experience.deleted_at.is_(None),
            )) or 0
            relationship_rows = list(db.scalars(select(SubjectRelationship).where(
                SubjectRelationship.status == "active",
                or_(
                    SubjectRelationship.source_subject_id == known.id,
                    SubjectRelationship.target_subject_id == known.id,
                ),
            )).all())
            connections = []
            for relation in relationship_rows:
                outgoing = relation.source_subject_id == known.id
                other_id = relation.target_subject_id if outgoing else relation.source_subject_id
                other = db.get(V2Subject, other_id)
                if not other or other.deleted_at:
                    continue
                other_type = db.get(SubjectType, other.subject_type_id)
                connections.append({
                    "direction": "outgoing" if outgoing else "incoming",
                    "relationship": relation.relationship,
                    "subject_id": str(other.id),
                    "subject_name": other.name,
                    "subject_type": other_type.canonical_name if other_type else None,
                    "provenance": relation.provenance_json,
                })
            known_subjects.append({
                "id": str(known.id), "name": known.name, "canonical_key": known.canonical_key,
                "subject_type_id": str(known_type.id), "subject_type": known_type.canonical_name,
                "identifiers": known.identifiers_json, "attributes": known.attributes_json,
                "provenance": known.provenance_json, "review_count": review_count,
                "review_status": "reviewed" if review_count else "unreviewed",
                "connections": connections,
            })
    return _result({
        "count": len(rows),
        "results": [
            {
                "id": str(e.id), "subject_id": str(subject.id), "subject_name": subject.name,
                "subject_type_id": str(subject_type.id), "subject_type": subject_type.canonical_name,
                "headline": e.headline, "summary": e.summary,
            }
            for e, subject, subject_type in rows
        ],
        "known_subjects": known_subjects,
    })


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
    return _result({"id": str(e.id), "record_type": e.record_type, "subject": {"id": str(s.id), "name": s.name, "canonical_key": s.canonical_key, "subject_type_id": str(t.id), "subject_type": t.canonical_name, "identifiers": s.identifiers_json, "attributes": s.attributes_json, "provenance": s.provenance_json}, "headline": e.headline, "summary": e.summary, "raw_text": e.raw_text, "structured_data": e.structured_data, "submitted_data": e.submitted_data, "normalization_log": e.normalization_log, "provenance": e.provenance, "assessments": [{"id": str(a.id), "assessment_type": a.assessment_type, "evidence": a.evidence_json, "analysis": a.analysis_json, "conclusion": a.conclusion, "confidence": a.confidence, "provenance": a.provenance} for a in assessments], "created_at": e.created_at.isoformat()})


def _enrich_subject(db, principal, args):
    client_id = f"{principal.client_id}:v3"
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    payload_hash, prior = begin_idempotent_write(
        db, client_id=client_id, key=f"subject-enrichment:{args['idempotency_key']}",
        payload=relevant,
    )
    if prior is not None:
        return _result(prior)
    subject_type = resolve_subject_type(db, args["subject_type"])
    if not subject_type:
        return _error("Subject type not found")
    subject = db.scalar(select(V2Subject).where(
        V2Subject.subject_type_id == subject_type.id,
        V2Subject.canonical_key == args["canonical_key"],
        V2Subject.deleted_at.is_(None),
    ))
    if not subject:
        return _error("Subject not found")
    if not any((
        args.get("identifiers"), args.get("attributes"), args.get("provenance"),
        args.get("subject_context"),
    )):
        return _error("No subject enrichment was supplied")
    subject = ensure_subject(
        db,
        SubjectEnsure(
            subject_type=subject_type.canonical_name, name=subject.name,
            canonical_key=subject.canonical_key, identifiers=args.get("identifiers", {}),
            attributes=args.get("attributes", {}), provenance=args.get("provenance", {}),
        ),
        client_id,
    )
    context = ensure_subject_context(
        db, subject, SubjectContextEnsure.model_validate(args.get("subject_context") or {}),
        client_id=client_id,
    )
    body = {
        "enriched": True, "subject_id": str(subject.id), "subject_type": subject_type.canonical_name,
        "canonical_key": subject.canonical_key, "identifiers": subject.identifiers_json,
        "attributes": subject.attributes_json, "provenance": subject.provenance_json,
        "subject_context": context,
    }
    finish_idempotent_write(
        db, client_id=client_id, key=f"subject-enrichment:{args['idempotency_key']}",
        payload_hash=payload_hash, response_body=body,
    )
    return _result(body)


def _request_path_exists(args, path):
    parts = path.split(".")
    if not parts or parts[0] not in {
        "identifiers", "subject_attributes", "subject_provenance", "subject_context",
    }:
        return False
    value = args.get(parts[0])
    for part in parts[1:]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return False
    return value is not None


def _validate_subject_enrichment_check(raw, args=None):
    if raw is None:
        return None, _error(
            "Subject enrichment check required",
            {
                "code": "subject_enrichment_check_required",
                "question": "Have you checked for authoritative information about this subject?",
                "instruction": (
                    "Perform the generic subject enrichment check automatically and retry the save. "
                    "Do not ask the user unless the subject identity is genuinely ambiguous."
                ),
            },
        )
    try:
        check = SubjectEnrichmentCheck.model_validate(raw)
    except ValueError as exc:
        return None, _error(
            "Invalid subject enrichment check",
            {"code": "subject_enrichment_check_invalid", "reason": str(exc)},
        )
    if check.status == "completed" and not check.sources:
        return None, _error(
            "A completed subject enrichment check requires at least one source",
            {
                "code": "subject_enrichment_sources_required",
                "instruction": "Add the sources consulted, or use unavailable with a reason and attempted searches.",
            },
        )
    if check.status == "completed":
        sources = set(check.sources)
        applied = set(check.applied_fields)
        unapplied = set(check.unapplied_sources)
        unknown = (applied | unapplied) - sources
        unreconciled = sources - (applied | unapplied)
        duplicated = applied & unapplied
        if unknown or unreconciled or duplicated:
            return None, _error(
                "Every consulted source must be reconciled exactly once",
                {
                    "code": "subject_enrichment_sources_unreconciled",
                    "unknown_sources": sorted(unknown),
                    "unreconciled_sources": sorted(unreconciled),
                    "duplicated_sources": sorted(duplicated),
                    "instruction": (
                        "For each source, list the save-request paths populated from it in applied_fields, "
                        "or give a reason in unapplied_sources when it yielded no stored discovery."
                    ),
                },
            )
        invalid_paths = {
            source: [
                path for path in paths
                if not _request_path_exists(args or {}, path)
            ]
            for source, paths in check.applied_fields.items()
        }
        invalid_paths = {source: paths for source, paths in invalid_paths.items() if paths}
        empty_reasons = [
            source for source, reason in check.unapplied_sources.items() if not reason.strip()
        ]
        if invalid_paths or empty_reasons:
            return None, _error(
                "Source reconciliation does not match the save request",
                {
                    "code": "subject_enrichment_reconciliation_invalid",
                    "invalid_paths": invalid_paths,
                    "empty_reasons": empty_reasons,
                },
            )
    if check.status == "unavailable" and (not check.reason or not check.attempts):
        return None, _error(
            "An unavailable subject enrichment check requires a reason and attempted searches",
            {"code": "subject_enrichment_unavailable_details_required"},
        )
    if check.status == "not_applicable" and not check.reason:
        return None, _error(
            "A not_applicable subject enrichment check requires a reason",
            {"code": "subject_enrichment_reason_required"},
        )
    if check.status == "ambiguous":
        return None, _error(
            "Subject identity is ambiguous",
            {
                "code": "subject_identity_ambiguous",
                "reason": check.reason,
                "candidate_identities": check.candidate_identities,
                "instruction": "Ask the user only for the clarification needed to identify the subject, then retry.",
            },
        )
    return check, None


def _save_experience(db, principal, args):
    if args.get("user_approved") is not True:
        return _error("Explicit user approval is required before saving a direct review")
    if principal.user_id is None:
        return _error("Authenticated TasteGraph user is required")
    enrichment_check, check_error = _validate_subject_enrichment_check(
        args.get("subject_enrichment_check"), args
    )
    if check_error is not None:
        return check_error
    client_id = f"{principal.client_id}:v3"
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    payload_hash, prior = begin_idempotent_write(db, client_id=client_id, key=f"experience:{args['idempotency_key']}", payload=relevant)
    if prior is not None:
        return _result(prior)
    subject_type = resolve_subject_type(db, args["subject_type"])
    if not subject_type:
        return _error(
            f"Unknown subject type '{args['subject_type']}'",
            {"instruction": "Inspect vocabulary_index and call resolve_subject_hierarchy with a broad-to-specific semantic path before resubmitting this review."},
        )
    subject_provenance = dict(args.get("subject_provenance", {}))
    enrichment_sources = list(subject_provenance.get("enrichment_sources", []))
    for source in enrichment_check.sources:
        if source not in enrichment_sources:
            enrichment_sources.append(source)
    if enrichment_sources:
        subject_provenance["enrichment_sources"] = enrichment_sources
    subject = ensure_subject(
        db,
        SubjectEnsure(
            subject_type=subject_type.canonical_name, name=args["subject_name"],
            canonical_key=args["canonical_key"], identifiers=args.get("identifiers", {}),
            attributes=args.get("subject_attributes", {}),
            provenance=subject_provenance,
        ),
        client_id,
    )
    context = ensure_subject_context(
        db, subject, SubjectContextEnsure.model_validate(args.get("subject_context") or {}),
        client_id=client_id,
    )
    exp = create_experience(
        db,
        ExperienceCreate(
            owner_id=principal.user_id, subject_id=subject.id, headline=args["headline"],
            summary=args["summary"], raw_text=args["raw_text"],
            structured_data=args.get("structured_data", {}),
            experienced_at=args.get("experienced_at"),
            visibility=args.get("visibility", "private"), user_approved=True,
            subject_enrichment_check=enrichment_check, source_client=client_id,
        ),
        client_id,
    )
    body = {
        "saved": True, "experience_id": str(exp.id), "subject_id": str(subject.id),
        "subject_type_id": str(subject_type.id), "subject_type": subject_type.canonical_name,
        "type_status": subject_type.status, "type_resolution": "existing", "type_created": False,
        "experienced_at": exp.experienced_at.isoformat(), "canonical_data": exp.structured_data,
        "normalization_log": exp.normalization_log, "subject_context": context,
        "subject_enrichment_check": exp.provenance.get("subject_enrichment_check"),
    }
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
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "TasteGraph v2", "version": SERVER_VERSION}, "instructions": "Before saving an experience, identify exactly what was experienced and inspect vocabulary_index. Reuse an existing canonical type or alias whenever possible. If the specific type is absent, reason from meaning to a broad-to-specific hierarchy and call resolve_subject_hierarchy; never create a type merely because it arrived first. Before saving, perform the generic subject enrichment check and include its result in subject_enrichment_check. Use authoritative or primary sources where available, but do not require a website, location or any domain-specific field. Reconcile every consulted source with the request paths it populated, or explain why it yielded no stored discovery. When the subject has its own canonical URL, store it as an identifier. Perform routine checking and retry automatically; do not ask the user unless identity is genuinely ambiguous. If enrichment cannot be found, use unavailable with a reason and the searches attempted. Save discoveries as unreviewed subject_context with generic relationships and source provenance, while attaching the review only to the exact subject experienced. For a small clearly published set of locations, save sibling locations; for a large chain, preserve the parent organisation's official branch-directory URL and expand it on demand. On a location-based recommendation, never conclude there is no relevant result from the target-town search alone: also search the relevant subject type without a text query, follow reviewed subjects to parent organisations, inspect their official branch directories for the requested location, and add any discovered branch as an unreviewed related subject. Do this routine chain lookup without asking the user. If authoritative information was missed during the original save, use enrich_subject to add it without creating another review. Location is optional: for a physical location record town and coordinates only when explicitly published by the source, otherwise record the published address; skip location when irrelevant. If the official source is unavailable, preserve that limitation and never invent facts or silently geocode coordinates. The experience date defaults to creation time unless explicitly provided. When structured data matches an existing globally registered canonical field, include it in the save: TestGraph attaches that field to the subject type automatically after validation. Do not ask for routine confirmation, omit the structured value, or demote it to raw_text merely because the field has not previously been used for that subject type. Only genuinely new reusable fields require register_field. Reviews store stable subject-type IDs, while belongs_to relationships provide the evolving semantic structure. Preserve exact user wording in raw_text and AI analysis in save_assessment."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params") or {}; name = params.get("name"); args = params.get("arguments") or {}
        write_names = {"resolve_subject_hierarchy", "register_subject_type_alias", "set_type_relationship", "retire_type_relationship", "register_field", "enrich_subject", "save_experience", "save_assessment"}
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
                elif name == "resolve_subject_hierarchy":
                    hierarchy = resolve_subject_hierarchy(db, args["terms"], created_by=f"{principal.client_id}:v3")
                    result = _result({
                        "resolved": True,
                        "leaf_id": str(hierarchy["leaf"].id),
                        "leaf": hierarchy["leaf"].canonical_name,
                        "path": hierarchy["path"],
                        "created_terms": hierarchy["created_terms"],
                        "relationships": hierarchy["relationships"],
                    })
                elif name == "register_subject_type_alias":
                    target = resolve_subject_type(db, args["subject_type"])
                    result = _error("Subject type not found") if not target else _result({"registered": True, "alias": add_subject_type_alias(db, target, args["alias"], source=f"{principal.client_id}:v3").alias, "subject_type_id": str(target.id), "canonical_name": target.canonical_name})
                elif name == "set_type_relationship":
                    source_type = resolve_subject_type(db, args["source_type"])
                    target_type = resolve_subject_type(db, args["target_type"])
                    if not source_type or not target_type:
                        result = _error("Both subject types must already exist", {"instruction": "Use resolve_subject_hierarchy first for unknown subject types."})
                    else:
                        rel = add_semantic_relationship(db, source_type, args.get("relationship", "belongs_to"), target_type, source=f"{principal.client_id}:v3")
                        result = _result({"registered": True, "id": str(rel.id), "source": source_type.canonical_name, "relationship": rel.relationship, "target": target_type.canonical_name})
                elif name == "retire_type_relationship":
                    source_type = resolve_subject_type(db, args["source_type"])
                    target_type = resolve_subject_type(db, args["target_type"])
                    if not source_type or not target_type:
                        result = _error("Both subject types must already exist")
                    else:
                        rel = retire_semantic_relationship(
                            db, source_type, args.get("relationship", "belongs_to"), target_type,
                            reason=args["reason"], retired_by=f"{principal.client_id}:v3",
                        )
                        result = _result({"retired": True, "id": str(rel.id), "source": source_type.canonical_name,
                                          "relationship": rel.relationship, "target": target_type.canonical_name,
                                          "reason": rel.retired_reason})
                elif name == "register_field":
                    field = ensure_field(db, FieldEnsure.model_validate(args), source=f"{principal.client_id}:v3")
                    result = _result({"registered": True, "field_id": str(field.id), "canonical_name": field.canonical_name})
                elif name == "enrich_subject": result = _enrich_subject(db, principal, args)
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
