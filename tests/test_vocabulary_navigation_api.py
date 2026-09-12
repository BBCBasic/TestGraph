from app.db.session import SessionLocal
from app.core.config import get_settings
from app.services.semantic import resolve_subject_hierarchy


def test_rest_navigation_discovers_children_and_deep_path(client):
    with SessionLocal() as db:
        hierarchy = resolve_subject_hierarchy(
            db,
            ["api navigation root", "api navigation branch", "api navigation leaf"],
            created_by="pytest",
        )
        root_id = hierarchy["path"][0]["id"]
        branch_id = hierarchy["path"][1]["id"]
        leaf_id = hierarchy["path"][2]["id"]

    roots = client.get("/api/v2/subject-types/roots", params={"limit": 100})
    assert roots.status_code == 200
    assert root_id in {item["id"] for item in roots.json()["items"]}

    children = client.get(f"/api/v2/subject-types/{root_id}/children")
    assert children.status_code == 200
    assert [item["id"] for item in children.json()["items"]] == [branch_id]

    path = client.get(f"/api/v2/subject-types/{leaf_id}/path")
    assert path.status_code == 200
    assert [[item["canonical_name"] for item in item_path] for item_path in path.json()["paths"]] == [
        ["api navigation root", "api navigation branch", "api navigation leaf"]
    ]


def test_rest_navigation_returns_not_found_for_unknown_type_id(client):
    response = client.get("/api/v2/subject-types/00000000-0000-0000-0000-000000000000/children")
    assert response.status_code == 404


def test_rest_typed_navigation_selects_relationship_and_reports_mode(client, monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_MODE", "typed")
    get_settings.cache_clear()
    try:
        with SessionLocal() as db:
            hierarchy = resolve_subject_hierarchy(
                db,
                ["typed api root", "typed api leaf"],
                created_by="pytest",
            )
            root_id = hierarchy["path"][0]["id"]
            leaf_id = hierarchy["path"][1]["id"]

        children = client.get(
            f"/api/v2/subject-types/{root_id}/children",
            params={"relationship": "is_a"},
        )
        path = client.get(
            f"/api/v2/subject-types/{leaf_id}/path",
            params={"relationship": "is_a"},
        )

        assert children.status_code == 200
        assert children.json()["classification_mode"] == "typed"
        assert children.json()["relationship"] == "is_a"
        assert [item["id"] for item in children.json()["items"]] == [leaf_id]
        assert path.status_code == 200
        assert path.json()["relationship"] == "is_a"
    finally:
        get_settings.cache_clear()


def test_rest_typed_navigation_rejects_legacy_relationship(client, monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_MODE", "typed")
    get_settings.cache_clear()
    try:
        response = client.get(
            "/api/v2/subject-types/roots",
            params={"relationship": "belongs_to"},
        )
        assert response.status_code == 422
        assert "choose 'is_a' or 'part_of'" in response.json()["detail"]
    finally:
        get_settings.cache_clear()
