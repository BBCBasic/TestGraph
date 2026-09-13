import uuid
from datetime import datetime, timezone

import pytest

from app.core.security import Principal
from app.db.session import SessionLocal
from app.models.entities import User
from app.schemas.v2 import AssessmentCreate, ExperienceCreate, SubjectEnsure
from app.services.mcp_v2_guidance_policy import _set_review_visibility
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
    assert "Classification" in page.text
    assert "Zoomable bubble chart" in page.text
    assert 'id="zoom-in"' in page.text
    assert "classificationPath" in page.text
    assert 'id="classification-tree"' in page.text
    assert "Classification hierarchy" in page.text
    assert "renderClassificationTree" in page.text
    assert "tree-node" in page.text
    assert "tree-toggle" in page.text
    assert "aria-expanded" in page.text
    assert "Expand " in page.text
    assert "Collapse " in page.text
    assert "subject-summary" in page.text


def test_live_page_distinguishes_typed_taxonomy_and_membership(client):
    page = client.get("/live")

    assert page.status_code == 200
    assert 'id="classification-context"' in page.text
    assert "taxonomy_relationship" in page.text
    assert "part_of" in page.text
    assert "Part of" in page.text
    assert "dataset.typeId" in page.text
    assert "same type" in page.text


def test_live_hierarchy_visually_promotes_children_of_the_synthetic_root(client):
    page = client.get("/live")

    assert page.status_code == 200
    assert "syntheticRoot" in page.text
    assert "is_synthetic" in page.text
    assert "synthetic universal root" in page.text
    assert "childList.hidden=!expanded" in page.text


def _create_subject_with_review(db, user, *, name, visibility="public", status="provisional", locked=False):
    token = str(uuid.uuid4())
    subject_type, _, _ = ensure_subject_type(
        db, f"live-subject-type-{token}", created_by="pytest",
    )
    subject = ensure_subject(db, SubjectEnsure(
        subject_type=subject_type.canonical_name,
        name=name,
        canonical_key=f"live-subject:{token}",
    ))
    subject.classification_status = status
    subject.classification_locked_at = datetime.now(timezone.utc) if locked else None
    db.commit()
    review = create_experience(db, ExperienceCreate(
        owner_id=user.id,
        subject_id=subject.id,
        headline=f"Review of {name}",
        summary=f"Summary of {name}",
        raw_text=f"Original words about {name}.",
        visibility=visibility,
        user_approved=True,
    ), "pytest")
    return subject_type, subject, review


def test_public_subjects_are_not_truncated_with_the_experience_feed(client):
    with SessionLocal() as db:
        user = User(display_name=f"limit-test-{uuid.uuid4()}", profile_data={})
        db.add(user); db.commit(); db.refresh(user)
        subject_type, subject, old_review = _create_subject_with_review(
            db, user, name="Yellow garden rose", status="candidate",
        )
        old_review.created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        _, filler_subject, _ = _create_subject_with_review(db, user, name="Recent subject")
        for index in range(100):
            create_experience(db, ExperienceCreate(
                owner_id=user.id,
                subject_id=filler_subject.id,
                headline=f"Recent review {index}",
                summary="Recent summary",
                raw_text="Recent original words.",
                visibility="public",
                user_approved=True,
            ), "pytest", commit=False)
        db.commit()
        subject_id = str(subject.id)
        subject_name = subject.name
        subject_type_name = subject_type.canonical_name

    payload = client.get("/api/v2/public/experiences?limit=100").json()

    assert all(item["subject"]["name"] != subject_name for item in payload["experiences"])
    public_subject = next(item for item in payload["subjects"] if item["id"] == subject_id)
    assert public_subject["name"] == "Yellow garden rose"
    assert public_subject["subject_type"] == subject_type_name
    assert public_subject["classification"]["status"] == "candidate"


def test_public_subjects_follow_visibility_changes_and_are_deduplicated(client):
    with SessionLocal() as db:
        user = User(display_name=f"visibility-feed-{uuid.uuid4()}", profile_data={})
        db.add(user); db.commit(); db.refresh(user)
        _, subject, first_review = _create_subject_with_review(
            db, user, name="Visibility subject", visibility="private",
        )
        first_review.publication_status = "draft"
        db.commit()
        principal = Principal(
            subject="pytest", client_id="pytest",
            scopes={"reviews:read", "reviews:write"}, user_id=user.id,
        )
        user_id = user.id
        subject_id = subject.id
        first_review_id = first_review.id

    def matching_subjects():
        payload = client.get("/api/v2/public/experiences?limit=1").json()
        return [item for item in payload["subjects"] if item["id"] == str(subject_id)]

    assert matching_subjects() == []

    with SessionLocal() as db:
        _set_review_visibility(db, principal, {
            "experience_id": str(first_review_id), "visibility": "public",
        })
    assert len(matching_subjects()) == 1

    with SessionLocal() as db:
        second_review = create_experience(db, ExperienceCreate(
            owner_id=user_id,
            subject_id=subject_id,
            headline="Second public review",
            summary="Second public summary",
            raw_text="Second public original words.",
            visibility="public",
            user_approved=True,
        ), "pytest")
        create_experience(db, ExperienceCreate(
            owner_id=user_id,
            subject_id=subject_id,
            headline="Private companion review",
            summary="Private companion summary",
            raw_text="Private companion original words.",
            visibility="private",
            user_approved=True,
        ), "pytest")
        second_review_id = second_review.id
    assert len(matching_subjects()) == 1

    with SessionLocal() as db:
        _set_review_visibility(db, principal, {
            "experience_id": str(first_review_id), "visibility": "private",
        })
    assert len(matching_subjects()) == 1

    with SessionLocal() as db:
        _set_review_visibility(db, principal, {
            "experience_id": str(second_review_id), "visibility": "unlisted",
        })
    assert matching_subjects() == []


@pytest.mark.parametrize(("status", "locked"), [
    ("provisional", False),
    ("candidate", False),
    ("confirmed", False),
    ("confirmed", True),
])
def test_public_subjects_include_every_classification_state(client, status, locked):
    with SessionLocal() as db:
        user = User(display_name=f"classification-{uuid.uuid4()}", profile_data={})
        db.add(user); db.commit(); db.refresh(user)
        _, subject, _ = _create_subject_with_review(
            db, user, name=f"{status}-{locked}", status=status, locked=locked,
        )
        subject_id = str(subject.id)

    payload = client.get("/api/v2/public/experiences?limit=1").json()
    public_subject = next(item for item in payload["subjects"] if item["id"] == subject_id)
    assert public_subject["classification"]["status"] == status
    assert public_subject["classification"]["locked"] is locked


def test_live_page_uses_the_authoritative_subject_feed(client):
    page = client.get("/live")

    assert page.status_code == 200
    assert "currentPublicSubjects" in page.text
    assert "feed.subjects||[]" in page.text


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
    visible = next(item for item in payload["experiences"] if item["headline"] == "Visible headline")
    assert visible["subject"]["classification"]["status"] == "provisional"
    assert visible["subject"]["classification"]["version"] == 1
    assert visible["subject"]["classification"]["locked"] is False
    assert visible["subject"]["classification"]["decisions"] == []
    assert "Private headline" not in rendered
    assert "owner_id" not in rendered
    assert "created_by_client" not in rendered
    assert "private_trace" not in rendered
    assert "internal-client-secret" not in rendered
