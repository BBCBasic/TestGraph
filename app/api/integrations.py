from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.api.account import router as account_router

router = APIRouter()
router.include_router(account_router)


def _base() -> str:
    return get_settings().public_base_url.rstrip("/")


@router.get("/integrations", include_in_schema=True)
def integrations():
    base = _base()
    return {
        "service": "TestGraph",
        "integration_model": "remote_mcp_with_oauth",
        "mcp": {
            "url": f"{base}/mcp-v2",
            "transport": "Streamable HTTP",
            "authentication": "OAuth 2.0 Authorization Code + PKCE",
            "protected_resource_metadata": f"{base}/.well-known/oauth-protected-resource/mcp-v2",
            "authorization_server_metadata": f"{base}/.well-known/oauth-authorization-server",
            "scopes": ["reviews:read", "reviews:write"],
        },
        "account": {
            "url": f"{base}/account",
            "google_login": get_settings().google_login_enabled,
            "notes": "Google login is optional and recovers a persistent TestGraph identity. Standalone tg_ capability creation remains supported.",
        },
        "clients": {
            "chatgpt": {"type": "MCP app/custom app", "endpoint": f"{base}/mcp-v2"},
            "claude": {"type": "remote MCP connector", "endpoint": f"{base}/mcp-v2"},
            "generic_mcp": {"type": "remote MCP server", "endpoint": f"{base}/mcp-v2"},
        },
        "account_connection": {
            "method": "A tg_ capability URL/key is entered on the TestGraph OAuth consent page. Google login can be used separately to recover the same persistent TestGraph identity and create additional capability keys.",
            "privacy": "Raw capability keys are shown once and stored only as hashes. The MCP client receives OAuth tokens, not the capability key.",
        },
        "fallbacks": {"capability_url": f"{base}/capability/new", "openapi": f"{base}/openapi.json", "schemas": f"{base}/schemas"},
    }
