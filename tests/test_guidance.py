import uuid

import pytest
from pydantic import ValidationError

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
    record_resolution,
    submit_contribution,
)
from app.services.guidance import get_induction
from app.services.mcp_v2_guidance_policy import apply_guidance_tool_policy


def _user(db, label="guidance-owner"):
    user = User(display_name=f"{label}-{uuid.uuid4()}")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _guidance_deliberation(db, owner, *, key, scope="global", target_model=None):
    context = {
        "governance_kind": "induction_guidance",
        "guidance_key": key,
        "guidance_scope": scope,
    }
    if target_model:
        context["target_model"] = target_model
    return create_deliberation(
        db,
        DeliberationCreate(
            canonical_key=f"guidance:{key}:{uuid.uuid4()}",
            title=f"Change {key} guidance",
            question=f"Should {key} guidance change?",
            context=context,
            target_model=target_model,
        ),
        owner_id=owner.id,
        client_id="chatgpt:v3",
    )


def test_unresolved_guidance_and_votes_do_not_activate_but_user_resolution_does():
    with SessionLocal() as db:
        owner = _user(db)
        item = _guidance_deliberation(db, owner, key="retrieval")
        vote = submit_contribution(
            db,
            DeliberationContributionCreate(
                deliberation_id=item.id,
                contribution_type="vote",
                content="I support the proposed retrieval wording.",
                evidence={"vote": "approve", "reason": "It is more explicit."},
                source_model="claude",
            ),
            owner_id=owner.id,
            client_id="claude:v3",
        )
        assert vote.contribution_type == "vote"

        before = get_induction(db, owner_id=owner.id, source_model="claude")
        retrieval_before = next(
            row for row in before["effective_guidance"] if row["key"] == "retrieval"
        )
        assert retrieval_before.get("source") != "user_approved_guidance"
        assert before["approved_global_guidance"] == []

        with pytest.raises(DeliberationError) as denied:
            record_resolution(
                db,
                DeliberationResolutionCreate(
                    deliberation_id=item.id,
                    resolution="Prefer exact subject-name follow-ups after broad lexical search.",
                    accepted_contribution_ids=[vote.id],
                    user_approved=False,
                ),
                owner_id=owner.id,
                client_id="chatgpt:v3",
            )
        assert denied.value.code == "USER_APPROVAL_REQUIRED"

        record_resolution(
            db,
            DeliberationResolutionCreate(
                deliberation_id=item.id,
                resolution="Prefer exact subject-name follow-ups after broad lexical search.",
                accepted_contribution_ids=[vote.id],
                user_approved=True,
            ),
            owner_id=owner.id,
            client_id="chatgpt:v3",
        )
        after = get_induction(db, owner_id=owner.id, source_model="claude")
        retrieval_after = next(
            row for row in after["effective_guidance"] if row["key"] == "retrieval"
        )
        assert retrieval_after["source"] == "user_approved_guidance"
        assert retrieval_after["text"].startswith("Prefer exact subject-name")
        assert len(after["approved_global_guidance"]) == 1


def test_model_guidance_layers_over_global_and_does_not_leak_to_other_models():
    with SessionLocal() as db:
        owner = _user(db)
        global_item = _guidance_deliberation(db, owner, key="classification")
        record_resolution(
            db,
            DeliberationResolutionCreate(
                deliberation_id=global_item.id,
                resolution="Global classification guidance.",
                user_approved=True,
            ),
            owner_id=owner.id,
            client_id="chatgpt:v3",
        )
        model_item = _guidance_deliberation(
            db, owner, key="classification", scope="model", target_model="gpt"
        )
        record_resolution(
            db,
            DeliberationResolutionCreate(
                deliberation_id=model_item.id,
                resolution="ChatGPT-specific classification guidance.",
                user_approved=True,
            ),
            owner_id=owner.id,
            client_id="chatgpt:v3",
        )

        chatgpt = get_induction(db, owner_id=owner.id, source_model="chatgpt")
        chatgpt_value = next(
            row for row in chatgpt["effective_guidance"] if row["key"] == "classification"
        )
        assert chatgpt_value["text"] == "ChatGPT-specific classification guidance."
        assert len(chatgpt["approved_model_guidance"]) == 1

        claude = get_induction(db, owner_id=owner.id, source_model="claude")
        claude_value = next(
            row for row in claude["effective_guidance"] if row["key"] == "classification"
        )
        assert claude_value["text"] == "Global classification guidance."
        assert claude["approved_model_guidance"] == []


def test_vote_requires_structured_decision_and_reason():
    with pytest.raises(ValidationError):
        DeliberationContributionCreate(
            deliberation_id=uuid.uuid4(),
            contribution_type="vote",
            content="approve",
            evidence={"vote": "approve"},
        )
    with pytest.raises(ValidationError):
        DeliberationContributionCreate(
            deliberation_id=uuid.uuid4(),
            contribution_type="vote",
            content="maybe",
            evidence={"vote": "maybe", "reason": "Unsure."},
        )


def test_guidance_policy_exposes_get_induction_and_vote_once():
    tools = [
        {
            "name": "fetch",
            "securitySchemes": [{"type": "oauth2", "scopes": ["reviews:read"]}],
            "_meta": {"securitySchemes": [{"type": "oauth2", "scopes": ["reviews:read"]}]},
        },
        {
            "name": "submit_contribution",
            "description": "old",
            "inputSchema": {
                "properties": {"contribution_type": {"enum": ["proposal", "critique"]}}
            },
        },
        {"name": "create_deliberation", "description": "old"},
        {"name": "record_resolution", "description": "old"},
    ]
    apply_guidance_tool_policy(tools)
    apply_guidance_tool_policy(tools)
    names = [tool["name"] for tool in tools]
    assert names.count("get_induction") == 1
    submit = next(tool for tool in tools if tool["name"] == "submit_contribution")
    assert submit["inputSchema"]["properties"]["contribution_type"]["enum"].count("vote") == 1
