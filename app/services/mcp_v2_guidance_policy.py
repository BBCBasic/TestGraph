from __future__ import annotations

import hashlib
import json
import os

from fastapi.responses import JSONResponse

from app.core.security import TokenError
from app.db.session import SessionLocal
from app.services.guidance import get_induction


WRITE_TOOL_NAMES = {
    "resolve_subject_hierarchy",
    "register_subject_type_alias",
    "set_type_relationship",
    "retire_type_relationship",
    "register_field",
    "enrich_subject",
    "correct_subject_fact",
    "save_experience",
    "delete_experience",
    "save_assessment",
    "create_deliberation",
    "claim_deliberation",
    "submit_contribution",
    "record_resolution",
    "assert_location",
    "resolve_location_assertion",
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
        "type": "object",
        "properties": {
            "source_model": {
                "type": "string",
                "maxLength": 160,
                "description": "Optional current model label. gpt and chatgpt are treated as aliases.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
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
    "inputSchema": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}


def _server_info(mcp_module) -> dict:
    base = mcp_module._base()
    build_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
    deployment_id = os.getenv("RAILWAY_DEPLOYMENT_ID") or "unknown"
    endpoint = f"{base}/mcp-v2"
    token_material = "|".join(
        [mcp_module.SERVER_VERSION, mcp_module.PROTOCOL_VERSION, build_sha, deployment_id, endpoint]
    )
    write_version_token = hashlib.sha256(token_material.encode("utf-8")).hexdigest()
    return {
        "service": "TestGraph",
        "api_version": "v2",
        "server_version": mcp_module.SERVER_VERSION,
        "protocol_version": mcp_module.PROTOCOL_VERSION,
        "build_sha": build_sha,
        "deployment_id": deployment_id,
        "service_id": os.getenv("RAILWAY_SERVICE_ID") or "unknown",
        "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT") or "unknown",
        "mcp_endpoint": endpoint,
        "version_endpoint": f"{base}/version",
        "write_version_token": write_version_token,
        "write_requirement": (
            "Call get_server_info immediately before every write and pass write_version_token as version_check."
        ),
    }


def _version_error(mcp_module, *, supplied=None):
    current = _server_info(mcp_module)
    payload = {
        "error": "TestGraph connection is out of date. Refresh or reconnect V2 before writing.",
        "error_code": "MCP_VERSION_CHECK_REQUIRED",
        "user_message": "TestGraph connection is out of date. Refresh or reconnect V2 before writing.",
        "write_blocked": True,
        "no_write_performed": True,
        "action_required": (
            "Call get_server_info on the current V2 connection immediately before the write, then retry using "
            "its write_version_token as version_check. If get_server_info is not visible, refresh or reconnect the MCP."
        ),
        "current_server": {
            "server_version": current["server_version"],
            "protocol_version": current["protocol_version"],
            "build_sha": current["build_sha"],
            "deployment_id": current["deployment_id"],
            "mcp_endpoint": current["mcp_endpoint"],
        },
    }
    if supplied:
        payload["supplied_version_check"] = supplied
    return {**mcp_module._result(payload), "isError": True}


def apply_guidance_tool_policy(tools: list[dict]) -> None:
    by_name = {item.get("name"): item for item in tools}
    read_template = by_name.get("fetch") or by_name.get("search") or {}

    if "get_server_info" not in by_name:
        tool = dict(GET_SERVER_INFO_TOOL)
        if "securitySchemes" in read_template:
            tool["securitySchemes"] = read_template["securitySchemes"]
        if "_meta" in read_template:
            tool["_meta"] = read_template["_meta"]
        tools.insert(0, tool)

    if "get_induction" not in by_name:
        tool = dict(GET_INDUCTION_TOOL)
        if "securitySchemes" in read_template:
            tool["securitySchemes"] = read_template["securitySchemes"]
        if "_meta" in read_template:
            tool["_meta"] = read_template["_meta"]
        tools.insert(0, tool)

    # Every write schema advertises the live-version precondition. The middleware below
    # enforces it as well, so even a stale client with an old schema cannot bypass it.
    for item in tools:
        if item.get("name") not in WRITE_TOOL_NAMES:
            continue
        schema = item.setdefault("inputSchema", {"type": "object", "properties": {}})
        properties = schema.setdefault("properties", {})
        properties["version_check"] = {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "description": (
                "Required live deployment token. Call get_server_info immediately before this write and pass "
                "write_version_token unchanged. Stale or missing tokens are rejected before any write occurs."
            ),
        }
        required = schema.setdefault("required", [])
        if "version_check" not in required:
            required.append("version_check")
        item["description"] = (
            item.get("description", "")
            + " VERSION SAFETY: immediately before calling this write, call get_server_info and pass its "
              "write_version_token as version_check. The server blocks stale or unchecked writes."
        )

    contribution = by_name.get("submit_contribution")
    if contribution:
        properties = contribution.get("inputSchema", {}).get("properties", {})
        contribution_type = properties.get("contribution_type", {})
        enum = contribution_type.setdefault("enum", [])
        if "vote" not in enum:
            enum.append("vote")
        contribution["description"] = (
            "Add an immutable proposal, critique, counterproposal, reconciliation or vote. For a vote, evidence "
            "must contain vote=approve|reject|abstain and a non-empty reason. Preserve attribution and disagreement. "
            "Votes are advisory and never resolve a deliberation or activate guidance. The server independently "
            "checks machine-verifiable acceptance criteria and referenced review IDs. VERSION SAFETY: immediately "
            "before calling this write, call get_server_info and pass its write_version_token as version_check."
        )

    create = by_name.get("create_deliberation")
    if create:
        create["description"] = (
            create.get("description", "")
            + " To propose an induction-guidance change, set context.governance_kind='induction_guidance', "
              "context.guidance_key to the stable section key, context.guidance_scope to 'global' or 'model', "
              "and context.target_model when scope is model. The proposal remains inactive until explicit user approval."
        )

    resolution = by_name.get("record_resolution")
    if resolution:
        resolution["description"] = (
            resolution.get("description", "")
            + " For an induction-guidance deliberation, a successful user-approved resolution becomes active "
              "guidance returned by get_induction; AI votes alone have no activation authority."
        )


def install_get_induction_middleware(app, mcp_module) -> None:
    @app.middleware("http")
    async def governed_induction(request, call_next):
        if request.method == "GET" and request.url.path == "/version":
            return JSONResponse(_server_info(mcp_module))

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

        # Enforce a fresh deployment check before any write reaches the normal MCP handler.
        if method == "tools/call" and tool_name in WRITE_TOOL_NAMES:
            args = params.get("arguments") or {}
            supplied = args.get("version_check")
            expected = _server_info(mcp_module)["write_version_token"]
            if supplied != expected:
                result = _version_error(mcp_module, supplied=supplied)
                return JSONResponse({"jsonrpc": "2.0", "id": body.get("id"), "result": result})

            # version_check is a transport safety precondition, not a domain argument.
            clean_body = dict(body)
            clean_params = dict(params)
            clean_args = dict(args)
            clean_args.pop("version_check", None)
            clean_params["arguments"] = clean_args
            clean_body["params"] = clean_params
            raw = json.dumps(clean_body, separators=(",", ":")).encode("utf-8")

        intercepted = method == "tools/call" and tool_name in {"get_induction", "get_server_info"}
        if not intercepted:
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": raw, "more_body": False}

            request._receive = receive
            return await call_next(request)

        rpc_id = body.get("id")
        args = params.get("arguments") or {}
        try:
            principal = mcp_module._principal(request, "reviews:read")
        except TokenError as exc:
            result = mcp_module._auth_error(str(exc))
        else:
            if tool_name == "get_server_info":
                result = mcp_module._result(_server_info(mcp_module))
            elif principal.user_id is None:
                result = mcp_module._error("Authenticated TestGraph user is required")
            else:
                with SessionLocal() as db:
                    induction = get_induction(
                        db,
                        owner_id=principal.user_id,
                        source_model=args.get("source_model"),
                    )
                result = mcp_module._result(induction)
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})