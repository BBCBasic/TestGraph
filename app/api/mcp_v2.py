from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
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
    AssessmentCreate, CollectionAssessment, ExperienceCreate, FieldEnsure, SubjectContextEnsure,
    SubjectEnrichmentCheck, SubjectEnsure,
)
from app.services.semantic import add_semantic_relationship, resolve_subject_hierarchy, retire_semantic_relationship
from app.services.v2 import (
    add_subject_type_alias, create_assessment, create_experience, delete_owned_experience,
    descendant_type_ids, ensure_field, ensure_subject, ensure_subject_context,
    _deep_fill_missing, fields_for_type, resolve_subject_type, vocabulary_index,
)
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write

router = APIRouter()
PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "3.10.1-alpha"
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
            "unapplied_sources why it yielded no stored discovery. Every applied path must declare a generic "
            "retrieval_uses entry explaining how it helps future identity, likely queries, location, classification, "
            "relationships, comparison or verification. Treat enrichment as preparation for future TestGraph "
            "searches: register information someone may realistically search for later, and do not store facts "
            "merely because they are available. Treat this as shared graph building: substantial discovery work for "
            "this subject becomes reusable for later searches, while this user can benefit from useful enrichment "
            "contributed for other subjects. A subject's own canonical URL is a stable "
            "identifier and must be stored in identifiers when found. If enrichment cannot be found, use unavailable "
            "with a reason and the searches attempted. Use not_applicable with a "
            "reason when external enrichment has no sensible application. Collection assessment is mandatory: "
            "declare whether the subject belongs to a wider collection, and when it does, save the collection as "
            "subject_context with its authoritative directory URL and a relationship to reviewed_subject. Submit "
            "every member exposed by a finite authoritative directory as an unreviewed subject and connect each one "
            "to the collection. The server rejects mismatched discovered and submitted counts. Location is optional; "
            "never invent facts "
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
                                "{'https://example.test': ['identifiers.website', "
                                "'subject_context.subjects[0].identifiers.branch_directory']}."
                            ),
                            "additionalProperties": {
                                "type": "array", "minItems": 1,
                                "items": {"type": "string", "minLength": 1},
                            },
                            "default": {},
                        },
                        "retrieval_uses": {
                            "type": "object",
                            "description": (
                                "Map every applied request path to its generic TestGraph purpose. Store a fact only "
                                "when it helps future identity, likely queries, location, classification, relationships, "
                                "comparison or server verification. Give likely query examples for every purpose other "
                                "than verification. Facts that are merely available but have no plausible graph or "
                                "retrieval use must not be stored."
                            ),
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "roles": {
                                        "type": "array", "minItems": 1,
                                        "items": {
                                            "type": "string",
                                            "enum": [
                                                "identity", "likely_query", "location",
                                                "classification", "relationship", "comparison",
                                                "verification",
                                            ],
                                        },
                                    },
                                    "likely_queries": {
                                        "type": "array", "default": [],
                                        "items": {"type": "string", "minLength": 1},
                                    },
                                    "reason": {"type": "string", "minLength": 1},
                                },
                                "required": ["roles", "reason"],
                                "additionalProperties": False,
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
                "collection_assessment": {
                    "type": "object",
                    "description": (
                        "Mandatory wider-collection assessment. member requires a collection name, type, "
                        "authoritative directory URL, discovered count, and submitted_member_refs naming "
                        "reviewed_subject plus every discovered sibling in subject_context. The server derives the "
                        "submitted count, requires it to equal discovered_count, and verifies every member relationship. "
                        "independent requires evidence_sources or search attempts. unavailable requires "
                        "unavailability_kind, attempts and a reason, and is only for genuine collection-identity or "
                        "authoritative-source failure. It is rejected when collection signals are already known or "
                        "when the reason is size, effort, inconvenience, latency, a quick review or deferred work. "
                        "ambiguous blocks the save. There is no deferred or lazy status."
                    ),
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["member", "independent", "unavailable", "ambiguous"],
                        },
                        "collection_name": {"type": "string", "minLength": 1},
                        "collection_type": {"type": "string", "minLength": 1},
                        "directory_url": {"type": "string", "minLength": 1},
                        "discovered_count": {"type": "integer", "minimum": 2},
                        "submitted_member_refs": {
                            "type": "array", "minItems": 2, "maxItems": 500,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "source_manifest": {
                            "type": "object",
                            "description": (
                                "Exhaustive authoritative source-surface manifest. Discover paginated directories, "
                                "sitemaps, official APIs, regional directories and member pages before deriving the "
                                "collection. Every member must map to at least one consulted source and no unresolved "
                                "source route may remain."
                            ),
                            "properties": {
                                "coverage_method": {
                                    "type": "string",
                                    "enum": [
                                        "single_page", "pagination", "sitemap", "api",
                                        "multi_source", "search_derived",
                                    ],
                                },
                                "declared_source_count": {"type": "integer", "minimum": 1},
                                "source_pages": {
                                    "type": "array", "minItems": 1, "maxItems": 500,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "url": {"type": "string", "minLength": 1},
                                            "source_kind": {
                                                "type": "string",
                                                "enum": [
                                                    "directory_page", "sitemap", "api",
                                                    "official_member_page", "other_authoritative",
                                                ],
                                            },
                                            "sequence": {"type": "integer", "minimum": 1},
                                            "member_refs": {
                                                "type": "array", "minItems": 1, "maxItems": 500,
                                                "items": {"type": "string", "minLength": 1},
                                            },
                                            "next_url": {"type": "string", "minLength": 1},
                                            "terminal": {"type": "boolean", "default": False},
                                        },
                                        "required": ["url", "source_kind", "member_refs"],
                                        "additionalProperties": False,
                                    },
                                },
                                "discovery_queries": {
                                    "type": "array", "minItems": 1, "maxItems": 50,
                                    "items": {"type": "string", "minLength": 1},
                                },
                                "exhaustion_evidence": {"type": "string", "minLength": 1},
                                "unresolved_source_urls": {
                                    "type": "array", "maxItems": 500, "default": [],
                                    "items": {"type": "string", "minLength": 1},
                                },
                            },
                            "required": [
                                "coverage_method", "declared_source_count", "source_pages",
                                "discovery_queries", "exhaustion_evidence",
                            ],
                            "additionalProperties": False,
                        },
                        "evidence_sources": {
                            "type": "array", "maxItems": 50, "default": [],
                            "items": {"type": "string", "minLength": 1},
                        },
                        "unavailability_kind": {
                            "type": "string",
                            "enum": [
                                "collection_identity_not_found",
                                "authoritative_source_not_found",
                                "authoritative_source_inaccessible",
                            ],
                            "description": (
                                "Required only for unavailable. Operational cost, collection size, inconvenience, "
                                "latency, quick-review scope and deferred work are never valid categories."
                            ),
                        },
                        "attempts": {
                            "type": "array", "maxItems": 50, "default": [],
                            "items": {"type": "string", "minLength": 1},
                        },
                        "reason": {"type": "string", "minLength": 1},
                        "candidate_collections": {
                            "type": "array", "maxItems": 20, "default": [],
                            "items": {"type": "string", "minLength": 1},
                        },
                        "checked_at": {"type": "string", "format": "date-time"},
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
                "raw_text", "subject_enrichment_check", "collection_assessment",
                "user_approved", "idempotency_key",
            ],
            "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    },
    {
        "name": "delete_experience",
        "title": "Delete a user-owned review",
        "description": (
            "Permanently delete one review only after the authenticated user explicitly requests deletion. "
            "Ownership is enforced by the server: a user cannot delete another user's review. Dependent AI "
            "assessments are deleted with the review. The subject is deleted only when it was created by the "
            "same user, has no remaining reviews and has no subject relationships; otherwise it is preserved. "
            "Do not ask for a second confirmation when the current user request already explicitly authorises "
            "deletion."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "delete_orphan_subject": {"type": "boolean", "default": True},
                "confirm_deletion": {"type": "boolean"},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
            },
            "required": ["id", "confirm_deletion", "idempotency_key"],
            "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {
            "readOnlyHint": False, "destructiveHint": True,
            "idempotentHint": True, "openWorldHint": False,
        },
    },
    {
        "name": "correct_subject_fact",
        "title": "Correct an existing subject fact",
        "description": (
            "Replace one incorrect identifier or attribute using the stable subject ID. The current value must "
            "match expected_value, authoritative evidence and a reason are mandatory, and the server preserves "
            "an immutable correction record in subject provenance. Use enrich_subject for missing facts; never use "
            "this operation merely to add a value."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_id": {"type": "string", "format": "uuid"},
                "field_root": {"type": "string", "enum": ["identifiers", "attributes"]},
                "field_path": {
                    "type": "string", "pattern": "^[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)*$",
                    "description": "Dot-separated path below field_root.",
                },
                "expected_value": {},
                "corrected_value": {},
                "evidence_sources": {
                    "type": "array", "minItems": 1, "maxItems": 20,
                    "items": {"type": "string", "minLength": 1},
                },
                "reason": {"type": "string", "minLength": 1},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
            },
            "required": [
                "subject_id", "field_root", "field_path", "expected_value", "corrected_value",
                "evidence_sources", "reason", "idempotency_key",
            ],
            "additionalProperties": False,
        },
        **_security(WRITE_SECURITY),
        "annotations": {
            "readOnlyHint": False, "destructiveHint": True,
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



def _validate_subject_context(db, raw):
    try:
        context = SubjectContextEnsure.model_validate(raw or {})
    except ValueError as exc:
        return None, _error(
            "Invalid subject context",
            {"code": "subject_context_invalid", "reason": str(exc)},
        )

    reserved_refs = {"reviewed_subject", "subject"}
    seen_refs = set()
    invalid_refs = []
    for item in context.subjects:
        if item.ref in reserved_refs or item.ref in seen_refs:
            invalid_refs.append(item.ref)
        seen_refs.add(item.ref)

    known_refs = reserved_refs | seen_refs
    invalid_relationships = []
    for index, item in enumerate(context.relationships):
        missing_refs = sorted({
            ref for ref in (item.source_ref, item.target_ref) if ref not in known_refs
        })
        if missing_refs:
            invalid_relationships.append({
                "index": index,
                "source_ref": item.source_ref,
                "target_ref": item.target_ref,
                "missing_refs": missing_refs,
            })

    if invalid_refs or invalid_relationships:
        return None, _error(
            "Subject context contains invalid references",
            {
                "code": "subject_context_references_invalid",
                "reserved_or_duplicate_refs": sorted(set(invalid_refs)),
                "invalid_relationships": invalid_relationships,
                "instruction": (
                    "Use a unique non-reserved ref for every related subject and make every "
                    "relationship refer only to reviewed_subject, subject, or a declared subject ref."
                ),
            },
        )

    unresolved_types = []
    for item in context.subjects:
        try:
            resolved = resolve_subject_type(db, item.subject_type)
        except ValueError:
            resolved = None
        if resolved is None:
            unresolved_types.append(item.subject_type)

    if unresolved_types:
        return None, _error(
            "Subject context contains unresolved subject types",
            {
                "code": "subject_context_types_unresolved",
                "unknown_subject_types": sorted(set(unresolved_types)),
                "instruction": (
                    "Inspect vocabulary_index and call resolve_subject_hierarchy for every unknown "
                    "subject type before retrying the unchanged save or enrichment request."
                ),
            },
        )

    return context, None


def _subject_candidates(db, subject_type, canonical_key):
    term = str(canonical_key or "").strip()
    if not term:
        return []
    pattern = f"%{term}%"
    rows = list(db.scalars(
        select(V2Subject).where(
            V2Subject.subject_type_id == subject_type.id,
            V2Subject.deleted_at.is_(None),
            or_(V2Subject.canonical_key.ilike(pattern), V2Subject.name.ilike(pattern)),
        ).order_by(V2Subject.name).limit(5)
    ).all())
    return [
        {"subject_id": str(item.id), "name": item.name, "canonical_key": item.canonical_key}
        for item in rows
    ]


def _locate_subject_for_write(db, args):
    raw_subject_id = args.get("subject_id")
    if raw_subject_id:
        try:
            subject_id = uuid.UUID(str(raw_subject_id))
        except ValueError:
            return None, None, _error(
                "Invalid subject ID", {"code": "subject_id_invalid"}
            )
        subject = db.get(V2Subject, subject_id)
        if not subject or subject.deleted_at:
            return None, None, _error(
                "Subject not found", {"code": "subject_not_found", "subject_id": str(subject_id)}
            )
        subject_type = db.get(SubjectType, subject.subject_type_id)
        return subject, subject_type, None

    type_term = args.get("subject_type")
    canonical_key = args.get("canonical_key")
    if not type_term or not canonical_key:
        return None, None, _error(
            "Stable subject locator required",
            {
                "code": "subject_locator_required",
                "instruction": (
                    "Supply subject_id from search, fetch or save_experience. The legacy "
                    "subject_type plus canonical_key pair is also accepted."
                ),
            },
        )
    subject_type = resolve_subject_type(db, type_term)
    if not subject_type:
        return None, None, _error("Subject type not found", {"code": "subject_type_not_found"})
    subject = db.scalar(select(V2Subject).where(
        V2Subject.subject_type_id == subject_type.id,
        V2Subject.canonical_key == canonical_key,
        V2Subject.deleted_at.is_(None),
    ))
    if not subject:
        return None, None, _error(
            "Subject not found",
            {
                "code": "subject_not_found",
                "canonical_key": canonical_key,
                "candidates": _subject_candidates(db, subject_type, canonical_key),
                "instruction": "Select only a confirmed candidate and retry using its subject_id.",
            },
        )
    return subject, subject_type, None


def _enrich_subject(db, principal, args):
    client_id = f"{principal.client_id}:v3"
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    payload_hash, prior = begin_idempotent_write(
        db, client_id=client_id, key=f"subject-enrichment:{args['idempotency_key']}",
        payload=relevant,
    )
    if prior is not None:
        return _result(prior)

    subject, subject_type, locator_error = _locate_subject_for_write(db, args)
    if locator_error is not None:
        return locator_error
    if not any((
        args.get("identifiers"), args.get("attributes"), args.get("provenance"),
        args.get("subject_context"),
    )):
        return _error("No subject enrichment was supplied", {"code": "subject_enrichment_empty"})

    context_payload, context_error = _validate_subject_context(
        db, args.get("subject_context")
    )
    if context_error is not None:
        return context_error
    enrichment_check, enrichment_error = _validate_subject_enrichment_check(
        args.get("subject_enrichment_check"), args,
        allowed_roots={
            "identifiers", "attributes", "provenance", "subject_context",
            "collection_assessment",
        },
    )
    if enrichment_error is not None:
        return enrichment_error
    collection_assessment, collection_error = _validate_collection_assessment(
        args.get("collection_assessment"), args, enrichment_check,
        primary_ref="subject", retry_tool="enrich_subject", attribute_key="attributes",
        provenance_key="provenance",
    )
    if collection_error is not None:
        return collection_error

    check_record = {
        "status": enrichment_check.status,
        "sources": enrichment_check.sources,
        "applied_fields": enrichment_check.applied_fields,
        "retrieval_uses": {
            path: use.model_dump(mode="json")
            for path, use in enrichment_check.retrieval_uses.items()
        },
        "unapplied_sources": enrichment_check.unapplied_sources,
        "collection_assessment": collection_assessment.model_dump(mode="json"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_client": client_id,
    }
    provenance = deepcopy(args.get("provenance", {}))
    provenance.setdefault("enrichment_checks", {})[args["idempotency_key"]] = check_record

    _, identifier_additions, identifier_conflicts = _deep_fill_missing(
        subject.identifiers_json, args.get("identifiers", {}), prefix="identifiers",
    )
    _, attribute_additions, attribute_conflicts = _deep_fill_missing(
        subject.attributes_json, args.get("attributes", {}), prefix="attributes",
    )
    _, provenance_additions, provenance_conflicts = _deep_fill_missing(
        subject.provenance_json, args.get("provenance", {}), prefix="provenance",
    )

    subject = ensure_subject(
        db,
        SubjectEnsure(
            subject_type=subject_type.canonical_name, name=subject.name,
            canonical_key=subject.canonical_key, identifiers=args.get("identifiers", {}),
            attributes=args.get("attributes", {}), provenance=provenance,
        ),
        client_id, owner_id=principal.user_id, commit=False,
    )
    context = ensure_subject_context(
        db, subject, context_payload,
        client_id=client_id, owner_id=principal.user_id, commit=False,
    )
    related_changed = any(
        item.get("created") or item.get("fields_added")
        for item in context["subjects"] + context["relationships"]
    )
    fields_added = identifier_additions + attribute_additions + provenance_additions
    conflicts = identifier_conflicts + attribute_conflicts + provenance_conflicts
    changed = bool(fields_added or related_changed)
    body = {
        "enriched": changed, "changed": changed,
        "subject_id": str(subject.id), "subject_type": subject_type.canonical_name,
        "canonical_key": subject.canonical_key, "fields_added": fields_added,
        "conflicts_preserved": conflicts,
        "identifiers": subject.identifiers_json, "attributes": subject.attributes_json,
        "provenance": subject.provenance_json, "subject_context": context,
    }
    finish_idempotent_write(
        db, client_id=client_id, key=f"subject-enrichment:{args['idempotency_key']}",
        payload_hash=payload_hash, response_body=body,
    )
    return _result(body)


def _nested_value(root, parts):
    value = root
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _correct_subject_fact(db, principal, args):
    client_id = f"{principal.client_id}:v3"
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    payload_hash, prior = begin_idempotent_write(
        db, client_id=client_id, key=f"subject-correction:{args['idempotency_key']}",
        payload=relevant,
    )
    if prior is not None:
        return _result(prior)
    subject, subject_type, locator_error = _locate_subject_for_write(
        db, {"subject_id": args.get("subject_id")}
    )
    if locator_error is not None:
        return locator_error

    parts = args["field_path"].split(".")
    root_name = args["field_root"]
    root = deepcopy(
        subject.identifiers_json if root_name == "identifiers" else subject.attributes_json
    )
    exists, current = _nested_value(root, parts)
    if not exists:
        return _error(
            "Subject fact does not exist",
            {
                "code": "subject_fact_missing",
                "instruction": "Use enrich_subject to add a missing fact.",
                "field": f"{root_name}.{args['field_path']}",
            },
        )
    if current != args["expected_value"]:
        return _error(
            "Subject correction conflict",
            {
                "code": "subject_correction_conflict",
                "field": f"{root_name}.{args['field_path']}",
                "expected_value": args["expected_value"],
                "current_value": current,
                "instruction": "Reassess the current value and evidence before retrying.",
            },
        )
    target = root
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = deepcopy(args["corrected_value"])

    provenance = deepcopy(subject.provenance_json or {})
    corrections = deepcopy(provenance.get("subject_corrections") or {})
    corrections[args["idempotency_key"]] = {
        "field": f"{root_name}.{args['field_path']}",
        "previous_value": current,
        "corrected_value": deepcopy(args["corrected_value"]),
        "evidence_sources": list(args["evidence_sources"]),
        "reason": args["reason"],
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "corrected_by": client_id,
    }
    provenance["subject_corrections"] = corrections
    if root_name == "identifiers":
        subject.identifiers_json = root
    else:
        subject.attributes_json = root
    subject.provenance_json = provenance
    db.flush()
    body = {
        "corrected": True, "subject_id": str(subject.id),
        "subject_type": subject_type.canonical_name,
        "field": f"{root_name}.{args['field_path']}",
        "previous_value": current, "corrected_value": args["corrected_value"],
        "correction_record": corrections[args["idempotency_key"]],
    }
    finish_idempotent_write(
        db, client_id=client_id, key=f"subject-correction:{args['idempotency_key']}",
        payload_hash=payload_hash, response_body=body,
    )
    return _result(body)


def _request_path_parts(path):
    parts = []
    for segment in path.split("."):
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", segment)
        if match is None:
            return None
        parts.append(match.group(1))
        if match.group(2) is not None:
            parts.append(match.group(2))
    return parts


def _request_path_exists(args, path, allowed_roots=None):
    parts = _request_path_parts(path)
    allowed_roots = allowed_roots or {
        "identifiers", "subject_attributes", "subject_provenance", "subject_context",
        "collection_assessment",
    }
    if not parts or parts[0] not in allowed_roots:
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


def _validate_subject_enrichment_check(raw, args=None, allowed_roots=None):
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
        applied_paths = {
            path for paths in check.applied_fields.values() for path in paths
        }
        retrieval_paths = set(check.retrieval_uses)
        missing_retrieval_uses = sorted(applied_paths - retrieval_paths)
        unknown_retrieval_uses = sorted(retrieval_paths - applied_paths)
        queryless_uses = sorted(
            path for path, use in check.retrieval_uses.items()
            if any(role != "verification" for role in use.roles)
            and not use.likely_queries
        )
        if missing_retrieval_uses or unknown_retrieval_uses or queryless_uses:
            return None, _error(
                "Every stored discovery requires a generic TestGraph retrieval purpose",
                {
                    "code": "subject_enrichment_retrieval_use_invalid",
                    "missing_retrieval_uses": missing_retrieval_uses,
                    "unknown_retrieval_uses": unknown_retrieval_uses,
                    "uses_without_likely_queries": queryless_uses,
                    "instruction": (
                        "For every applied request path, state how it helps identity, likely queries, "
                        "location, classification, relationships, comparison or verification. Give "
                        "likely query examples unless the path exists only for server verification. "
                        "Do not store facts merely because the source publishes them."
                    ),
                },
            )
        invalid_paths = {
            source: [
                path for path in paths
                if not _request_path_exists(args or {}, path, allowed_roots)
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


_COLLECTION_SIGNAL_KEYS = {
    "brand", "chain", "collection", "collection_name", "group", "locations",
    "location_directory", "branch_directory", "directory_url", "parent_company",
    "parent_organization", "parent_organisation",
}
_COLLECTION_RELATIONSHIPS = {
    "branch_of", "location_of", "member_of", "owned_by", "part_of",
    "subsidiary_of", "variant_of",
}
_COLLECTION_OPERATIONAL_EXCUSE_RE = re.compile(
    r"(disproportionate|quick\s+review|too\s+many|too\s+large|"
    r"time[-\s]?consuming|not\s+enough\s+time|inconvenien|"
    r"\beffort\b|\blatency\b|\bdefer(?:red|ring)?\b|come\s+back\s+later)",
    re.IGNORECASE,
)


def _normalise_collection_token(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _contains_collection_signal(value):
    if isinstance(value, dict):
        for key, item in value.items():
            normalised_key = _normalise_collection_token(key)
            if normalised_key in _COLLECTION_SIGNAL_KEYS and item not in (None, "", [], {}):
                return True
            if (
                normalised_key == "relationship"
                and _normalise_collection_token(item) in _COLLECTION_RELATIONSHIPS
            ):
                return True
            if _contains_collection_signal(item):
                return True
    elif isinstance(value, list):
        return any(_contains_collection_signal(item) for item in value)
    return False


def _contains_exact_value(value, expected):
    if isinstance(value, dict):
        return any(_contains_exact_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_value(item, expected) for item in value)
    return value == expected


def _collection_action_required(
    code, required_action, *, retry_tool="save_experience", **details,
):
    return _error(
        "Collection assessment incomplete",
        {
            "status": "action_required",
            "code": code,
            "required_action": required_action,
            "retry_tool": retry_tool,
            **details,
        },
    )


def _validate_collection_source_manifest(
    assessment, enrichment_sources, submitted_refs, *, retry_tool,
):
    manifest = assessment.source_manifest
    if manifest is None:
        return _collection_action_required(
            "collection_source_manifest_required",
            (
                "Use web search to discover every authoritative directory surface for the collection, "
                "then submit the complete source manifest before deriving members."
            ),
            retry_tool=retry_tool,
        )

    pages = manifest.source_pages
    page_urls = [page.url for page in pages]
    if len(page_urls) != len(set(page_urls)):
        return _collection_action_required(
            "collection_source_urls_not_unique",
            "Remove duplicate source-page URLs from the collection source manifest.",
            retry_tool=retry_tool,
        )
    if manifest.declared_source_count != len(pages):
        return _collection_action_required(
            "collection_source_count_mismatch",
            "Submit every discovered authoritative source page before retrying.",
            retry_tool=retry_tool,
            declared_source_count=manifest.declared_source_count,
            submitted_source_count=len(pages),
        )
    if manifest.unresolved_source_urls:
        return _collection_action_required(
            "collection_sources_unresolved",
            "Inspect every unresolved authoritative source route and resubmit only when none remain.",
            retry_tool=retry_tool,
            unresolved_source_urls=manifest.unresolved_source_urls,
        )
    if assessment.directory_url not in set(page_urls):
        return _collection_action_required(
            "collection_directory_missing_from_manifest",
            "Include the authoritative directory URL in source_manifest.source_pages.",
            retry_tool=retry_tool,
            directory_url=assessment.directory_url,
        )

    unreconciled_pages = sorted(set(page_urls) - enrichment_sources)
    if unreconciled_pages:
        return _collection_action_required(
            "collection_source_pages_unreconciled",
            "Add every authoritative source page to subject_enrichment_check.sources and reconcile it.",
            retry_tool=retry_tool,
            unreconciled_source_pages=unreconciled_pages,
        )
    missing_evidence_pages = sorted(set(page_urls) - set(assessment.evidence_sources))
    if missing_evidence_pages:
        return _collection_action_required(
            "collection_source_pages_not_evidence",
            "Include every source-manifest page in collection_assessment.evidence_sources.",
            retry_tool=retry_tool,
            missing_evidence_pages=missing_evidence_pages,
        )

    empty_pages = [page.url for page in pages if not page.member_refs]
    if empty_pages:
        return _collection_action_required(
            "collection_source_pages_without_members",
            "Map every authoritative source page to the member refs derived from it.",
            retry_tool=retry_tool,
            source_pages=empty_pages,
        )
    covered_refs = {
        member_ref for page in pages for member_ref in page.member_refs
    }
    submitted_set = set(submitted_refs)
    unknown_refs = sorted(covered_refs - submitted_set)
    missing_refs = sorted(submitted_set - covered_refs)
    if unknown_refs or missing_refs:
        return _collection_action_required(
            "collection_source_member_coverage_mismatch",
            "Map exactly every submitted collection member to at least one authoritative source page.",
            retry_tool=retry_tool,
            unknown_member_refs=unknown_refs,
            uncovered_member_refs=missing_refs,
        )

    if manifest.coverage_method == "pagination":
        sequences = [page.sequence for page in pages]
        expected = list(range(1, len(pages) + 1))
        if None in sequences or sorted(sequences) != expected:
            return _collection_action_required(
                "collection_pagination_incomplete",
                "Number every pagination source continuously from 1 through the terminal page.",
                retry_tool=retry_tool,
                submitted_sequences=sequences,
                expected_sequences=expected,
            )
        ordered = sorted(pages, key=lambda page: page.sequence)
        terminal_pages = [page for page in ordered if page.terminal]
        if len(terminal_pages) != 1 or terminal_pages[0].sequence != len(ordered):
            return _collection_action_required(
                "collection_pagination_terminal_invalid",
                "Mark exactly the final pagination page as terminal.",
                retry_tool=retry_tool,
            )
        broken_links = []
        for index, page in enumerate(ordered):
            expected_next = ordered[index + 1].url if index + 1 < len(ordered) else None
            if page.next_url != expected_next:
                broken_links.append({
                    "sequence": page.sequence,
                    "url": page.url,
                    "submitted_next_url": page.next_url,
                    "expected_next_url": expected_next,
                })
        if broken_links:
            return _collection_action_required(
                "collection_pagination_links_incomplete",
                "Follow and record every next-page link through the terminal page.",
                retry_tool=retry_tool,
                broken_links=broken_links,
            )
    else:
        unresolved_links = sorted({
            page.next_url for page in pages
            if page.next_url and page.next_url not in set(page_urls)
        })
        if unresolved_links:
            return _collection_action_required(
                "collection_source_links_unresolved",
                "Inspect every discovered next source URL before declaring discovery exhausted.",
                retry_tool=retry_tool,
                unresolved_source_urls=unresolved_links,
            )
    return None


def _validate_collection_assessment(
    raw, args, enrichment_check, *, primary_ref="reviewed_subject",
    retry_tool="save_experience", attribute_key="subject_attributes",
    provenance_key="subject_provenance",
):
    def action(code, required_action, **details):
        return _collection_action_required(
            code, required_action, retry_tool=retry_tool, **details,
        )
    if raw is None:
        return None, action(
            "collection_assessment_required",
            "Determine whether the target subject belongs to a wider collection, submit the evidence, and retry.",
        )
    try:
        assessment = CollectionAssessment.model_validate(raw)
    except ValueError as exc:
        return None, action(
            "collection_assessment_invalid",
            "Correct the collection assessment and retry.",
            reason=str(exc),
        )

    enrichment_sources = set(enrichment_check.sources)
    unknown_evidence = set(assessment.evidence_sources) - enrichment_sources
    if unknown_evidence:
        return None, action(
            "collection_evidence_not_reconciled",
            "Include every collection evidence URL in subject_enrichment_check.sources and reconcile it.",
            unknown_evidence_sources=sorted(unknown_evidence),
        )

    if assessment.status == "member":
        missing = [
            name for name, value in {
                "collection_name": assessment.collection_name,
                "collection_type": assessment.collection_type,
                "directory_url": assessment.directory_url,
                "discovered_count": assessment.discovered_count,
                "submitted_member_refs": assessment.submitted_member_refs,
                "source_manifest": assessment.source_manifest,
            }.items()
            if value is None or value == []
        ]
        if missing:
            return None, action(
                "collection_member_details_required",
                "Supply the collection identity, authoritative directory URL, discovered count, every submitted member ref and the exhaustive source manifest.",
                missing_fields=missing,
            )
        if assessment.directory_url not in enrichment_sources:
            return None, action(
                "collection_directory_source_required",
                "Add the authoritative directory URL to subject_enrichment_check.sources and reconcile it.",
                directory_url=assessment.directory_url,
            )

        context = args.get("subject_context") or {}
        subjects = context.get("subjects") or []
        collection_subjects = [
            subject for subject in subjects
            if str(subject.get("name", "")).strip().casefold()
            == assessment.collection_name.strip().casefold()
        ]
        if not collection_subjects:
            return None, action(
                "collection_subject_required",
                "Add the named collection as an unreviewed subject in subject_context.",
                collection_name=assessment.collection_name,
            )
        collection_subject = collection_subjects[0]
        if not _contains_exact_value(
            {
                "identifiers": collection_subject.get("identifiers", {}),
                "attributes": collection_subject.get("attributes", {}),
                "provenance": collection_subject.get("provenance", {}),
            },
            assessment.directory_url,
        ):
            return None, action(
                "collection_directory_not_stored",
                "Store the authoritative directory URL on the collection subject.",
                collection_ref=collection_subject.get("ref"),
                directory_url=assessment.directory_url,
            )

        if not _contains_exact_value(
            collection_subject.get("attributes", {}),
            assessment.discovered_count,
        ):
            return None, action(
                "collection_member_count_not_stored",
                "Store discovered_count on the collection subject attributes.",
                collection_ref=collection_subject.get("ref"),
                discovered_count=assessment.discovered_count,
            )

        collection_ref = collection_subject.get("ref")
        linked = bool(collection_ref) and any(
            {relationship.get("source_ref"), relationship.get("target_ref")}
            == {primary_ref, collection_ref}
            for relationship in context.get("relationships") or []
        )
        if not linked:
            return None, action(
                "collection_relationship_required",
                "Connect the target subject to the collection subject in subject_context.",
                collection_ref=collection_ref,
            )

        submitted_refs = assessment.submitted_member_refs
        if not submitted_refs:
            return None, action(
                "collection_members_required",
                "Submit the target subject and every discovered collection member in submitted_member_refs.",
            )
        if len(submitted_refs) != len(set(submitted_refs)):
            return None, action(
                "collection_member_refs_not_unique",
                "Remove duplicate submitted_member_refs and retry.",
            )
        known_refs = {primary_ref} | {
            subject.get("ref") for subject in subjects if subject.get("ref")
        }
        unknown_refs = sorted(set(submitted_refs) - known_refs)
        if unknown_refs:
            return None, action(
                "collection_member_refs_unknown",
                "Every submitted member ref must be the target subject or a subject_context subject.",
                unknown_refs=unknown_refs,
            )
        if collection_ref in submitted_refs:
            return None, action(
                "collection_subject_is_not_member",
                "Do not count the collection subject itself as one of its members.",
                collection_ref=collection_ref,
            )
        if primary_ref not in submitted_refs:
            return None, action(
                "reviewed_subject_not_counted",
                "Include the target subject in submitted_member_refs.",
            )
        submitted_count = len(submitted_refs)
        if submitted_count != assessment.discovered_count:
            return None, action(
                "collection_member_count_mismatch",
                "Submit every discovered member before retrying; lazy or future materialisation is not accepted.",
                discovered_count=assessment.discovered_count,
                submitted_count=submitted_count,
                missing_count=assessment.discovered_count - submitted_count,
            )
        manifest_error = _validate_collection_source_manifest(
            assessment, enrichment_sources, submitted_refs, retry_tool=retry_tool,
        )
        if manifest_error is not None:
            return None, manifest_error
        relationships = context.get("relationships") or []
        unlinked_refs = sorted(
            member_ref for member_ref in submitted_refs
            if not any(
                {relationship.get("source_ref"), relationship.get("target_ref")}
                == {member_ref, collection_ref}
                for relationship in relationships
            )
        )
        if unlinked_refs:
            return None, action(
                "collection_members_not_linked",
                "Connect every submitted member to the collection.",
                unlinked_refs=unlinked_refs,
                collection_ref=collection_ref,
            )

    elif assessment.status == "independent":
        if not assessment.evidence_sources and not assessment.attempts:
            return None, action(
                "collection_independent_evidence_required",
                "Provide an authoritative source or a concise record of the collection-membership searches performed.",
            )
        collection_data = {
            "identifiers": args.get("identifiers", {}),
            "subject_attributes": args.get(attribute_key, {}),
            "subject_provenance": args.get(provenance_key, {}),
            "subject_context": args.get("subject_context", {}),
        }
        if _contains_collection_signal(collection_data):
            return None, action(
                "collection_assessment_inconsistent",
                "The save contains collection or parent-organisation signals; classify it as member or remove the inconsistent data.",
            )

    elif assessment.status == "unavailable":
        if (
            not assessment.unavailability_kind
            or not assessment.reason
            or not assessment.attempts
        ):
            return None, action(
                "collection_unavailable_details_required",
                "Record unavailability_kind, attempted searches and the genuine identity or authoritative-source failure.",
            )
        explanation = " ".join([assessment.reason, *assessment.attempts])
        if _COLLECTION_OPERATIONAL_EXCUSE_RE.search(explanation):
            return None, action(
                "collection_unavailable_operational_excuse",
                "Collection size, effort, inconvenience, latency, quick-review scope and deferred work are not valid reasons. Complete the collection assessment before saving.",
            )
        collection_data = {
            "identifiers": args.get("identifiers", {}),
            "subject_attributes": args.get(attribute_key, {}),
            "subject_provenance": args.get(provenance_key, {}),
            "subject_context": args.get("subject_context", {}),
        }
        explicit_collection_evidence = any([
            assessment.collection_name,
            assessment.collection_type,
            assessment.directory_url,
            assessment.discovered_count is not None,
            assessment.submitted_member_refs,
            assessment.source_manifest is not None,
            assessment.candidate_collections,
        ])
        if explicit_collection_evidence or _contains_collection_signal(collection_data):
            return None, action(
                "collection_unavailable_inconsistent",
                "Known collection evidence cannot be classified unavailable. Complete member assessment, or use ambiguous if the collection identity genuinely cannot be resolved.",
            )

    elif assessment.status == "ambiguous":
        return None, _error(
            "Collection membership is ambiguous",
            {
                "status": "action_required",
                "code": "collection_membership_ambiguous",
                "reason": assessment.reason,
                "candidate_collections": assessment.candidate_collections,
                "required_action": "Ask only for the clarification needed to identify the collection.",
                "retry_tool": retry_tool,
            },
        )

    return assessment, None


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
    collection_assessment, collection_error = _validate_collection_assessment(
        args.get("collection_assessment"), args, enrichment_check
    )
    if collection_error is not None:
        return collection_error
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
    context_payload, context_error = _validate_subject_context(
        db, args.get("subject_context")
    )
    if context_error is not None:
        return context_error
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
        db, subject, context_payload,
        client_id=client_id, owner_id=principal.user_id,
    )
    exp = create_experience(
        db,
        ExperienceCreate(
            owner_id=principal.user_id, subject_id=subject.id, headline=args["headline"],
            summary=args["summary"], raw_text=args["raw_text"],
            structured_data=args.get("structured_data", {}),
            experienced_at=args.get("experienced_at"),
            visibility=args.get("visibility", "private"), user_approved=True,
            subject_enrichment_check=enrichment_check,
            collection_assessment=collection_assessment, source_client=client_id,
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
        "collection_assessment": exp.provenance.get("collection_assessment"),
    }
    finish_idempotent_write(db, client_id=client_id, key=f"experience:{args['idempotency_key']}", payload_hash=payload_hash, response_body=body)
    return _result(body)


def _delete_experience(db, principal, args):
    if args.get("confirm_deletion") is not True:
        return _error("Explicit deletion confirmation is required")
    if principal.user_id is None:
        return _error("Authenticated TasteGraph user is required")
    try:
        experience_id = uuid.UUID(str(args.get("id", "")))
    except ValueError:
        return _error("Invalid experience ID")
    client_id = f"{principal.client_id}:v3"
    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}
    payload_hash, prior = begin_idempotent_write(
        db, client_id=client_id, key=f"delete-experience:{args['idempotency_key']}",
        payload=relevant,
    )
    if prior is not None:
        return _result(prior)
    try:
        body = delete_owned_experience(
            db, experience_id, principal.user_id,
            delete_orphan_subject=args.get("delete_orphan_subject", True),
        )
    except ValueError:
        return _error("Experience not found")
    finish_idempotent_write(
        db, client_id=client_id, key=f"delete-experience:{args['idempotency_key']}",
        payload_hash=payload_hash, response_body=body,
    )
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
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "TasteGraph v2", "version": SERVER_VERSION}, "instructions": "Before saving an experience, identify exactly what was experienced and inspect vocabulary_index. Reuse an existing canonical type or alias whenever possible. If the specific type is absent, reason from meaning to a broad-to-specific hierarchy and call resolve_subject_hierarchy; never create a type merely because it arrived first. Before saving, perform the generic subject enrichment check and include its result in subject_enrichment_check. Use authoritative or primary sources where available, but do not require a website, location or any domain-specific field. Reconcile every consulted source with the request paths it populated, or explain why it yielded no stored discovery. For every applied path, declare its generic retrieval_uses purpose and likely query examples: identity, likely query, location, classification, relationship, comparison or verification. Do not store facts merely because a source publishes them; omit facts with no plausible future TestGraph retrieval or graph use. When the subject has its own canonical URL, store it as an identifier. Perform routine checking and retry automatically; do not ask the user unless identity is genuinely ambiguous. If enrichment cannot be found, use unavailable with a reason and the searches attempted. Register information you may realistically search for later against what you save in TestGraph; do not collect facts merely because they are available. Treat enrichment as shared graph work: accept substantial discovery work now because captured knowledge is reusable in later searches, and users benefit reciprocally from useful enrichment contributed for other subjects. Save discoveries as unreviewed subject_context with generic relationships and source provenance, while attaching the review only to the exact subject experienced. Always submit collection_assessment. Collection unavailable is only for a genuine failure to identify the collection or find/access authoritative evidence; it is invalid when collection signals are known and never valid because the collection is large, inconvenient, slow, effortful, part of a quick review or deferred until later. When the subject belongs to a wider collection, do not stop at one company landing page. Use web search to discover every authoritative collection surface, including pagination, sitemaps, official APIs, regional directories and member pages. Return a complete source_manifest that maps every member to its consulted source pages, records discovery queries and exhaustion evidence, follows pagination to a terminal page and leaves no unresolved source URL. Save the collection as an unreviewed subject and preserve its authoritative directory URL and discovered count. Submit reviewed_subject plus every derived member as unreviewed subject_context, connect every member to the collection, and list those refs in submitted_member_refs. The server rejects incomplete source coverage and requires submitted_count to equal discovered_count; unreviewed status, collection size and future materialisation are not valid omissions. On a location-based recommendation, never conclude there is no relevant result from the target-town search alone: also search the relevant subject type without a text query, follow reviewed subjects to parent organisations, inspect their official branch directories for the requested location, and add any discovered branch as an unreviewed related subject. Do this routine chain lookup without asking the user. If authoritative information was missed during the original save, use enrich_subject to add it without creating another review. Location is optional: for a physical location record town and coordinates only when explicitly published by the source, otherwise record the published address; skip location when irrelevant. If the official source is unavailable, preserve that limitation and never invent facts or silently geocode coordinates. The experience date defaults to creation time unless explicitly provided. When structured data matches an existing globally registered canonical field, include it in the save: TestGraph attaches that field to the subject type automatically after validation. Do not ask for routine confirmation, omit the structured value, or demote it to raw_text merely because the field has not previously been used for that subject type. Only genuinely new reusable fields require register_field. Reviews store stable subject-type IDs, while belongs_to relationships provide the evolving semantic structure. Preserve exact user wording in raw_text and AI analysis in save_assessment."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params") or {}; name = params.get("name"); args = params.get("arguments") or {}
        write_names = {"resolve_subject_hierarchy", "register_subject_type_alias", "set_type_relationship", "retire_type_relationship", "register_field", "enrich_subject", "correct_subject_fact", "save_experience", "delete_experience", "save_assessment"}
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
                elif name == "correct_subject_fact": result = _correct_subject_fact(db, principal, args)
                elif name == "save_experience": result = _save_experience(db, principal, args)
                elif name == "delete_experience": result = _delete_experience(db, principal, args)
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
