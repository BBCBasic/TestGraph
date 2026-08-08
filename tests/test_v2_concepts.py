import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import semantic  # noqa: F401
from app.models.entities import User
from app.models.v2 import Assessment, Concept, ConceptField, ConceptFieldProposal, FieldAlias
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, FieldProposal, SubjectEnsure
from app.services.semantic import alias_consensus_status, propose_alias
from app.services.v2 import (
    approve_field_proposal,
    create_assessment,
    create_experience,
    ensure_concept,
    ensure_subject,
    normalise_data,
    propose_concept_fields,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def observations_field():
    return FieldProposal(
        submitted_name="user_direct_observations",
        canonical_name="user_direct_observations",
        json_schema={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_type": {"type": "string"},
                    "target_name": {"type": "string"},
                    "exact_words": {"type": "string"},
                },
                "required": ["target_type", "target_name", "exact_words"],
                "additionalProperties": False,
            },
        },
        description="The user's exact observations, attached to their targets.",
    )


def test_ensure_concept_creates_hierarchy_but_no_fields(db: Session):
    concept = ensure_concept(db, ConceptEnsure(
        path="dining.restaurant_review",
        description="A direct restaurant experience",
        created_by="claude-client",
    ))
    assert concept.path == "dining.restaurant_review"
    assert db.scalar(select(Concept).where(Concept.path == "dining")) is not None
    assert db.scalar(select(ConceptField).where(ConceptField.concept_id == concept.id)) is None


def test_field_proposal_stays_pending_and_creates_no_experience_or_canonical_field(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="dining.restaurant_review"))
    rows = propose_concept_fields(
        db,
        concept=concept,
        proposals=[observations_field()],
        proposer_client_id="claude-client:v2",
    )
    assert rows[0].status == "pending"
    assert db.scalar(select(ConceptField).where(ConceptField.concept_id == concept.id)) is None
    assert db.scalar(select(ConceptFieldProposal).where(
        ConceptFieldProposal.concept_id == concept.id
    )) is not None


def test_manual_approval_promotes_full_json_schema(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="dining.restaurant_review"))
    proposal = propose_concept_fields(
        db,
        concept=concept,
        proposals=[observations_field()],
        proposer_client_id="claude-client:v2",
    )[0]
    field = approve_field_proposal(db, proposal.id)
    assert field.canonical_name == "user_direct_observations"
    assert field.metadata_json["json_schema"]["items"]["type"] == "object"
    assert db.get(ConceptFieldProposal, proposal.id).status == "approved"


def test_approved_json_schema_is_enforced(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="dining.restaurant_review"))
    proposal = propose_concept_fields(
        db, concept=concept, proposals=[observations_field()], proposer_client_id="claude-client:v2"
    )[0]
    approve_field_proposal(db, proposal.id)

    valid = [{
        "target_type": "dish",
        "target_name": "Beef carpaccio",
        "exact_words": "very good",
    }]
    data, log = normalise_data(db, concept, {"user_direct_observations": valid})
    assert data == {"user_direct_observations": valid}
    assert log[0]["method"] == "canonical"

    with pytest.raises(ValueError, match="exact_words"):
        normalise_data(db, concept, {
            "user_direct_observations": [{"target_type": "dish", "target_name": "Salad"}]
        })


def test_unknown_field_requires_separate_proposal_and_approval(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="dining.restaurant_review"))
    with pytest.raises(ValueError) as exc:
        normalise_data(db, concept, {"would_return_if_local": True})
    assert "propose_concept_fields" in str(exc.value)


def test_alias_consensus_still_operates_after_field_approval(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="dining.restaurant_review"))
    proposal = propose_concept_fields(
        db, concept=concept, proposals=[observations_field()], proposer_client_id="schema-client"
    )[0]
    approve_field_proposal(db, proposal.id)
    first = propose_alias(
        db,
        concept=concept,
        alias="direct_comments",
        canonical_name="user_direct_observations",
        proposer_client_id="chatgpt-client",
    )
    second = propose_alias(
        db,
        concept=concept,
        alias="direct_comments",
        canonical_name="user_direct_observations",
        proposer_client_id="claude-client",
    )
    db.commit()
    assert first["status"] == "proposed"
    assert second["status"] == "accepted"
    assert db.scalar(select(FieldAlias).where(
        FieldAlias.concept_id == concept.id,
        FieldAlias.alias_normalized == "direct_comments",
    )) is not None
    assert alias_consensus_status(db, concept, "direct_comments")["status"] == "accepted"


def test_direct_experience_requires_original_text_and_approved_fields(db: Session):
    user = User(display_name="Test user", profile_data={})
    db.add(user); db.commit(); db.refresh(user)
    concept = ensure_concept(db, ConceptEnsure(path="dining.restaurant_review"))
    proposal = propose_concept_fields(
        db, concept=concept, proposals=[observations_field()], proposer_client_id="schema-client"
    )[0]
    approve_field_proposal(db, proposal.id)
    subject = ensure_subject(
        db,
        SubjectEnsure(
            concept_path=concept.path,
            name="Gustave",
            canonical_key="restaurant-gustave-neuilly.fr",
        ),
    )
    value = [{
        "target_type": "dish",
        "target_name": "Beef carpaccio",
        "exact_words": "very good",
    }]
    exp = create_experience(db, ExperienceCreate(
        owner_id=user.id,
        subject_id=subject.id,
        headline="Gustave",
        summary="The carpaccio was very good.",
        raw_text="I had beef carpaccio, that was very good.",
        structured_data={"user_direct_observations": value},
        user_approved=True,
        source_client="claude-authenticated-client",
    ), "claude-authenticated-client")
    assert exp.submitted_data == {"user_direct_observations": value}
    assert exp.structured_data == {"user_direct_observations": value}
    assert exp.raw_text.startswith("I had beef carpaccio")
    assert exp.provenance == {
        "kind": "direct_user_experience",
        "source_client": "claude-authenticated-client",
    }

    with pytest.raises(PydanticValidationError):
        ExperienceCreate(
            owner_id=user.id,
            subject_id=subject.id,
            headline="Missing original",
            summary="No raw evidence",
            raw_text="",
            user_approved=True,
        )


def test_assessment_derives_exact_target_and_authenticated_provenance(db: Session):
    user = User(display_name="Assessment owner", profile_data={})
    db.add(user); db.commit(); db.refresh(user)
    concept = ensure_concept(db, ConceptEnsure(path="dining.restaurant_review"))
    subject = ensure_subject(db, SubjectEnsure(
        concept_path=concept.path,
        name="Gustave",
        canonical_key="gustave-restaurant",
    ))
    experience = create_experience(db, ExperienceCreate(
        owner_id=user.id,
        subject_id=subject.id,
        headline="Gustave",
        summary="The stored representation may overstate the evidence.",
        raw_text="There is too much else to try.",
        user_approved=True,
    ), "claude-connector:v2")

    assessment = create_assessment(
        db,
        AssessmentCreate(
            experience_id=experience.id,
            assessment_type="representation_fidelity",
            evidence={"direct": ["There is too much else to try."]},
            analysis={"inference": "Returning is a lower priority."},
            conclusion="The stored boolean is stronger than the evidence.",
            confidence=0.95,
            provenance={"source_client": "spoofed-client", "kind": "direct_user_experience"},
        ),
        client_id="chatgpt-connector:v2",
        user_id=user.id,
    )

    assert assessment.experience_id == experience.id
    assert assessment.subject_id == subject.id
    assert assessment.user_id == user.id
    assert assessment.created_by_client == "chatgpt-connector:v2"
    assert assessment.provenance == {
        "source_client": "chatgpt-connector:v2",
        "kind": "ai_derived_assessment",
        "target_experience_id": str(experience.id),
    }
    assert db.scalar(select(Assessment).where(
        Assessment.experience_id == experience.id
    )) is assessment


def test_assessment_cannot_target_another_users_experience(db: Session):
    owner = User(display_name="Owner", profile_data={})
    other = User(display_name="Other", profile_data={})
    db.add_all([owner, other]); db.commit()
    concept = ensure_concept(db, ConceptEnsure(path="dining.restaurant_review"))
    subject = ensure_subject(db, SubjectEnsure(
        concept_path=concept.path,
        name="Gustave",
        canonical_key="gustave-restaurant",
    ))
    experience = create_experience(db, ExperienceCreate(
        owner_id=owner.id,
        subject_id=subject.id,
        headline="Gustave",
        summary="A direct experience.",
        raw_text="The staff were nice.",
        user_approved=True,
    ), "claude-connector:v2")

    with pytest.raises(ValueError, match="Experience not found"):
        create_assessment(
            db,
            AssessmentCreate(
                experience_id=experience.id,
                assessment_type="representation_fidelity",
            ),
            client_id="chatgpt-connector:v2",
            user_id=other.id,
        )
