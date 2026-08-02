def create_user(client,auth,name,weights):
    r=client.post("/api/v1/users",headers=auth,json={"display_name":name,"profile_data":{"dimension_importance":weights}});assert r.status_code==200;return r.json()
def create_subject(client,auth,key="dishoom-test"):
    r=client.post("/api/v1/subjects",headers=auth,json={"subject_type":"restaurant","name":"Dishoom Permit Room Portobello","canonical_key":key,"metadata_json":{"city":"London"}});assert r.status_code==200;return r.json()

def test_health_and_discovery(client):
    assert client.get("/health/live").status_code==200
    assert client.get("/health/ready").json()["database"]=="ok"
    assert client.get("/.well-known/review-service.json").status_code==200
    assert client.get("/openapi.json").status_code==200

def test_auth_required(client):
    r=client.post("/api/v1/users",json={"display_name":"X"})
    assert r.status_code==401

def test_draft_idempotency_publish_and_personalisation(client,auth):
    robert=create_user(client,auth,"Robert Test",{"noise":0.9,"food":0.9,"service":0.8})
    fred=create_user(client,auth,"Fred Test",{"noise":0.15,"food":0.95,"service":0.75})
    subject=create_subject(client,auth)
    client.put("/api/v1/alignments",headers=auth,json={"source_user_id":robert["id"],"target_user_id":fred["id"],"dimensions":{"noise":0.2,"food":0.8,"service":0.7}})
    payload={"owner_id":robert["id"],"subject_id":subject["id"],"subject_type":"restaurant","schema_version":"1.0","visibility":"private","headline":"Too noisy and disappointing","summary":"A difficult room with dull daal.","common_data":{"observations":[{"category":"service","statement":"Asked for water three times.","confidence":0.95}],"subjective_impressions":[{"category":"noise","statement":"Too noisy.","sentiment":-0.9,"importance_to_reviewer":0.9},{"category":"food","statement":"Daal was dull.","sentiment":-0.7,"importance_to_reviewer":0.9}],"weaknesses":["noise","service"]},"domain_data":{"food":4,"service":3,"atmosphere":2,"noise":2,"meal_pacing":4},"provenance":{"source_method":"llm_conversation","source_client":"chatgpt"},"consent":{"user_approved":False}}
    h={**auth,"Idempotency-Key":"same-review-1"}
    r1=client.post("/api/v1/experiences/drafts",headers=h,json=payload);assert r1.status_code==201
    r2=client.post("/api/v1/experiences/drafts",headers=h,json=payload);assert r2.status_code in (200,201);assert r1.json()["id"]==r2.json()["id"]
    exp=r1.json()
    p=client.post(f"/api/v1/experiences/{exp['id']}/publish",headers=auth,json={"user_approved":True,"approved_version":1});assert p.status_code==200;assert p.json()["publication_status"]=="published"
    pr=client.get(f"/api/v1/experiences/{exp['id']}/for/{fred['id']}");assert pr.status_code==200
    dims={x["dimension"]:x for x in pr.json()["dimensions"]}
    assert dims["food"]["relevance"]>dims["noise"]["relevance"]

def test_unknown_domain_field_rejected(client,auth):
    u=create_user(client,auth,"Schema User",{})
    s=create_subject(client,auth,"dishoom-schema-test")
    payload={"owner_id":u["id"],"subject_id":s["id"],"subject_type":"restaurant","schema_version":"1.0","headline":"x","summary":"x","common_data":{},"domain_data":{"food":5,"invented_field":2},"provenance":{"source_method":"test"}}
    r=client.post("/api/v1/experiences/drafts",headers=auth,json=payload)
    assert r.status_code==400
