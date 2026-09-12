from app.db.session import SessionLocal
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
