import base64
import hashlib
import json
import re
from app.core.config import get_settings

def _pkce(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

def _rpc(client, method, params=None, token=None, call_id=1):
    headers={"Accept":"application/json, text/event-stream"}
    if token: headers["Authorization"]=f"Bearer {token}"
    body={"jsonrpc":"2.0","id":call_id,"method":method}
    if params is not None: body["params"]=params
    return client.post("/mcp",headers=headers,json=body)

def test_oauth_and_mcp_save_search_fetch(client,auth,monkeypatch):
    user=client.post("/api/v1/users",headers=auth,json={"display_name":"OAuth Test User"}).json()
    monkeypatch.setenv("OAUTH_OWNER_USER_ID",user["id"])
    monkeypatch.setenv("OAUTH_CONNECTION_CODE","test-connect-code")
    get_settings.cache_clear()
    redirect_uri="https://chatgpt.example/callback"
    registered=client.post("/oauth/register",json={"redirect_uris":[redirect_uri],"client_name":"ChatGPT test","token_endpoint_auth_method":"none"})
    assert registered.status_code==201
    client_id=registered.json()["client_id"]
    verifier="v"*64; challenge=_pkce(verifier); resource="http://127.0.0.1:8000/mcp"
    form={"response_type":"code","client_id":client_id,"redirect_uri":redirect_uri,"state":"state-1",
          "code_challenge":challenge,"code_challenge_method":"S256","resource":resource,
          "scope":"reviews:read reviews:write","connection_secret":"test-connect-code"}
    approved=client.post("/oauth/authorize",data=form,follow_redirects=False)
    assert approved.status_code==303
    code=re.search(r"[?&]code=([^&]+)",approved.headers["location"]).group(1)
    tokens=client.post("/oauth/token",data={"grant_type":"authorization_code","client_id":client_id,
        "code":code,"redirect_uri":redirect_uri,"code_verifier":verifier,"resource":resource})
    assert tokens.status_code==200
    access=tokens.json()["access_token"]
    assert _rpc(client,"initialize").json()["result"]["serverInfo"]["name"]=="TasteGraph"
    tools=_rpc(client,"tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools}=={"search","fetch","get_schema","save_review"}
    unauth=_rpc(client,"tools/call",{"name":"search","arguments":{"query":"Example"}}).json()["result"]
    assert unauth["isError"] and "mcp/www_authenticate" in unauth["_meta"]
    review={"subject_type":"restaurant","subject_name":"Example Bistro","canonical_key":"example-bistro-test",
      "subject_metadata":{"city":"Testville","country":"GB"},"headline":"Excellent lunch and friendly service",
      "summary":"The food was excellent, the service was friendly, and the visit felt good value.",
      "common_data":{"observations":[{"category":"service","statement":"The server checked in after the main course arrived.","confidence":1.0}],
        "subjective_impressions":[{"category":"food","statement":"The main course was excellent.","sentiment":0.95,"importance_to_reviewer":0.95}],
        "strengths":["food","service","value"],"weaknesses":[],"would_repeat":True},
      "domain_data":{"food":9.5,"service":8.5,"atmosphere":8.0,"value":9.0},
      "visibility":"private","user_approved":True,"idempotency_key":"example-bistro-oauth-test-review"}
    saved=_rpc(client,"tools/call",{"name":"save_review","arguments":review},access,2).json()["result"]
    assert saved["structuredContent"]["publication_status"]=="published"
    again=_rpc(client,"tools/call",{"name":"save_review","arguments":review},access,3).json()["result"]
    assert again["structuredContent"]["experience_id"]==saved["structuredContent"]["experience_id"]
    searched=_rpc(client,"tools/call",{"name":"search","arguments":{"query":"Example"}},access,4).json()["result"]
    results=json.loads(searched["content"][0]["text"])["results"]
    assert len(results)==1
    fetched=_rpc(client,"tools/call",{"name":"fetch","arguments":{"id":results[0]["id"]}},access,5).json()["result"]
    assert json.loads(fetched["content"][0]["text"])["metadata"]["subject"]["name"]=="Example Bistro"
