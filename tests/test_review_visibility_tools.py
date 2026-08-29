import uuid

from app.api.mcp_v2 import TOOLS
from app.core.security import Principal
from app.db.session import SessionLocal
from app.models.entities import User
from app.models.v2 import SubjectType, V2Experience, V2Subject
from app.services.mcp_v2_guidance_policy import (
    _list_reviews_by_visibility,
    _set_review_visibility,
)


def _principal(user_id):
    return Principal(subject="pytest", client_id="pytest", scopes={"reviews:read", "reviews:write"}, user_id=user_id)


def _make_review(db, user, visibility, headline):
    token = str(uuid.uuid4())
    subject_type = SubjectType(
        canonical_name=f"visibility-test-{token}",
        normalized_name=f"visibility-test-{token}",
        status="provisional",
        created_by="pytest",
    )
    db.add(subject_type); db.flush()
    subject = V2Subject(
        subject_type_id=subject_type.id,
        owner_id=user.id,
        name=f"Subject {headline}",
        canonical_key=f"subject:{uuid.uuid4()}",
        identifiers_json={}, attributes_json={}, provenance_json={},
    )
    db.add(subject); db.flush()
    review = V2Experience(
        owner_id=user.id,
        subject_id=subject.id,
        headline=headline,
        summary=headline,
        raw_text=headline,
        visibility=visibility,
        publication_status="published",
        structured_data={}, submitted_data={}, normalization_log=[], provenance={},
        created_by_client="pytest",
    )
    db.add(review); db.commit(); db.refresh(review)
    return review


def test_review_visibility_tools_are_published():
    names = {tool["name"] for tool in TOOLS}
    assert {"list_reviews_by_visibility", "set_review_visibility"} <= names
    setter = next(tool for tool in TOOLS if tool["name"] == "set_review_visibility")
    assert "version_check" in setter["inputSchema"]["required"]


def test_list_reviews_by_visibility_returns_stable_ids_and_positions():
    with SessionLocal() as db:
        user = User(display_name=f"visibility-{uuid.uuid4()}", profile_data={})
        db.add(user); db.commit(); db.refresh(user)
        private = _make_review(db, user, "private", "Private review")
        _make_review(db, user, "public", "Public review")

        result = _list_reviews_by_visibility(db, _principal(user.id), {"visibility": "private"})["structuredContent"]
        assert result["visibility"] == "private"
        assert result["items"] == [{
            "position": 1,
            "experience_id": str(private.id),
            "subject_name": "Subject Private review",
            "headline": "Private review",
        }]


def test_set_review_visibility_enforces_ownership_and_publishes_public_review():
    with SessionLocal() as db:
        owner = User(display_name=f"owner-{uuid.uuid4()}", profile_data={})
        other = User(display_name=f"other-{uuid.uuid4()}", profile_data={})
        db.add_all([owner, other]); db.commit(); db.refresh(owner); db.refresh(other)
        review = _make_review(db, owner, "private", "Visibility change")
        review.publication_status = "draft"
        db.commit()

        denied = _set_review_visibility(db, _principal(other.id), {
            "experience_id": str(review.id), "visibility": "public"
        })
        assert denied["isError"] is True

        changed = _set_review_visibility(db, _principal(owner.id), {
            "experience_id": str(review.id), "visibility": "public"
        })["structuredContent"]
        db.refresh(review)
        assert changed["experience_id"] == str(review.id)
        assert changed["visibility"] == "public"
        assert review.visibility == "public"
        assert review.publication_status == "published"
