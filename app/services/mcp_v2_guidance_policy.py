from __future__ import annotations

import json

from fastapi.responses import JSONResponse

from app.core.security import TokenError
from app.db.session import SessionLocal
from app.services.guidance import get_induction


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


def apply_guidance_tool_policy(tools: list[dict]) -> None:
    by_name = {item.get("name"): item for item in tools}
    if "get_induction" not in by_name:
        # Match the read security metadata already used by the other read-only MCP tools.
        read_template = by_name.get("fetch") or by_name.get("search") or {}
        tool = dict(GET_INDUCTION_TOOL)
        if "securitySchemes" in read_template:
            tool["securitySchemes"] = read_template["securitySchemes"]
        if "_meta" in read_template:
            tool["_meta"] = read_template["_meta"]
        tools.insert(0, tool)

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
            "checks machine-verifiable acceptance criteria and referenced review IDs."
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
        if request.method != "POST" or request.url.path != "/mcp-v2":
            return await call_next(request)

        raw = await request.body()
        try:
            body = json.loads(raw or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            body = {}

        params = body.get("params") or {}
        if body.get("method") != "tools/call" or params.get("name") != "get_induction":
            # Reading the body in middleware must not starve the downstream MCP handler.
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
            if principal.user_id is None:
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
