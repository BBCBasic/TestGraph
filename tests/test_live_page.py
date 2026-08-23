from app.db.session import SessionLocal
from app.models.entities import User
from app.schemas.v2 import AssessmentCreate, ExperienceCreate, SubjectEnsure
from app.services.v2 import create_assessment, create_experience, ensure_subject, ensure_subject_type


def test_live_page_is_v2_only_and_linked(client):
    home = client.get("/")
    page = client.get("/live")

    assert home.status_code == 200
    assert 'href="/live"' in home.text
    assert page.status_code == 200
    assert "Live TestGraph" in page.text
    assert "/api/v2/vocabulary" in page.text
    assert "/api/v2/public/experiences" in page.text
    assert "/api/v1/" not in page.text


def test_public_v2_feed_excludes_private_data_and_internal_metadata(client):
    with SessionLocal() as db:
        user = User(display_name="Live page test", profile_data={})
        db.add(user); db.commit(); db.refresh(user)
        ensure_subject_type(db, "live-test-place", created_by="internal-client-secret")
        subject = ensure_subject(db, SubjectEnsure(
            subject_type="live-test-place", name="Public example", canonical_key="public-example"
        ))
        public = create_experience(db, ExperienceCreate(
            owner_id=user.id, subject_id=subject.id, headline="Visible headline",
            summary="Visible summary", raw_text="Visible original words.",
            visibility="public", user_approved=True,
        ), "internal-client-secret")
        create_experience(db, ExperienceCreate(
            owner_id=user.id, subject_id=subject.id, headline="Private headline",
            summary="Private summary", raw_text="Private original words.",
            visibility="private", user_approved=True,
        ), "internal-client-secret")
        create_assessment(db, AssessmentCreate(
            experience_id=public.id, assessment_type="quality",
            conclusion="Supported conclusion", confidence=.8, source_model="test-model",
            provenance={"private_trace": "must-not-leak"},
        ), client_id="internal-client-secret", user_id=user.id)

    response = client.get("/api/v2/public/experiences?limit=100")
    assert response.status_code == 200
    payload = response.json()
    rendered = response.text
    assert payload["counts"]["experiences"] >= 1
    assert any(item["headline"] == "Visible headline" for item in payload["experiences"])
    assert "Private headline" not in rendered
    assert "owner_id" not in rendered
    assert "created_by_client" not in rendered
    assert "private_trace" not in rendered
    assert "internal-client-secret" not in rendered
