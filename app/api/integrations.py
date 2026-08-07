from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter()


def _base() -> str:
    return get_settings().public_base_url.rstrip("/")


@router.get("/integrations", include_in_schema=True)
def integrations():
    base = _base()
    return {
        "service": "TasteGraph",
        "integration_model": "remote_mcp_with_oauth",
        "mcp": {
            "url": f"{base}/mcp",
            "transport": "Streamable HTTP",
            "authentication": "OAuth 2.0 Authorization Code + PKCE",
            "protected_resource_metadata": f"{base}/.well-known/oauth-protected-resource",
            "authorization_server_metadata": f"{base}/.well-known/oauth-authorization-server",
            "scopes": ["reviews:read", "reviews:write"],
            "tools": ["search", "fetch", "get_schema", "save_review"],
        },
        "clients": {
            "chatgpt": {
                "type": "MCP app/custom app",
                "endpoint": f"{base}/mcp",
                "notes": "Full write actions depend on the ChatGPT plan/workspace features available to the user. Read/search can be exposed independently of write permissions.",
            },
            "claude": {
                "type": "remote MCP connector",
                "endpoint": f"{base}/mcp",
                "notes": "Use the same OAuth-protected MCP endpoint and tools.",
            },
            "generic_mcp": {
                "type": "remote MCP server",
                "endpoint": f"{base}/mcp",
            },
        },
        "account_connection": {
            "method": "TasteGraph capability URL/key entered on the TasteGraph OAuth consent page",
            "privacy": "The MCP client receives OAuth tokens; the private TasteGraph capability key is not returned to the client.",
        },
        "fallbacks": {
            "capability_url": f"{base}/capability/new",
            "openapi": f"{base}/openapi.json",
            "schemas": f"{base}/schemas",
        },
    }
