import uuid


def create_user(client, auth, name="Robert", weights=None):
    response = client.post("/api/v1/users", headers=auth, json={"display_name": name, "profile_data": {"dimension_importance": weights or {}}})
    assert response.status_code == 200
    return response.json()


def create_subject(client, auth, subject_type="recipe"):
    key = f"{subject_type}-{uuid.uuid4()}"
    response = client.post("/api/v1/subjects", headers=auth, json={"subject_type": subject_type, "name": "Test subject", "canonical_key": key})
    assert response.status_code == 200
    return response.json()


def create_draft(client, auth, visibility="private", subject_type="recipe"):
    user = create_user(client, auth, weights={"flavour": 0.8})
    subject = create_subject(client, auth, subject_type)
    domain_data = {"flavour": 9} if subject_type == "recipe" else {
        "overall_rating": 8.5, "food": 9, "noise_comfort": 7, "visit_date": "2026-08-02",
        "meal_type": "dinner", "party_size": 2, "wait_minutes": 5,
        "spend_per_person": 52.5, "currency": "GBP",
        "dishes": [{"name": "Black daal", "rating": 9, "shared": True, "would_order_again": True}],
    }
    response = client.post("/api/v1/experiences/drafts", headers=auth, json={
        "owner_id": user["id"], "subject_id": subject["id"], "subject_type": subject_type,
        "schema_version": "1.0", "visibility": visibility, "headline": "Test review", "summary": "Useful detail",
        "common_data": {"subjective_impressions": [{"category": "flavour", "statement": "Excellent", "sentiment": 0.9, "importance_to_reviewer": 0.9}]},
        "domain_data": domain_data, "provenance": {"source_method": "test"},
    })
    assert response.status_code == 201
    return response.json(), user, subject


def publish(client, auth, draft):
    response = client.post(f"/api/v1/experiences/{draft['id']}/publish", headers=auth, json={"user_approved": True, "approved_version": draft["version"]})
    assert response.status_code == 200
    return response.json()


def test_public_list_never_exposes_drafts_private_unlisted_or_aggregate(client, auth):
    draft, _, _ = create_draft(client, auth, "public")
    private, _, _ = create_draft(client, auth, "private"); publish(client, auth, private)
    unlisted, _, _ = create_draft(client, auth, "unlisted"); publish(client, auth, unlisted)
    aggregate, _, _ = create_draft(client, auth, "aggregate_only"); publish(client, auth, aggregate)
    public, _, _ = create_draft(client, auth, "public"); public = publish(client, auth, public)
    ids = {row["id"] for row in client.get("/api/v1/experiences").json()}
    assert public["id"] in ids
    assert not {draft["id"], private["id"], unlisted["id"], aggregate["id"]} & ids


def test_exact_read_policy(client, auth):
    draft, _, _ = create_draft(client, auth, "public")
    assert client.get(f"/api/v1/experiences/{draft['id']}").status_code == 404
    assert client.get(f"/api/v1/experiences/{draft['id']}", headers=auth).status_code == 200
    unlisted, _, _ = create_draft(client, auth, "unlisted"); unlisted = publish(client, auth, unlisted)
    assert client.get(f"/api/v1/experiences/{unlisted['id']}").status_code == 200
    aggregate, _, _ = create_draft(client, auth, "aggregate_only"); aggregate = publish(client, auth, aggregate)
    assert client.get(f"/api/v1/experiences/{aggregate['id']}", headers=auth).status_code == 404


def test_public_user_omits_profile_data_and_personalisation_requires_auth(client, auth):
    draft, user, _ = create_draft(client, auth, "public"); published = publish(client, auth, draft)
    public_user = client.get(f"/api/v1/users/{user['id']}")
    assert public_user.status_code == 200
    assert "profile_data" not in public_user.json()
    url = f"/api/v1/experiences/{published['id']}/for/{user['id']}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=auth).status_code == 200


def test_canonical_resolution(client, auth):
    subject = create_subject(client, auth)
    response = client.get("/api/v1/subjects/resolve", params={"subject_type": subject["subject_type"], "canonical_key": subject["canonical_key"]})
    assert response.status_code == 200
    assert response.json()["id"] == subject["id"]
    assert client.get("/api/v1/subjects/resolve", params={"subject_type": "recipe", "canonical_key": "missing"}).status_code == 404


def test_patch_draft_uses_version_and_cannot_edit_published(client, auth):
    draft, _, _ = create_draft(client, auth)
    response = client.patch(f"/api/v1/experiences/{draft['id']}", headers=auth, json={"expected_version": 1, "headline": "Corrected review"})
    assert response.status_code == 200
    edited = response.json()
    assert edited["headline"] == "Corrected review" and edited["version"] == 2
    assert client.patch(f"/api/v1/experiences/{draft['id']}", headers=auth, json={"expected_version": 1, "summary": "stale"}).status_code == 409
    published = publish(client, auth, edited)
    assert client.patch(f"/api/v1/experiences/{published['id']}", headers=auth, json={"expected_version": 2, "summary": "late"}).status_code == 409


def test_client_attribution_is_credential_derived(client, auth):
    draft, _, _ = create_draft(client, auth)
    assert draft["created_by_client"] == "development-client"


def test_restaurant_schema_and_validation(client, auth):
    draft, _, _ = create_draft(client, auth, subject_type="restaurant")
    assert draft["domain_data"]["dishes"][0]["would_order_again"] is True
    user = create_user(client, auth); subject = create_subject(client, auth, "restaurant")
    base = {"owner_id": user["id"], "subject_id": subject["id"], "subject_type": "restaurant", "schema_version": "1.0", "headline": "x", "summary": "x", "common_data": {}, "provenance": {"source_method": "test"}}
    assert client.post("/api/v1/experiences/drafts", headers=auth, json={**base, "domain_data": {"party_size": 0}}).status_code == 400
    assert client.post("/api/v1/experiences/drafts", headers=auth, json={**base, "domain_data": {"currency": "gbp"}}).status_code == 400


def test_openapi_has_one_credential_scheme_and_protected_routes(client):
    document = client.get("/openapi.json").json()
    assert set(document["components"]["securitySchemes"]) == {"ApiKey"}
    assert "publication_status" not in {p["name"] for p in document["paths"]["/api/v1/experiences"]["get"]["parameters"]}
    assert document["paths"]["/api/v1/experiences/{experience_id}/for/{reader_id}"]["get"]["security"]
    assert document["paths"]["/api/v1/experiences/{experience_id}"]["patch"]["security"]
