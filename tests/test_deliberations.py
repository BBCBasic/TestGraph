import uuid

import pytest
from sqlalchemy import select

from app.api.mcp_v2 import SERVER_VERSION, TOOLS, _search
from app.core.security import Principal
from app.db.session import SessionLocal
from app.models.entities import User
from app.models.v2 import SubjectType, V2Experience, V2Subject
from app.schemas.deliberation import (
    DeliberationClaim,
    DeliberationContributionCreate,
    DeliberationCreate,
    DeliberationResolutionCreate,
)
from app.services.deliberation import (
    DeliberationError,
    claim_deliberation,
    create_deliberation,
    get_deliberation,
    list_open_deliberations,
    record_resolution,
    submit_contribution,
)
from app.services.write_safety import (
    IdempotencyKeyConflictError,
    begin_idempotent_write,
    finish_idempotent_write,
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


def test_open_inbox_claiming_and_machine_completion_verification():
    with SessionLocal() as db:
        owner = _user(db, "coordination-owner")
        subject_type = SubjectType(
            canonical_name=f"coordination-{uuid.uuid4()}",
            normalized_name=f"coordination-{uuid.uuid4()}",
            status="provisional", created_by="pytest",
        )
        db.add(subject_type); db.flush()
        subject = V2Subject(
            subject_type_id=subject_type.id, owner_id=owner.id,
            name="Coordination evidence", canonical_key=f"evidence:{uuid.uuid4()}",
        )
        db.add(subject); db.flush()
        experience = V2Experience(
            owner_id=owner.id, subject_id=subject.id,
            headline="Evidence", summary="Evidence", raw_text="Evidence",
            created_by_client="pytest:v3",
        )
        db.add(experience); db.commit()

        deliberation = create_deliberation(
            db,
            DeliberationCreate(
                canonical_key=f"claude-task:{uuid.uuid4()}",
                title="Claude task", question="Run one probe", target_model="claude",
                acceptance_criteria={
                    "probes_attempted": 1,
                    "search_query_log_required": True,
                    "exact_subject_followup_required": True,
                    "fetch_all_reviews_required": True,
                    "final_contribution_required": True,
                    "deterministic_idempotency_key_required": True,
                    "claim_required": True,
                    "conflicts_and_dates_preserved": True,
                },
            ),
            owner_id=owner.id, client_id="chatgpt:v3",
        )
        listed = list_open_deliberations(
            db, owner_id=owner.id, target_model="claude", unclaimed_only=True
        )
        assert [item["id"] for item in listed] == [str(deliberation.id)]

        claimed = claim_deliberation(
            db, DeliberationClaim(deliberation_id=deliberation.id, source_model="claude-sonnet"),
            owner_id=owner.id, client_id="claude:v3",
        )
        assert claimed.claimed_by_client == "claude:v3"
        assert list_open_deliberations(
            db, owner_id=owner.id, target_model="claude", unclaimed_only=True
        ) == []
        with pytest.raises(DeliberationError) as conflict:
            claim_deliberation(
                db, DeliberationClaim(deliberation_id=deliberation.id, source_model="gpt"),
                owner_id=owner.id, client_id="other:v3",
            )
        assert conflict.value.code == "DELIBERATION_ALREADY_CLAIMED"

        contribution = submit_contribution(
            db,
            DeliberationContributionCreate(
                deliberation_id=deliberation.id, contribution_type="critique",
                content="Probe complete", source_model="claude-sonnet",
                evidence={"probe_log": [{
                    "queries": ["evidence"],
                    "exact_name_followups": ["Coordination evidence"],
                    "reviews_fetched": [str(experience.id)],
                    "subject_identities": [{
                        "subject_id": str(subject.id),
                        "subject_name": subject.name,
                        "subject_type": subject_type.canonical_name,
                        "review_count": 1,
                    }],
                }]},
            ),
            owner_id=owner.id, client_id="claude:v3",
            idempotency_key="claude-task-stable-1",
        )
        verification = contribution.verification_json
        assert verification["all_machine_checks_passed"] is True
        assert verification["machine_checks"]["referenced_reviews_exist"]["passed"] is True
        assert verification["machine_checks"]["claim_required"]["passed"] is True
        assert verification["not_machine_verifiable"] == ["conflicts_and_dates_preserved"]



def test_idempotency_replay_and_conflict_are_machine_verified_from_server_records():
    with SessionLocal() as db:
        owner = _user(db, "idempotency-verifier-owner")
        client_id = "claude:v3"
        deliberation = create_deliberation(
            db,
            DeliberationCreate(
                canonical_key=f"idempotency-verifier:{uuid.uuid4()}",
                title="Verify idempotency evidence",
                question="Can replay and conflict behavior be machine checked?",
                target_model="claude",
                acceptance_criteria={
                    "claim_required": True,
                    "identical_claim_replay_required": True,
                    "identical_contribution_replay_required": True,
                    "replay_identity_preserved": True,
                    "changed_payload_rejected": True,
                    "expected_error_code": "IDEMPOTENCY_KEY_CONFLICT",
                    "duplicate_contributions_for_replay_forbidden": True,
                    "final_contribution_required": True,
                    "task_discovered_via_open_inbox": True,
                    "writes_outside_deliberation_forbidden": True,
                },
            ),
            owner_id=owner.id,
            client_id="chatgpt:v3",
        )
        claim_deliberation(
            db,
            DeliberationClaim(
                deliberation_id=deliberation.id,
                source_model="claude-sonnet",
            ),
            owner_id=owner.id,
            client_id=client_id,
        )

        claim_key = "deliberation-claim:claude-claim-replay-test"
        claim_payload = {
            "deliberation_id": str(deliberation.id),
            "source_model": "claude-sonnet",
        }
        claim_hash, prior = begin_idempotent_write(
            db, client_id=client_id, key=claim_key, payload=claim_payload
        )
        assert prior is None
        finish_idempotent_write(
            db,
            client_id=client_id,
            key=claim_key,
            payload_hash=claim_hash,
            response_body={"claimed": True, "id": str(deliberation.id)},
        )
        _, claim_replay = begin_idempotent_write(
            db, client_id=client_id, key=claim_key, payload=claim_payload
        )
        assert claim_replay["id"] == str(deliberation.id)

        probe = submit_contribution(
            db,
            DeliberationContributionCreate(
                deliberation_id=deliberation.id,
                contribution_type="proposal",
                content="Idempotency replay probe payload A",
                confidence=0.9,
                source_model="claude-sonnet",
            ),
            owner_id=owner.id,
            client_id=client_id,
            idempotency_key="claude-idempotency-probe-test",
        )
        contribution_key = (
            "deliberation-contribution:claude-idempotency-probe-test"
        )
        contribution_payload = {
            "deliberation_id": str(deliberation.id),
            "contribution_type": "proposal",
            "content": "Idempotency replay probe payload A",
        }
        contribution_hash, prior = begin_idempotent_write(
            db,
            client_id=client_id,
            key=contribution_key,
            payload=contribution_payload,
        )
        assert prior is None
        response_body = {
            "saved": True,
            "deliberation_id": str(deliberation.id),
            "contribution": {"id": str(probe.id)},
        }
        finish_idempotent_write(
            db,
            client_id=client_id,
            key=contribution_key,
            payload_hash=contribution_hash,
            response_body=response_body,
        )
        _, replay = begin_idempotent_write(
            db,
            client_id=client_id,
            key=contribution_key,
            payload=contribution_payload,
        )
        assert replay["contribution"]["id"] == str(probe.id)

        with pytest.raises(IdempotencyKeyConflictError):
            begin_idempotent_write(
                db,
                client_id=client_id,
                key=contribution_key,
                payload={
                    **contribution_payload,
                    "content": "Idempotency replay probe payload B",
                },
            )

        final = submit_contribution(
            db,
            DeliberationContributionCreate(
                deliberation_id=deliberation.id,
                contribution_type="reconciliation",
                content="Server evidence should verify the replay and conflict.",
                source_model="claude-sonnet",
            ),
            owner_id=owner.id,
            client_id=client_id,
            idempotency_key="claude-idempotency-final-test",
        )
        verification = final.verification_json
        checks = verification["machine_checks"]
        assert verification["all_machine_checks_passed"] is True
        for criterion in (
            "identical_claim_replay_required",
            "identical_contribution_replay_required",
            "replay_identity_preserved",
            "changed_payload_rejected",
            "expected_error_code",
            "duplicate_contributions_for_replay_forbidden",
        ):
            assert checks[criterion]["passed"] is True
        assert checks["expected_error_code"]["observed"] == [
            "IDEMPOTENCY_KEY_CONFLICT"
        ]
        assert checks["identical_contribution_replay_required"][
            "contribution_ids"
        ] == [str(probe.id)]
        assert verification["not_machine_verifiable"] == [
            "task_discovered_via_open_inbox",
            "writes_outside_deliberation_forbidden",
        ]


def test_exact_followup_reuses_fully_retrieved_identity_and_names_missing_subjects():
    with SessionLocal() as db:
        owner = _user(db, "followup-owner")
        unique = uuid.uuid4().hex
        subject_type = SubjectType(
            canonical_name=f"followup-{unique}",
            normalized_name=f"followup-{unique}",
            status="provisional", created_by="pytest",
        )
        db.add(subject_type); db.flush()
        subject = V2Subject(
            subject_type_id=subject_type.id, owner_id=owner.id,
            name="Exact Subject", canonical_key=f"exact:{uuid.uuid4()}",
        )
        db.add(subject); db.flush()
        experience = V2Experience(
            owner_id=owner.id, subject_id=subject.id,
            headline="Evidence", summary="Evidence", raw_text="Evidence",
            created_by_client="pytest:v3",
        )
        db.add(experience); db.commit()

        deliberation = create_deliberation(
            db,
            DeliberationCreate(
                canonical_key=f"followup-task:{uuid.uuid4()}",
                title="Follow-up task", question="Reuse exact identity evidence",
                target_model="claude",
                acceptance_criteria={
                    "probes_attempted": 2,
                    "exact_subject_followup_required": True,
                    "claim_required": True,
                },
            ),
            owner_id=owner.id, client_id="chatgpt:v3",
        )
        claim_deliberation(
            db,
            DeliberationClaim(
                deliberation_id=deliberation.id, source_model="claude-sonnet"
            ),
            owner_id=owner.id, client_id="claude:v3",
        )
        identity = {
            "subject_id": str(subject.id),
            "subject_name": subject.name,
            "subject_type": subject_type.canonical_name,
            "review_count": 1,
        }
        complete = submit_contribution(
            db,
            DeliberationContributionCreate(
                deliberation_id=deliberation.id,
                contribution_type="critique",
                content="Earlier exact search reused",
                evidence={"probe_log": [
                    {
                        "queries": [{"query": subject.name}],
                        "exact_name_followups": [],
                        "reviews_fetched": [str(experience.id)],
                        "subject_identities": [identity],
                    },
                    {
                        "queries": [{"query": "Evidence"}],
                        "exact_name_followups": [],
                        "reviews_fetched": [str(experience.id)],
                        "subject_identities": [identity],
                    },
                ]},
                source_model="claude-sonnet",
            ),
            owner_id=owner.id, client_id="claude:v3",
        )
        check = complete.verification_json["machine_checks"][
            "exact_subject_followup_required"
        ]
        assert check["passed"] is True
        assert check["missing_followups"] == []
        assert complete.verification_json["machine_checks"]["claim_required"]["passed"] is True

        missing_id = uuid.uuid4()
        incomplete = submit_contribution(
            db,
            DeliberationContributionCreate(
                deliberation_id=deliberation.id,
                contribution_type="critique",
                content="Missing exact follow-up",
                evidence={"probe_log": [
                    {
                        "queries": [{"query": subject.name}],
                        "exact_name_followups": [],
                        "reviews_fetched": [str(experience.id)],
                        "subject_identities": [identity],
                    },
                    {
                        "queries": [{"query": "Evidence"}],
                        "exact_name_followups": [],
                        "reviews_fetched": [str(experience.id)],
                        "subject_identities": [{
                            "subject_id": str(missing_id),
                            "subject_name": "Unsearched Subject",
                            "subject_type": "software",
                            "review_count": 1,
                        }],
                    },
                ]},
                source_model="claude-sonnet",
            ),
            owner_id=owner.id, client_id="claude:v3",
        )
        check = incomplete.verification_json["machine_checks"][
            "exact_subject_followup_required"
        ]
        assert check["passed"] is False
        assert check["missing_followups"] == [{
            "probe_index": 1,
            "subject_id": str(missing_id),
            "subject_name": "Unsearched Subject",
            "subject_type": "software",
            "review_count": 1,
            "reviews_fetched_for_subject": 0,
            "reason": "exact_name_search_missing",
        }]



def test_gpt_and_chatgpt_share_one_open_inbox_target():
    with SessionLocal() as db:
        owner = _user(db, "target-alias-owner")
        deliberation = create_deliberation(
            db,
            DeliberationCreate(
                canonical_key=f"gpt-target:{uuid.uuid4()}",
                title="GPT target", question="Can ChatGPT find this?", target_model="gpt",
            ),
            owner_id=owner.id, client_id="claude:v3",
        )

        assert deliberation.target_model == "chatgpt"
        deliberation.target_model = "gpt"
        db.commit()
        for label in ("gpt", "chatgpt", "ChatGPT"):
            listed = list_open_deliberations(
                db, owner_id=owner.id, target_model=label, unclaimed_only=True
            )
            assert [item["id"] for item in listed] == [str(deliberation.id)]


def test_search_paginates_and_reports_same_name_identity_collision():
    with SessionLocal() as db:
        owner = _user(db, "search-owner")
        types = []
        for label in ("app", "software"):
            unique = uuid.uuid4().hex
            item = SubjectType(
                canonical_name=f"{label}-{unique}", normalized_name=f"{label}-{unique}",
                status="provisional", created_by="pytest",
            )
            db.add(item); types.append(item)
        db.flush()
        for index, subject_type in enumerate(types):
            subject = V2Subject(
                subject_type_id=subject_type.id, owner_id=owner.id,
                name="Same Name Product", canonical_key=f"same-name:{index}:{uuid.uuid4()}",
            )
            db.add(subject); db.flush()
            db.add(V2Experience(
                owner_id=owner.id, subject_id=subject.id,
                headline=f"Review {index}", summary="same name", raw_text="same name",
                created_by_client="pytest:v3",
            ))
        db.commit()
        principal = Principal(
            subject="pytest", client_id="pytest", scopes={"reviews:read"}, user_id=owner.id
        )
        first = _search(db, principal, {"query": "Same Name Product", "limit": 1})["structuredContent"]
        assert first["has_more"] is True
        assert first["next_cursor"]
        assert len(first["identity_collisions"]) == 1
        assert len(first["identity_collisions"][0]) == 2
        second = _search(db, principal, {
            "query": "Same Name Product", "limit": 1, "cursor": first["next_cursor"]
        })["structuredContent"]
        assert {first["results"][0]["id"], second["results"][0]["id"]}
        assert first["results"][0]["subject_id"] != second["results"][0]["subject_id"]


def test_deliberation_tools_publish_strict_schemas_and_new_version():
    tools = {tool["name"]: tool for tool in TOOLS}
    assert SERVER_VERSION == "3.20.0-alpha"
    assert {
        "create_deliberation",
        "get_deliberation",
        "list_open_deliberations",
        "claim_deliberation",
        "submit_contribution",
        "record_resolution",
    }.issubset(tools)
    assert tools["create_deliberation"]["inputSchema"]["additionalProperties"] is False
    assert tools["submit_contribution"]["inputSchema"]["properties"][
        "contribution_type"
    ]["enum"] == ["proposal", "critique", "counterproposal", "reconciliation", "vote"]
    assert "user_approved" in tools["record_resolution"]["inputSchema"]["required"]
    assert tools["get_deliberation"]["annotations"]["readOnlyHint"] is True
    assert "cursor" in tools["search"]["inputSchema"]["properties"]
    assert tools["claim_deliberation"]["annotations"]["readOnlyHint"] is False
