import base64
import hashlib
import re

from app.core.config import get_settings


def _pkce(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _rpc(client, path, method, params=None, token=None, call_id=1):
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": call_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post(path, headers=headers, json=body)


def test_oauth_mcp_v2_resource_flow(client, auth, monkeypatch):
    user = client.post("/api/v1/users", headers=auth, json={"display_name": "OAuth V2 User"}).json()
    monkeypatch.setenv("OAUTH_OWNER_USER_ID", user["id"])
    monkeypatch.setenv("OAUTH_CONNECTION_CODE", "test-connect-code-v2")
    get_settings.cache_clear()

    base = "http://127.0.0.1:8000"
    resource = f"{base}/mcp-v2"
    metadata = client.get("/.well-known/oauth-protected-resource/mcp-v2")
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == resource

    redirect_uri = "https://claude.example/callback"
    registered = client.post("/oauth/register", json={
        "redirect_uris": [redirect_uri],
        "client_name": "Claude v2 test",
        "token_endpoint_auth_method": "none",
    })
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]

    verifier = "v" * 64
    form = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": "state-v2",
        "code_challenge": _pkce(verifier),
        "code_challenge_method": "S256",
        "resource": resource,
        "scope": "reviews:read reviews:write",
        "connection_secret": "test-connect-code-v2",
    }
    approved = client.post("/oauth/authorize", data=form, follow_redirects=False)
    assert approved.status_code == 303
    code = re.search(r"[?&]code=([^&]+)", approved.headers["location"]).group(1)

    tokens = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "resource": resource,
    })
    assert tokens.status_code == 200
    access = tokens.json()["access_token"]

    initialized = _rpc(client, "/mcp-v2", "initialize", token=access)
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "TasteGraph v2"

    tools = _rpc(client, "/mcp-v2", "tools/list", token=access, call_id=2).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"search", "fetch", "get_concept", "propose_alias", "save_experience", "save_assessment"}

    wrong_resource = _rpc(client, "/mcp", "tools/call", {"name": "search", "arguments": {}}, access, 3).json()["result"]
    assert wrong_resource["isError"] is True
