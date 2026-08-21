import uuid

import pytest

from app.api.mcp_v2 import SERVER_VERSION, TOOLS
from app.db.session import SessionLocal
from app.models.entities import User
from app.schemas.deliberation import (
    DeliberationContributionCreate,
    DeliberationCreate,
    DeliberationResolutionCreate,
)
from app.services.deliberation import (
    DeliberationError,
    create_deliberation,
    get_deliberation,
    record_resolution,
    submit_contribution,
)


def _user(db, label):
    user = User(display_name=f"{label}-{uuid.uuid4()}")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_deliberation_preserves_attribution_disagreement_and_user_authority():
    with SessionLocal() as db:
        owner = _user(db, "deliberation-owner")
        deliberation = create_deliberation(
            db,
            DeliberationCreate(
                canonical_key=f"architecture:{uuid.uuid4()}",
                title="Choose a first MCP workflow",
                question="Should TestGraph add deliberation or a generic task queue first?",
                context={"current_tools": ["save_assessment"]},
                constraints=["Keep the first version narrow"],
                acceptance_criteria={"auditable": True},
            ),
            owner_id=owner.id,
            client_id="chatgpt:v3",
        )
        proposal = submit_contribution(
            db,
            DeliberationContributionCreate(
                deliberation_id=deliberation.id,
                contribution_type="proposal",
                content="Add a narrow deliberation workflow first.",
                evidence={"reason": "The immediate need is controlled disagreement."},
                confidence=0.82,
                unresolved_points=["How should clients discover open deliberations?"],
                source_model="gpt-5",
            ),
            owner_id=owner.id,
            client_id="chatgpt:v3",
        )
        critique = submit_contribution(
            db,
            DeliberationContributionCreate(
                deliberation_id=deliberation.id,
                contribution_type="critique",
                content="A stable key is required for cross-client retrieval.",
                evidence={"risk": "A UUID must otherwise be copied manually."},
                confidence=0.9,
                unresolved_points=["Whether listing should be added later"],
                responds_to_contribution_ids=[proposal.id],
                source_model="claude",
            ),
            owner_id=owner.id,
            client_id="claude:v3",
        )

        body = get_deliberation(
            db, owner_id=owner.id, canonical_key=deliberation.canonical_key
        )
        assert body["status"] == "open"
        assert [item["source_client"] for item in body["contributions"]] == [
            "chatgpt:v3", "claude:v3"
        ]
        assert body["contributions"][1]["responds_to_contribution_ids"] == [
            str(proposal.id)
        ]

        with pytest.raises(DeliberationError) as denied:
            record_resolution(
                db,
                DeliberationResolutionCreate(
                    deliberation_id=deliberation.id,
                    resolution="Adopt the narrow workflow.",
                    accepted_contribution_ids=[proposal.id, critique.id],
                    unresolved_points=["Listing remains future work"],
                    user_approved=False,
                ),
                owner_id=owner.id,
                client_id="claude:v3",
            )
        assert denied.value.code == "USER_APPROVAL_REQUIRED"

        resolved = record_resolution(
            db,
            DeliberationResolutionCreate(
                deliberation_id=deliberation.id,
                resolution="Adopt deliberation with stable-key retrieval.",
                rationale="Both contributions identify the narrowest useful system.",
                accepted_contribution_ids=[proposal.id, critique.id],
                unresolved_points=["Listing remains future work"],
                user_approved=True,
            ),
            owner_id=owner.id,
            client_id="chatgpt:v3",
        )
        assert resolved.status == "resolved"
        assert resolved.resolution_json["user_approved"] is True
        assert resolved.resolution_json["unresolved_points"] == [
            "Listing remains future work"
        ]

        with pytest.raises(DeliberationError) as closed:
            submit_contribution(
                db,
                DeliberationContributionCreate(
                    deliberation_id=deliberation.id,
                    contribution_type="reconciliation",
                    content="Late agreement",
                ),
                owner_id=owner.id,
                client_id="claude:v3",
            )
        assert closed.value.code == "DELIBERATION_CLOSED"


def test_deliberation_is_private_to_its_owner():
    with SessionLocal() as db:
        owner = _user(db, "owner")
        other = _user(db, "other")
        deliberation = create_deliberation(
            db,
            DeliberationCreate(
                canonical_key=f"private:{uuid.uuid4()}",
                title="Private deliberation",
                question="What next?",
            ),
            owner_id=owner.id,
            client_id="chatgpt:v3",
        )
        with pytest.raises(DeliberationError) as hidden:
            get_deliberation(
                db, owner_id=other.id, deliberation_id=deliberation.id
            )
        assert hidden.value.code == "DELIBERATION_NOT_FOUND"


def test_deliberation_tools_publish_strict_schemas_and_new_version():
    tools = {tool["name"]: tool for tool in TOOLS}
    assert SERVER_VERSION == "3.13.0-alpha"
    assert {
        "create_deliberation",
        "get_deliberation",
        "submit_contribution",
        "record_resolution",
    }.issubset(tools)
    assert tools["create_deliberation"]["inputSchema"]["additionalProperties"] is False
    assert tools["submit_contribution"]["inputSchema"]["properties"][
        "contribution_type"
    ]["enum"] == ["proposal", "critique", "counterproposal", "reconciliation"]
    assert "user_approved" in tools["record_resolution"]["inputSchema"]["required"]
    assert tools["get_deliberation"]["annotations"]["readOnlyHint"] is True
