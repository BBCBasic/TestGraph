from __future__ import annotations

import base64
import hashlib
import re

from app.core.config import get_settings


def _pkce(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _rpc(client,path,method,params=None,token=None,call_id=1):
    headers={"Content-Type":"application/json"}
    if token:
        headers["Authorization"]=f"Bearer {token}"
    payload={"jsonrpc":"2.0","id":call_id,"method":method}
    if params is not None:
        payload["params"]=params
    return client.post(path,headers=headers,json=payload)


def test_oauth_mcp_v2_resource_flow(client,auth,monkeypatch):
    user=client.post("/api/v1/users",headers=auth,json={"display_name":"OAuth V2 User"}).json()
    monkeypatch.setenv("OAUTH_OWNER_USER_ID",user["id"]);monkeypatch.setenv("OAUTH_CONNECTION_CODE","test-connect-code-v2");get_settings.cache_clear()
    base="http://127.0.0.1:8000";resource=f"{base}/mcp-v2"
    assert client.get("/.well-known/oauth-protected-resource/mcp-v2").json()["resource"]==resource
    redirect_uri="https://claude.example/callback"
    client_id=client.post("/oauth/register",json={"redirect_uris":[redirect_uri],"client_name":"V3 test","token_endpoint_auth_method":"none"}).json()["client_id"]
    verifier="v"*64
    approved=client.post("/oauth/authorize",data={"response_type":"code","client_id":client_id,"redirect_uri":redirect_uri,"state":"state-v3","code_challenge":_pkce(verifier),"code_challenge_method":"S256","resource":resource,"scope":"reviews:read reviews:write","connection_secret":"test-connect-code-v2"},follow_redirects=False)
    code=re.search(r"[?&]code=([^&]+)",approved.headers["location"]).group(1)
    access=client.post("/oauth/token",data={"grant_type":"authorization_code","client_id":client_id,"code":code,"redirect_uri":redirect_uri,"code_verifier":verifier,"resource":resource}).json()["access_token"]
    initialized=_rpc(client,"/mcp-v2","initialize",token=access)
    assert initialized.json()["result"]["serverInfo"]["version"]=="3.19.0-alpha"
    instructions=initialized.json()["result"]["instructions"]
    assert "full available reasoning, web retrieval and tool capabilities" in instructions
    assert "open-ended semantic and discovery engine" in instructions
    assert "server-side verification" in instructions
    assert "only record_resolution with explicit user approval may close it" in instructions
    assert "list_open_deliberations" in instructions
    assert "next_cursor" in instructions
    tools=_rpc(client,"/mcp-v2","tools/list",token=access,call_id=2).json()["result"]["tools"]
    assert {tool["name"] for tool in tools}=={"get_server_info","get_induction","search","fetch","vocabulary_index","resolve_subject_type","resolve_subject","resolve_subject_hierarchy","register_subject_type_alias","set_type_relationship","retire_type_relationship","register_field","enrich_subject","correct_subject_fact","save_experience","delete_experience","save_assessment","create_deliberation","get_deliberation","list_open_deliberations","claim_deliberation","submit_contribution","record_resolution","assert_location","get_location_assertions","resolve_location_assertion"}
    save_tool=next(tool for tool in tools if tool["name"]=="save_experience")
    properties=save_tool["inputSchema"]["properties"]
    assert "experienced_at" in properties
    assert "subject_context" in properties
    assert "subject_enrichment_check" in properties
    assert "subject_enrichment_check" in save_tool["inputSchema"]["required"]
