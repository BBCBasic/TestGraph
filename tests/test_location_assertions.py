import uuid
from datetime import datetime, timezone

import pytest

from app.api.mcp_v2 import SERVER_VERSION, TOOLS, _search
from app.core.security import Principal
from app.db.session import SessionLocal
from app.models.entities import User
from app.models.v2 import SubjectType, V2Subject
from app.schemas.v2 import LocationAssertionCreate, LocationResolutionCreate, PlaceReference
from app.services.location import (
    LocationError,
    assertions_for_subject,
    create_location_assertion,
    resolve_location_assertion,
)
from app.services.v2 import resolve_subject_type


def _user(db):
    user = User(display_name=f"location-{uuid.uuid4()}")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _subject(db, owner, *, kind=None, eligible=True, attributes=None):
    kind = kind or f"location-test-venue-{uuid.uuid4()}"
    subject_type = resolve_subject_type(db, kind)
    if subject_type is None:
        subject_type = SubjectType(
            canonical_name=kind,
            normalized_name=kind,
            description="Location assertion test type",
            status="provisional",
            public_location_eligible=eligible,
            created_by="pytest",
        )
        db.add(subject_type)
        db.flush()
    else:
        subject_type.public_location_eligible = eligible
    subject = V2Subject(
        subject_type_id=subject_type.id,
        owner_id=owner.id,
        name=f"Venue {uuid.uuid4()}",
        canonical_key=f"venue:{uuid.uuid4()}",
        identifiers_json={},
        attributes_json=attributes or {},
        provenance_json={},
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return subject


def _source(reference="official listing"):
    return {"reference": reference, "kind": "authoritative"}


def test_location_tools_are_published():
    names = {tool["name"] for tool in TOOLS}
    assert SERVER_VERSION == "3.19.0-alpha"
    assert {"assert_location", "get_location_assertions", "resolve_location_assertion"} <= names


def test_place_hierarchy_drives_location_search_without_guessing():
    with SessionLocal() as db:
        owner = _user(db)
        venue = _subject(db, owner)
        located = create_location_assertion(
            db,
            LocationAssertionCreate(
                subject_id=venue.id,
                predicate="located_in",
                object_place=PlaceReference(
                    name="Paintworks",
                    canonical_key=f"place:paintworks:{uuid.uuid4()}",
                    identifiers={"wikidata": f"Q{uuid.uuid4().int}"},
                    place_kind="neighbourhood",
                ),
                source=_source("official venue page"),
            ),
            owner_id=owner.id,
            client_id="claude:v3",
        )
        paintworks = db.get(V2Subject, located.object_subject_id)
        create_location_assertion(
            db,
            LocationAssertionCreate(
                subject_id=paintworks.id,
                predicate="contained_in",
                object_place=PlaceReference(
                    name="Bristol",
                    canonical_key=f"place:bristol:{uuid.uuid4()}",
                    identifiers={"wikidata": f"Q{uuid.uuid4().int}"},
                    place_kind="city",
                ),
                qualifiers={"scheme": "administrative"},
                source=_source("official boundary dataset"),
            ),
            owner_id=owner.id,
            client_id="claude:v3",
        )
        principal = Principal(
            subject="pytest", client_id="pytest",
            scopes={"reviews:read"}, user_id=owner.id,
        )
        result = _search(db, principal, {"query": "Bristol", "limit": 10})
        body = result["structuredContent"]
        match = next(item for item in body["known_subjects"] if item["id"] == str(venue.id))
        assert match["location_matches"][0]["route"] == "place_or_descendant"
        assert body["location_ambiguities"] == []


def test_contested_claim_requires_independent_resolution_or_user_approval():
    with SessionLocal() as db:
        owner = _user(db)
        venue = _subject(db, owner)
        first = create_location_assertion(
            db,
            LocationAssertionCreate(
                subject_id=venue.id, predicate="postcode", value="BS1 1AA",
                source=_source("source one"),
            ),
            owner_id=owner.id, client_id="claude:v3",
        )
        second = create_location_assertion(
            db,
            LocationAssertionCreate(
                subject_id=venue.id, predicate="postcode", value="BS2 2BB",
                source=_source("source two"), valid_from=datetime.now(timezone.utc),
            ),
            owner_id=owner.id, client_id="claude:v3",
        )
        assert first.conflict_state == "contested"
        assert second.conflict_state == "contested"
        with pytest.raises(LocationError) as denied:
            resolve_location_assertion(
                db,
                LocationResolutionCreate(
                    assertion_id=second.id, decision="accepted",
                    rationale="Preferred source", user_approved=False,
                ),
                owner_id=owner.id, client_id="claude:v3",
            )
        assert denied.value.code == "INDEPENDENT_RESOLUTION_REQUIRED"
        accepted = resolve_location_assertion(
            db,
            LocationResolutionCreate(
                assertion_id=second.id, decision="accepted",
                rationale="Independent verification", user_approved=False,
            ),
            owner_id=owner.id, client_id="chatgpt:v3",
        )
        assert accepted.conflict_state == "accepted"
        assert accepted.resolution_json["resolved_by_client"] == "chatgpt:v3"


def test_location_eligibility_wgs84_and_legacy_drift_are_enforced():
    with SessionLocal() as db:
        owner = _user(db)
        person = _subject(db, owner, kind=f"person-test-{uuid.uuid4()}", eligible=False)
        with pytest.raises(LocationError) as ineligible:
            create_location_assertion(
                db,
                LocationAssertionCreate(
                    subject_id=person.id, predicate="postcode", value="BS1 1AA",
                    source=_source(),
                ),
                owner_id=owner.id, client_id="claude:v3",
            )
        assert ineligible.value.code == "LOCATION_SUBJECT_INELIGIBLE"

        venue = _subject(
            db, owner, kind=f"drift-test-{uuid.uuid4()}", eligible=True,
            attributes={"postcode": "BS1 1AA"},
        )
        with pytest.raises(LocationError) as invalid_position:
            create_location_assertion(
                db,
                LocationAssertionCreate(
                    subject_id=venue.id, predicate="position",
                    value={"latitude": 91, "longitude": -2},
                    source=_source(),
                ),
                owner_id=owner.id, client_id="claude:v3",
            )
        assert invalid_position.value.code == "LOCATION_POSITION_OUT_OF_RANGE"

        create_location_assertion(
            db,
            LocationAssertionCreate(
                subject_id=venue.id, predicate="postcode", value="BS2 2BB",
                source=_source("new official listing"),
            ),
            owner_id=owner.id, client_id="claude:v3",
        )
        location = assertions_for_subject(db, venue.id, owner_id=owner.id)
        assert location["migration"]["legacy_fallback_removal_allowed"] is False
        assert location["migration"]["drift"][0]["state"] == "drift_contested"
