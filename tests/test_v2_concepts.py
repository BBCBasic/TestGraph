import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import semantic  # noqa: F401
from app.models.entities import User
from app.models.v2 import Concept, ConceptField, FieldAlias
from app.schemas.v2 import ConceptEnsure, ExperienceCreate, FieldProposal, SubjectEnsure
from app.services.semantic import alias_consensus_status, propose_alias
from app.services.v2 import create_experience, ensure_concept, ensure_subject, normalise_data


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def af_field():
    return FieldProposal(
        submitted_name="autofocus",
        canonical_name="AF",
        data_type="rating",
        description="Overall autofocus performance",
        aliases=["auto_focus", "focus performance"],
    )


def tracking_field():
    return FieldProposal(
        submitted_name="subject_tracking",
        canonical_name="AF.tracking",
        data_type="rating",
        description="Autofocus subject tracking",
        aliases=["focus tracking", "tracking autofocus", "af tracking"],
    )


def test_first_camera_creates_hierarchy_and_canonical_vocabulary_without_auto_accepting_aliases(db: Session):
    concept = ensure_concept(db, ConceptEnsure(
        path="product.electronics.camera.action_camera",
        description="Compact rugged action camera",
        proposed_fields=[af_field(), tracking_field()],
        created_by="chatgpt-client",
    ))
    assert concept.path == "product.electronics.camera.action_camera"
    assert db.scalar(select(Concept).where(Concept.path == "product")) is not None
    assert db.scalar(select(Concept).where(Concept.path == "product.electronics.camera")) is not None
    names = set(db.scalars(select(ConceptField.canonical_name).where(ConceptField.concept_id == concept.id)).all())
    assert names == {"AF", "AF.tracking"}
    assert db.scalar(select(FieldAlias).where(FieldAlias.concept_id == concept.id)) is None


def test_new_field_use_records_first_ai_vote_but_does_not_accept_alias(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera"))
    data, log = normalise_data(db, concept, {"autofocus": 9}, [af_field()], "chatgpt-client")
    assert data == {"AF": 9}
    assert log == [{"submitted": "autofocus", "canonical": "AF", "method": "new_field_proposal"}]
    status = alias_consensus_status(db, concept, "autofocus")
    assert status["status"] == "proposed"
    assert status["supporting_clients"] == 1
    assert db.scalar(select(FieldAlias).where(FieldAlias.concept_id == concept.id)) is None


def test_same_client_cannot_manufacture_consensus(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera", proposed_fields=[af_field()]))
    first = propose_alias(db, concept=concept, alias="autofocus", canonical_name="AF", proposer_client_id="chatgpt-client")
    second = propose_alias(db, concept=concept, alias="autofocus", canonical_name="AF", proposer_client_id="chatgpt-client")
    db.commit()
    assert first["status"] == "proposed"
    assert second["status"] == "proposed"
    assert second["supporting_clients"] == 1
    assert db.scalar(select(FieldAlias).where(FieldAlias.concept_id == concept.id)) is None


def test_second_independent_ai_promotes_alias_then_future_writes_normalise_automatically(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera", proposed_fields=[af_field()]))
    propose_alias(db, concept=concept, alias="autofocus", canonical_name="AF", proposer_client_id="chatgpt-client")
    status = propose_alias(db, concept=concept, alias="autofocus", canonical_name="AF", proposer_client_id="claude-client")
    db.commit()
    assert status["status"] == "accepted"
    assert status["promoted_by_consensus"] is True

    data, log = normalise_data(db, concept, {"autofocus": 9}, [], "third-ai")
    assert data == {"AF": 9}
    assert log == [{"submitted": "autofocus", "canonical": "AF", "method": "accepted_alias"}]


def test_conflicting_ai_semantics_block_promotion(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera", proposed_fields=[af_field(), tracking_field()]))
    first = propose_alias(db, concept=concept, alias="tracking", canonical_name="AF.tracking", proposer_client_id="chatgpt-client")
    second = propose_alias(db, concept=concept, alias="tracking", canonical_name="AF", proposer_client_id="claude-client")
    third = propose_alias(db, concept=concept, alias="tracking", canonical_name="AF.tracking", proposer_client_id="gemini-client")
    db.commit()
    assert first["status"] == "proposed"
    assert second["status"] == "conflict"
    assert third["status"] == "conflict"
    assert db.scalar(select(FieldAlias).where(FieldAlias.concept_id == concept.id, FieldAlias.alias_normalized == "tracking")) is None


def test_unknown_field_is_rejected_when_ai_does_not_propose_new_field_or_existing_mapping(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera", proposed_fields=[af_field()]))
    with pytest.raises(ValueError) as exc:
        normalise_data(db, concept, {"weather sealing": 8}, [], "other-ai")
    assert "unknown_fields" in str(exc.value)


def test_direct_experience_preserves_original_and_canonical_data(db: Session):
    user = User(display_name="Test user", profile_data={})
    db.add(user); db.commit(); db.refresh(user)
    concept = ensure_concept(db, ConceptEnsure(path="product.electronics.camera"))
    subject = ensure_subject(db, SubjectEnsure(concept_path=concept.path, name="Example Camera", canonical_key="example-camera"))
    exp = create_experience(db, ExperienceCreate(
        owner_id=user.id,
        subject_id=subject.id,
        headline="Excellent autofocus",
        summary="Autofocus is excellent.",
        raw_text="The autofocus is astonishingly good on birds.",
        structured_data={"autofocus": 9},
        proposed_fields=[af_field()],
        user_approved=True,
        source_client="chatgpt",
    ), "chatgpt-authenticated-client")
    assert exp.submitted_data == {"autofocus": 9}
    assert exp.structured_data == {"AF": 9}
    assert exp.raw_text.startswith("The autofocus")
    assert exp.provenance["kind"] == "direct_user_experience"
    assert alias_consensus_status(db, concept, "autofocus")["supporting_clients"] == 1
