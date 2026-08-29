from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from html import escape
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import desc, select

from app.core.config import get_settings
from app.core.security import TokenError
from app.db.session import SessionLocal
from app.models.entities import AuditEvent, OAuthAuthorizationCode, OAuthClient
from app.models.v2 import V2Experience, V2Subject
from app.services.guidance import get_induction


WRITE_TOOL_NAMES = {
    "resolve_subject_hierarchy", "register_subject_type_alias", "set_type_relationship",
    "retire_type_relationship", "register_field", "enrich_subject", "correct_subject_fact",
    "save_experience", "delete_experience", "save_assessment", "create_deliberation",
    "claim_deliberation", "submit_contribution", "record_resolution", "assert_location",
    "resolve_location_assertion", "affirm_subject_classification", "propose_subject_reclassification",
    "reopen_subject_classification", "set_review_visibility",
}

GET_INDUCTION_TOOL = {
    "name": "get_induction",
    "title": "Get TestGraph induction and governed guidance",
    "description": (
        "Call this when first using TestGraph, after an MCP refresh, or when you need the current shared operating "
        "guidance. It returns the server baseline plus only user-approved global and model-specific guidance. "
        "Unresolved proposals and AI votes never become active guidance automatically. Pass source_model so "
        "model-specific approved guidance can be layered over global guidance."
    ),
    "inputSchema": {
        "type": "object", "properties": {
            "source_model": {"type": "string", "maxLength": 160,
                             "description": "Optional current model label. gpt and chatgpt are treated as aliases."}
        }, "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}

GET_SERVER_INFO_TOOL = {
    "name": "get_server_info",
    "title": "Get TestGraph server and deployment version",
    "description": (
        "Return the exact TestGraph MCP server version and live deployment identity. Call this immediately before "
        "any write operation and pass the returned write_version_token unchanged as version_check. A token from a "
        "different or older deployment is rejected before any write is attempted. Compare build_sha and deployment_id "
        "with the public /version endpoint when diagnosing stale MCP connections or endpoint mismatches."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}

LIST_REVIEWS_BY_VISIBILITY_TOOL = {
    "name": "list_reviews_by_visibility",
    "title": "List my reviews by visibility",
    "description": (
        "List the authenticated user's reviews in one visibility state and return stable experience IDs plus "
        "1-based positions for conversational shorthand. Positions are display-only: all later mutations must use "
        "the returned experience_id, never the position itself."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "visibility": {
                "type": "string",
                "enum": ["private", "unlisted", "public", "aggregate_only"],
            }
        },
        "required": ["visibility"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}

SET_REVIEW_VISIBILITY_TOOL = {
    "name": "set_review_visibility",
    "title": "Change review visibility",
    "description": (
        "Change one authenticated-user-owned review to private, unlisted, public or aggregate_only using its stable "
        "experience_id. Use a preceding list_reviews_by_visibility result to translate conversational list numbers "
        "back to stable IDs. Setting public also ensures publication_status=published."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "experience_id": {"type": "string", "format": "uuid"},
            "visibility": {
                "type": "string",
                "enum": ["private", "unlisted", "public", "aggregate_only"],
            },
        },
        "required": ["experience_id", "visibility"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}

_VISIBILITIES = {"private", "unlisted", "public", "aggregate_only"}


def _tool_result(payload):
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=str, separators=(",", ":"))}],
        "structuredContent": payload,
    }


def _tool_error(message, details=None):
    payload = {"error": message}
    if details is not None:
        payload["details"] = details
    return {**_tool_result(payload), "isError": True}


def _list_reviews_by_visibility(db, principal, args):
    visibility = str(args.get("visibility", "")).strip()
    if visibility not in _VISIBILITIES:
        return _tool_error("Invalid review visibility")
    if principal.user_id is None:
        return _tool_error("Authenticated TestGraph user is required")
    rows = db.execute(
        select(V2Experience, V2Subject)
        .join(V2Subject, V2Experience.subject_id == V2Subject.id)
        .where(
            V2Experience.owner_id == principal.user_id,
            V2Experience.deleted_at.is_(None),
            V2Experience.visibility == visibility,
        )
        .order_by(V2Experience.created_at.desc(), V2Experience.id.desc())
    ).all()
    return _tool_result({
        "visibility": visibility,
        "count": len(rows),
        "items": [
            {
                "position": index,
                "experience_id": str(experience.id),
                "subject_name": subject.name,
                "headline": experience.headline,
            }
            for index, (experience, subject) in enumerate(rows, start=1)
        ],
        "numbering_rule": (
            "Positions are conversational shorthand only. Use experience_id for every visibility change."
        ),
    })


def _set_review_visibility(db, principal, args):
    visibility = str(args.get("visibility", "")).strip()
    if visibility not in _VISIBILITIES:
        return _tool_error("Invalid review visibility")
    if principal.user_id is None:
        return _tool_error("Authenticated TestGraph user is required")
    try:
        experience_id = uuid.UUID(str(args.get("experience_id", "")))
    except ValueError:
        return _tool_error("Invalid experience ID")
    experience = db.scalar(select(V2Experience).where(
        V2Experience.id == experience_id,
        V2Experience.owner_id == principal.user_id,
        V2Experience.deleted_at.is_(None),
    ))
    if experience is None:
        return _tool_error(
            "Experience not found",
            {"code": "REVIEW_NOT_FOUND_OR_NOT_OWNED"},
        )
    previous = experience.visibility
    experience.visibility = visibility
    if visibility == "public":
        experience.publication_status = "published"
    db.commit()
    return _tool_result({
        "changed": previous != visibility,
        "experience_id": str(experience.id),
        "previous_visibility": previous,
        "visibility": experience.visibility,
        "publication_status": experience.publication_status,
    })


def _server_info(mcp_module) -> dict:
    base = mcp_module._base()
    build_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
    deployment_id = os.getenv("RAILWAY_DEPLOYMENT_ID") or "unknown"
    endpoint = f"{base}/mcp-v2"
    token_material = "|".join([mcp_module.SERVER_VERSION, mcp_module.PROTOCOL_VERSION, build_sha, deployment_id, endpoint])
    write_version_token = hashlib.sha256(token_material.encode("utf-8")).hexdigest()
    return {
        "service": "TestGraph", "api_version": "v2", "server_version": mcp_module.SERVER_VERSION,
        "protocol_version": mcp_module.PROTOCOL_VERSION, "build_sha": build_sha, "deployment_id": deployment_id,
        "service_id": os.getenv("RAILWAY_SERVICE_ID") or "unknown",
        "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT") or "unknown",
        "mcp_endpoint": endpoint, "version_endpoint": f"{base}/version", "write_version_token": write_version_token,
        "write_requirement": "Call get_server_info immediately before every write and pass write_version_token as version_check.",
    }


def _version_error(mcp_module, *, supplied=None):
    current = _server_info(mcp_module)
    message = "TestGraph connection is out of date. Refresh or reconnect TestGraph before writing. No data was changed."
    payload = {
        "error": message, "error_code": "MCP_VERSION_CHECK_REQUIRED", "user_message": message,
        "write_blocked": True, "no_write_performed": True,
        "action_required": (
            "Call get_server_info on the current TestGraph connection immediately before the write, then retry using "
            "its write_version_token as version_check. If get_server_info is not visible, refresh or reconnect TestGraph."
        ),
        "current_server": {key: current[key] for key in ("server_version", "protocol_version", "build_sha", "deployment_id", "mcp_endpoint")},
    }
    if supplied:
        payload["supplied_version_check"] = supplied
    return {**mcp_module._result(payload), "isError": True}


def apply_guidance_tool_policy(tools: list[dict]) -> None:
    by_name = {item.get("name"): item for item in tools}
    read_template = by_name.get("fetch") or by_name.get("search") or {}
    write_template = by_name.get("save_experience") or read_template
    for definition, template in (
        (GET_SERVER_INFO_TOOL, read_template),
        (GET_INDUCTION_TOOL, read_template),
        (LIST_REVIEWS_BY_VISIBILITY_TOOL, read_template),
        (SET_REVIEW_VISIBILITY_TOOL, write_template),
    ):
        if definition["name"] not in by_name:
            tool = dict(definition)
            if "securitySchemes" in template:
                tool["securitySchemes"] = template["securitySchemes"]
            if "_meta" in template:
                tool["_meta"] = template["_meta"]
            tools.insert(0, tool)
    for item in tools:
        if item.get("name") not in WRITE_TOOL_NAMES:
            continue
        schema = item.setdefault("inputSchema", {"type": "object", "properties": {}})
        schema.setdefault("properties", {})["version_check"] = {
            "type": "string", "minLength": 64, "maxLength": 64,
            "description": "Required live deployment token. Call get_server_info immediately before this write and pass write_version_token unchanged. Stale or missing tokens are rejected before any write occurs.",
        }
        required = schema.setdefault("required", [])
        if "version_check" not in required:
            required.append("version_check")
        item["description"] = item.get("description", "") + " VERSION SAFETY: immediately before calling this write, call get_server_info and pass its write_version_token as version_check. The server blocks stale or unchecked writes."
    enrichment = by_name.get("enrich_subject")
    if enrichment:
        enrichment["description"] += (
            " CLASSIFICATION HAND-OFF: after any successful enrichment that changes the subject or its relationships, "
            "immediately call get_subject_classification. If the classification is not confirmed and the enriched "
            "evidence supports the current type and no more precise strict descendant is justified, call "
            "affirm_subject_classification. If a more precise strict-descendant type is supported, call "
            "propose_subject_reclassification instead. Pass this model's stable identity, the reason and the enrichment "
            "evidence. Do not leave classification convergence pending merely because enrichment succeeded."
        )
    contribution = by_name.get("submit_contribution")
    if contribution:
        enum = contribution.get("inputSchema", {}).get("properties", {}).get("contribution_type", {}).setdefault("enum", [])
        if "vote" not in enum:
            enum.append("vote")
        contribution["description"] = "Add an immutable proposal, critique, counterproposal, reconciliation or vote. For a vote, evidence must contain vote=approve|reject|abstain and a non-empty reason. Preserve attribution and disagreement. Votes are advisory and never resolve a deliberation or activate guidance. The server independently checks machine-verifiable acceptance criteria and referenced review IDs. VERSION SAFETY: immediately before calling this write, call get_server_info and pass its write_version_token as version_check."
    create = by_name.get("create_deliberation")
    if create:
        create["description"] += " To propose an induction-guidance change, set context.governance_kind='induction_guidance', context.guidance_key to the stable section key, context.guidance_scope to 'global' or 'model', and context.target_model when scope is model. The proposal remains inactive until explicit user approval."
    resolution = by_name.get("record_resolution")
    if resolution:
        resolution["description"] += " For an induction-guidance deliberation, a successful user-approved resolution becomes active guidance returned by get_induction; AI votes alone have no activation authority."


def _restore_body(request: Request, raw: bytes) -> None:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    request._body = raw
    if hasattr(request, "_json"):
        delattr(request, "_json")
    request._receive = receive


def _record_oauth_connection(request: Request, raw: bytes, mcp_module) -> None:
    try:
        form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        client_id = (form.get("client_id") or [""])[0]
        resource = (form.get("resource") or [""])[0]
        if not client_id or not resource:
            return
        with SessionLocal() as db:
            client = db.get(OAuthClient, client_id)
            code = db.scalar(
                select(OAuthAuthorizationCode)
                .where(OAuthAuthorizationCode.client_id == client_id, OAuthAuthorizationCode.resource == resource)
                .order_by(desc(OAuthAuthorizationCode.created_at))
            )
            if not code:
                return
            prior = db.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.action == "oauth.connected",
                    AuditEvent.client_id == client_id,
                    AuditEvent.actor_id == str(code.user_id),
                    AuditEvent.object_id == resource,
                ).limit(1)
            )
            info = _server_info(mcp_module)
            event = AuditEvent(
                actor_id=str(code.user_id), client_id=client_id, action="oauth.connected",
                object_type="oauth_resource", object_id=resource,
                request_id=getattr(request.state, "request_id", f"oauth_{uuid.uuid4().hex}"),
                details={
                    "connection_kind": "reconnect" if prior else "new",
                    "client_name": client.client_name if client else "MCP client",
                    "resource": resource,
                    "scope": code.scope,
                    "server_version": info["server_version"],
                    "protocol_version": info["protocol_version"],
                    "build_sha": info["build_sha"],
                    "deployment_id": info["deployment_id"],
                    "mcp_endpoint": info["mcp_endpoint"],
                    "user_agent": request.headers.get("user-agent", "")[:240],
                    "ip_stored": False,
                },
            )
            db.add(event)
            db.commit()
    except Exception:
        # Connection auditing must never break OAuth.
        return


def _connection_table(events: list[AuditEvent]) -> HTMLResponse:
    rows = []
    for event in events:
        d = event.details or {}
        created = event.created_at.isoformat(sep=" ", timespec="seconds")
        rows.append(
            "<tr>"
            f"<td>{escape(created)}</td>"
            f"<td>{escape(str(d.get('connection_kind', 'unknown')))}</td>"
            f"<td>{escape(str(d.get('client_name', 'MCP client')))}</td>"
            f"<td>{escape(str(d.get('resource', event.object_id)))}</td>"
            f"<td>{escape(str(d.get('server_version', '')))}</td>"
            f"<td><code>{escape(str(d.get('build_sha', 'unknown')))[:12]}</code></td>"
            f"<td>{escape(str(d.get('user_agent', '')))[:120]}</td>"
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="7">No successful OAuth connections recorded yet.</td></tr>'
    response = HTMLResponse(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>TestGraph connection audit</title><style>body{{font:15px/1.45 system-ui;margin:32px;color:#20231f}}h1{{margin-bottom:6px}}p{{color:#5b625d}}table{{border-collapse:collapse;width:100%;margin-top:22px}}th,td{{border-bottom:1px solid #ddd;padding:9px;text-align:left;vertical-align:top}}th{{background:#f5f5f5}}code{{font-size:12px}}</style></head><body><h1>TestGraph connection audit</h1><p>Successful OAuth authorisations. Raw IP addresses are not stored.</p><table><thead><tr><th>Time</th><th>Type</th><th>Client</th><th>Resource</th><th>Server</th><th>Build</th><th>User agent</th></tr></thead><tbody>{body}</tbody></table></body></html>""")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def install_get_induction_middleware(app, mcp_module) -> None:
    @app.get("/admin/connections", response_class=HTMLResponse, include_in_schema=False)
    async def connections_login():
        response = HTMLResponse("""<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>TestGraph connection audit</title><style>body{font:16px/1.5 system-ui;max-width:560px;margin:8vh auto;padding:0 20px}main{border:1px solid #ddd;border-radius:16px;padding:28px}input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px;border-radius:9px}input{border:1px solid #bbb}button{border:0;background:#20231f;color:white;font-weight:700}</style></head><body><main><h1>Connection audit</h1><p>Enter the TestGraph admin API key.</p><form method="post"><input type="password" name="admin_key" autocomplete="off" required><button type="submit">View connections</button></form></main></body></html>""")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.post("/admin/connections", response_class=HTMLResponse, include_in_schema=False)
    async def connections_page(request: Request):
        form = await request.form()
        supplied = str(form.get("admin_key", ""))
        expected = get_settings().development_api_key
        if not expected or not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "Invalid admin key")
        with SessionLocal() as db:
            events = list(db.scalars(
                select(AuditEvent).where(AuditEvent.action == "oauth.connected")
                .order_by(desc(AuditEvent.created_at)).limit(500)
            ).all())
        return _connection_table(events)

    @app.middleware("http")
    async def governed_induction(request, call_next):
        if request.method == "GET" and request.url.path == "/version":
            return JSONResponse(_server_info(mcp_module))

        # Observe successful OAuth authorisation without modifying OAuth itself.
        if request.method == "POST" and request.url.path == "/oauth/authorize":
            raw = await request.body()
            _restore_body(request, raw)
            response = await call_next(request)
            if response.status_code in {302, 303, 307, 308}:
                _record_oauth_connection(request, raw, mcp_module)
            return response

        if request.method != "POST" or request.url.path != "/mcp-v2":
            return await call_next(request)

        raw = await request.body()
        try:
            body = json.loads(raw or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            body = {}
        params = body.get("params") or {}
        method = body.get("method")
        tool_name = params.get("name")
        if method == "tools/call" and tool_name in WRITE_TOOL_NAMES:
            args = params.get("arguments") or {}
            supplied = args.get("version_check")
            expected = _server_info(mcp_module)["write_version_token"]
            if supplied != expected:
                result = _version_error(mcp_module, supplied=supplied)
                return JSONResponse({"jsonrpc": "2.0", "id": body.get("id"), "result": result})
            clean_body = dict(body)
            clean_params = dict(params)
            clean_args = dict(args)
            clean_args.pop("version_check", None)
            clean_params["arguments"] = clean_args
            clean_body["params"] = clean_params
            raw = json.dumps(clean_body, separators=(",", ":")).encode("utf-8")
        intercepted = method == "tools/call" and tool_name in {
            "get_induction", "get_server_info", "list_reviews_by_visibility", "set_review_visibility"
        }
        if not intercepted:
            _restore_body(request, raw)
            return await call_next(request)
        rpc_id = body.get("id")
        args = params.get("arguments") or {}
        scope = "reviews:write" if tool_name == "set_review_visibility" else "reviews:read"
        try:
            principal = mcp_module._principal(request, scope)
        except TokenError as exc:
            result = mcp_module._auth_error(str(exc))
        else:
            if tool_name == "get_server_info":
                result = mcp_module._result(_server_info(mcp_module))
            elif principal.user_id is None:
                result = mcp_module._error("Authenticated TestGraph user is required")
            elif tool_name == "list_reviews_by_visibility":
                with SessionLocal() as db:
                    result = _list_reviews_by_visibility(db, principal, args)
            elif tool_name == "set_review_visibility":
                with SessionLocal() as db:
                    result = _set_review_visibility(db, principal, args)
            else:
                with SessionLocal() as db:
                    induction = get_induction(db, owner_id=principal.user_id, source_model=args.get("source_model"))
                result = mcp_module._result(induction)
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})
