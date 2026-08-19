import base64,hashlib,re
from app.core.config import get_settings

def _pkce(verifier):return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
def _rpc(client,path,method,params=None,token=None,call_id=1):
    headers={"Accept":"application/json, text/event-stream"}
    if token:headers["Authorization"]=f"Bearer {token}"
    body={"jsonrpc":"2.0","id":call_id,"method":method}
    if params is not None:body["params"]=params
    return client.post(path,headers=headers,json=body)

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
    assert initialized.json()["result"]["serverInfo"]["version"]=="3.12.0-alpha"
    instructions=initialized.json()["result"]["instructions"]
    assert "full available reasoning, web retrieval and tool capabilities" in instructions
    assert "open-ended semantic and discovery engine" in instructions
    assert "server-side verification" in instructions
    tools=_rpc(client,"/mcp-v2","tools/list",token=access,call_id=2).json()["result"]["tools"]
    assert {tool["name"] for tool in tools}=={"search","fetch","vocabulary_index","resolve_subject_type","resolve_subject","resolve_subject_hierarchy","register_subject_type_alias","set_type_relationship","retire_type_relationship","register_field","enrich_subject","correct_subject_fact","save_experience","delete_experience","save_assessment"}
    save_tool=next(tool for tool in tools if tool["name"]=="save_experience")
    properties=save_tool["inputSchema"]["properties"]
    assert "experienced_at" in properties
    assert "subject_context" in properties
    assert "subject_enrichment_check" in properties
    assert "subject_enrichment_check" in save_tool["inputSchema"]["required"]
    retrieval_uses=properties["subject_enrichment_check"]["properties"]["retrieval_uses"]
    assert "roles" in retrieval_uses["additionalProperties"]["required"]
    assert "reason" in retrieval_uses["additionalProperties"]["required"]
    assert "collection_assessment" in properties
    assert "collection_assessment" in save_tool["inputSchema"]["required"]
    assert "deferred" not in properties["collection_assessment"]["properties"]["status"]["enum"]
    assert "discovered_count" in properties["collection_assessment"]["properties"]
    assert "submitted_member_refs" in properties["collection_assessment"]["properties"]
    assert "collection_id" in properties["collection_assessment"]["properties"]
    assert "manifest_revision" in properties["collection_assessment"]["properties"]
    unavailability_kind=properties["collection_assessment"]["properties"]["unavailability_kind"]
    assert set(unavailability_kind["enum"])=={
        "collection_identity_not_found",
        "authoritative_source_not_found",
        "authoritative_source_inaccessible",
    }
    assert "realistically search for later" in save_tool["description"]
    assert "open-ended semantic and discovery engine" in save_tool["description"]
    source_manifest=properties["collection_assessment"]["properties"]["source_manifest"]
    assert "source_pages" in source_manifest["properties"]
    assert "discovery_queries" in source_manifest["required"]
    assert "exhaustion_evidence" in source_manifest["required"]
    delete_tool=next(tool for tool in tools if tool["name"]=="delete_experience")
    assert delete_tool["annotations"]["destructiveHint"] is True
    assert "confirm_deletion" in delete_tool["inputSchema"]["required"]
    enrich_tool=next(tool for tool in tools if tool["name"]=="enrich_subject")
    enrich_schema=enrich_tool["inputSchema"]
    assert "subject_context" in enrich_schema["properties"]
    assert "subject_id" in enrich_schema["properties"]
    assert "subject_enrichment_check" in enrich_schema["required"]
    assert "collection_assessment" in enrich_schema["required"]
    assert "open-ended semantic and discovery engine" in enrich_tool["description"]
    assert {"required":["subject_id"]} in enrich_schema["anyOf"]
    correction_tool=next(tool for tool in tools if tool["name"]=="correct_subject_fact")
    assert correction_tool["annotations"]["destructiveHint"] is True
    assert "expected_value" in correction_tool["inputSchema"]["required"]
