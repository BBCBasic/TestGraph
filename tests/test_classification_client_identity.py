import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.mcp_v2 import TOOLS, _affirm_subject_classification
from app.core.security import Principal
from app.db.base import Base
from app.models.v2 import SubjectType
from app.schemas.v2 import SubjectEnsure
from app.services.client_identity import canonical_client_identity
from app.services.v2 import ensure_subject


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_classification_identity_preserves_authenticated_client_ids_exactly():
    assert canonical_client_identity("oauth-client:v3") == "oauth-client:v3"
    assert canonical_client_identity("oauth-client:v2") == "oauth-client:v2"
    assert canonical_client_identity("organisation:agent") == "organisation:agent"
    assert canonical_client_identity("organisation:agent:v4") == "organisation:agent:v4"
    assert canonical_client_identity("organisation:agent:v3:v3") == "organisation:agent:v3:v3"


def test_rest_creation_then_mcp_review_by_same_oauth_client_cannot_confirm():
    with _session() as db:
        subject_type = SubjectType(
            canonical_name="person",
            normalized_name="person",
            status="provisional",
            created_by="pytest",
        )
        db.add(subject_type)
        db.commit()
        user_id = uuid.uuid4()
        subject = ensure_subject(
            db,
            SubjectEnsure(
                subject_type="person",
                name="Fred",
                canonical_key="fred",
                identifiers={},
                attributes={},
                provenance={},
            ),
            "same-oauth-client",
            owner_id=user_id,
        )
        principal = Principal(
            subject="pytest",
            client_id="same-oauth-client",
            scopes={"reviews:read", "reviews:write"},
            user_id=user_id,
        )

        response = _affirm_subject_classification(
            db,
            principal,
            {
                "subject_id": str(subject.id),
                "source_model": "different-reported-model",
                "reason": "Fred is a person",
                "evidence": {},
            },
        )

        state = response["structuredContent"]
        assert state["status"] == "candidate"
        assert state["locked_at"] is None
        assert state["creation_proposal"]["source_client"] == "same-oauth-client"
        assert state["active_decisions"][0]["source_client"] == "same-oauth-client"


def test_classification_tool_descriptions_explain_creator_reviewer_settlement():
    descriptions = {
        tool["name"]: tool["description"]
        for tool in TOOLS
        if tool["name"] in {"affirm_subject_classification", "propose_subject_reclassification"}
    }
    for description in descriptions.values():
        lowered = description.lower()
        assert "authenticated client" in lowered
        assert "creation proposal" in lowered
        assert "two distinct model identities" not in lowered


def test_creator_review_migration_revision_fits_default_alembic_column():
    migration = Path("alembic/versions/0025_creator_review_classification.py")
    spec = importlib.util.spec_from_file_location("creator_review_migration", migration)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert len(module.revision) <= 32


def test_migration_preserves_raw_proposal_and_strips_one_known_legacy_decision_tag():
    migration = Path("alembic/versions/0025_creator_review_classification.py")
    spec = importlib.util.spec_from_file_location("creator_review_backfill", migration)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    subjects = sa.Table(
        "v2_subjects", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_type_id", sa.Uuid(), nullable=False),
        sa.Column("provenance_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    runs = sa.Table(
        "workflow_runs", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
    )
    events = sa.Table(
        "workflow_events", metadata,
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("actor_client", sa.String()),
        sa.Column("actor_model", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    decisions = sa.Table(
        "subject_classification_decisions", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("classification_version", sa.Integer(), nullable=False),
        sa.Column("source_model", sa.String(), nullable=False),
        sa.Column("source_client", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    subject_id = uuid.uuid4()
    type_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(subjects.insert().values(
            id=subject_id, subject_type_id=type_id, provenance_json={}, created_at=now,
        ))
        connection.execute(runs.insert().values(id=run_id, subject_id=subject_id))
        connection.execute(events.insert().values(
            workflow_run_id=run_id,
            actor_client="oauth-client:v3",
            actor_model="creator-model",
            created_at=now,
        ))
        connection.execute(decisions.insert().values(
            id=uuid.uuid4(), subject_id=subject_id, classification_version=1,
            source_model="review-model", source_client="oauth-client:v3:v3", created_at=now,
        ))
        original_op = module.op
        module.op = SimpleNamespace(get_bind=lambda: connection)
        try:
            module._backfill_creation_proposals()
            module._normalize_legacy_decision_clients()
        finally:
            module.op = original_op

        proposal = connection.execute(sa.select(subjects.c.provenance_json)).scalar_one()[
            "classification_proposal"
        ]
        decision_client = connection.execute(sa.select(decisions.c.source_client)).scalar_one()

    assert proposal["source_client"] == "oauth-client:v3"
    assert proposal["identity_basis"] == "workflow_backfill"
    assert decision_client == "oauth-client:v3"
