import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import User
from app.models.v2 import Concept, ConceptField, FieldAlias, V2Experience
from app.schemas.v2 import ConceptEnsure, ExperienceCreate, FieldProposal, SubjectEnsure
from app.services.v2 import create_experience, ensure_concept, ensure_subject, normalise_data


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def camera_fields():
    return [
        FieldProposal(
            submitted_name="autofocus",
            canonical_name="AF",
            data_type="rating",
            description="Overall autofocus performance",
            aliases=["auto_focus", "focus performance"],
        ),
        FieldProposal(
            submitted_name="subject_tracking",
            canonical_name="AF.tracking",
            data_type="rating",
            description="Autofocus subject tracking",
            aliases=["focus tracking", "tracking autofocus", "af tracking"],
        ),
    ]


def test_first_camera_creates_hierarchy_and_vocabulary(db: Session):
    concept = ensure_concept(db, ConceptEnsure(
        path="product.electronics.camera.action_camera",
        description="Compact rugged action camera",
        proposed_fields=camera_fields(),
        created_by="chatgpt",
    ))
    assert concept.path == "product.electronics.camera.action_camera"
    assert db.scalar(select(Concept).where(Concept.path == "product")) is not None
    assert db.scalar(select(Concept).where(Concept.path == "product.electronics.camera")) is not None
    names = set(db.scalars(select(ConceptField.canonical_name).where(ConceptField.concept_id == concept.id)).all())
    assert names == {"AF", "AF.tracking"}


def test_different_ai_terms_converge_on_same_canonical_fields(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera", proposed_fields=camera_fields(), created_by="chatgpt"))
    data, log = normalise_data(db, concept, {"auto_focus": 9, "focus tracking": 10}, [], "claude")
    assert data == {"AF": 9, "AF.tracking": 10}
    assert {item["method"] for item in log} == {"alias"}


def test_unknown_field_is_rejected_until_explicitly_proposed(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera", proposed_fields=camera_fields()))
    with pytest.raises(ValueError) as exc:
        normalise_data(db, concept, {"weather sealing": 8}, [], "other-ai")
    assert "unknown_fields" in str(exc.value)

    data, _ = normalise_data(db, concept, {"weather sealing": 8}, [
        FieldProposal(submitted_name="weather sealing", canonical_name="weather_sealing", data_type="rating")
    ], "other-ai")
    assert data == {"weather_sealing": 8}


def test_direct_experience_preserves_original_and_canonical_data(db: Session):
    user = User(display_name="Test user", profile_data={})
    db.add(user); db.commit(); db.refresh(user)
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera", proposed_fields=camera_fields()))
    subject = ensure_subject(db, SubjectEnsure(concept_path=concept.path, name="Example Camera", canonical_key="example-camera"))
    exp = create_experience(db, ExperienceCreate(
        owner_id=user.id,
        subject_id=subject.id,
        headline="Excellent tracking",
        summary="Tracking is excellent.",
        raw_text="The autofocus tracking is astonishingly good on birds.",
        structured_data={"autofocus": 9, "af tracking": 10},
        user_approved=True,
        source_client="chatgpt",
    ), "chatgpt")
    assert exp.submitted_data == {"autofocus": 9, "af tracking": 10}
    assert exp.structured_data == {"AF": 9, "AF.tracking": 10}
    assert exp.raw_text.startswith("The autofocus")
    assert exp.provenance["kind"] == "direct_user_experience"
