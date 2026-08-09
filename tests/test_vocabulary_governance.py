import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import semantic  # noqa: F401
from app.models.v2 import ConceptField, ConceptFieldProposal
from app.schemas.v2 import ConceptEnsure, FieldProposal
from app.services.v2 import ensure_concept, propose_concept_fields
from app.services.vocabulary_governance import verify_field_proposal, vocabulary_index


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def bike_field():
    return FieldProposal(
        submitted_name="bike condition",
        canonical_name="bike_condition",
        json_schema={"type": "string"},
        description="Condition of a hired bicycle.",
        aliases=["cycle condition"],
    )


def test_global_index_links_words_to_dns_positions_and_pending_proposals(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="travel.bike_hire", created_by="chatgpt:v2"))
    proposal = propose_concept_fields(
        db,
        concept=concept,
        proposals=[bike_field()],
        proposer_client_id="chatgpt:v2",
    )[0]

    bike = vocabulary_index(db, "bike")
    assert bike["match_count"] >= 3
    assert any(
        item["kind"] == "concept_path_segment"
        and item["concept_path"] == "travel.bike_hire"
        for item in bike["matches"]
    )
    assert any(
        item["kind"] == "pending_field_proposal"
        and item["proposal_id"] == str(proposal.id)
        and item["position"].startswith(f"proposal:{proposal.id}:")
        for item in bike["matches"]
    )

    whole = vocabulary_index(db)
    row = next(item for item in whole["index"] if item["word"] == "hire")
    assert any(location["concept_path"] == "travel.bike_hire" for location in row["locations"])


def test_proposing_ai_cannot_verify_its_own_field(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="travel.bike_hire", created_by="chatgpt:v2"))
    proposal = propose_concept_fields(
        db,
        concept=concept,
        proposals=[bike_field()],
        proposer_client_id="chatgpt:v2",
    )[0]

    with pytest.raises(ValueError, match="cannot verify its own"):
        verify_field_proposal(
            db,
            proposal.id,
            verifier_client_id="chatgpt:v2",
            reason="I agree with myself",
        )

    assert db.get(ConceptFieldProposal, proposal.id).status == "pending"
    assert db.scalar(select(ConceptField).where(ConceptField.concept_id == concept.id)) is None


def test_second_ai_verifies_and_commits_pending_field(db: Session):
    concept = ensure_concept(db, ConceptEnsure(path="travel.bike_hire", created_by="chatgpt:v2"))
    proposal = propose_concept_fields(
        db,
        concept=concept,
        proposals=[bike_field()],
        proposer_client_id="chatgpt:v2",
    )[0]

    decided, field = verify_field_proposal(
        db,
        proposal.id,
        verifier_client_id="claude:v2",
        reason="The field is distinct and the string schema matches the intended meaning.",
    )

    assert decided.status == "approved"
    assert decided.proposer_client_id == "chatgpt:v2"
    assert decided.decision_by == "claude:v2"
    assert decided.decision_reason.startswith("The field is distinct")
    assert field.canonical_name == "bike_condition"

    pending = vocabulary_index(db, "condition")
    assert not any(item["kind"] == "pending_field_proposal" for item in pending["matches"])
    assert any(
        item["kind"] == "canonical_field"
        and item["canonical_name"] == "bike_condition"
        and item["concept_path"] == "travel.bike_hire"
        for item in pending["matches"]
    )

    # Verification is idempotent once another client has already promoted it.
    again, same_field = verify_field_proposal(
        db,
        proposal.id,
        verifier_client_id="gemini:v2",
    )
    assert again.status == "approved"
    assert same_field.id == field.id
    assert again.decision_by == "claude:v2"
