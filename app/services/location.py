from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.models.v2 import LocationAssertion, SubjectType, V2Subject, now_utc
from app.schemas.v2 import LocationAssertionCreate, LocationResolutionCreate, SubjectEnsure
from app.services.v2 import ensure_subject, resolve_subject_type

LOCATION_PREDICATES = {
    "located_in", "contained_in", "published_address", "postcode", "position",
}
ACTIVE_STATES = {"uncontested", "contested", "accepted"}
UK_POSTCODE_RE = re.compile(
    r"\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b", re.IGNORECASE
)


class LocationError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _normalise_postcode(value: str) -> dict[str, str]:
    text = " ".join(value.upper().split())
    compact = re.sub(r"\s+", "", text)
    if len(compact) >= 5:
        text = f"{compact[:-3]} {compact[-3:]}"
    return {"text": text, "normalized": compact}


def _normalise_value(
    predicate: str, value: Any, qualifiers: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    qualifiers = deepcopy(qualifiers or {})
    if predicate == "published_address":
        text = value.get("text") if isinstance(value, dict) else value
        text = str(text or "").strip()
        if not text:
            raise LocationError(
                "LOCATION_VALUE_REQUIRED", "published_address requires non-empty text"
            )
        return {"text": " ".join(text.split())}, qualifiers
    if predicate == "postcode":
        text = value.get("text") if isinstance(value, dict) else value
        text = str(text or "").strip()
        if not text:
            raise LocationError("LOCATION_VALUE_REQUIRED", "postcode requires text")
        return _normalise_postcode(text), qualifiers
    if predicate == "position":
        if not isinstance(value, dict):
            raise LocationError("LOCATION_POSITION_INVALID", "position must be an object")
        try:
            latitude = float(value["latitude"])
            longitude = float(value["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LocationError(
                "LOCATION_POSITION_INVALID",
                "position requires numeric latitude and longitude",
            ) from exc
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise LocationError(
                "LOCATION_POSITION_OUT_OF_RANGE",
                "WGS84 latitude or longitude is out of range",
                {"latitude": latitude, "longitude": longitude},
            )
        datum = str(value.get("datum", "WGS84")).upper()
        if datum != "WGS84":
            raise LocationError(
                "LOCATION_DATUM_UNSUPPORTED",
                "v1 position assertions require WGS84",
                {"datum": datum},
            )
        precision = value.get("precision_m", qualifiers.get("precision_m"))
        if precision is not None:
            try:
                precision = float(precision)
            except (TypeError, ValueError) as exc:
                raise LocationError(
                    "LOCATION_PRECISION_INVALID", "precision_m must be numeric"
                ) from exc
            if precision <= 0:
                raise LocationError(
                    "LOCATION_PRECISION_INVALID", "precision_m must be greater than zero"
                )
            qualifiers["precision_m"] = precision
        return {
            "latitude": latitude, "longitude": longitude, "datum": "WGS84",
        }, qualifiers
    return {}, qualifiers


def _assertion_hash(
    predicate: str,
    object_subject_id: uuid.UUID | None,
    value: dict[str, Any],
    qualifiers: dict[str, Any],
) -> str:
    material = {
        "predicate": predicate,
        "object_subject_id": str(object_subject_id) if object_subject_id else None,
        "value": value,
        "qualifiers": qualifiers,
    }
    return hashlib.sha256(_canonical_json(material).encode()).hexdigest()


def _source_valid(source: dict[str, Any]) -> bool:
    return isinstance(source, dict) and bool(source) and any(
        source.get(key) for key in ("url", "reference", "source_id")
    )


def _place_type(db: Session) -> SubjectType:
    place_type = resolve_subject_type(db, "place")
    if not place_type:
        raise LocationError(
            "PLACE_TYPE_MISSING",
            "The place subject type is not installed; apply the latest migration",
        )
    if not place_type.public_location_eligible:
        place_type.public_location_eligible = True
        db.flush()
    return place_type


def _identifier_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= _identifier_values(item)
    elif isinstance(value, list):
        for item in value:
            found |= _identifier_values(item)
    elif value is not None:
        found.add(str(value).strip().casefold())
    return {item for item in found if item}


def _resolve_or_create_place(
    db: Session,
    place: Any,
    *,
    client_id: str,
    source: dict[str, Any],
) -> V2Subject:
    place_type = _place_type(db)
    if place is None:
        raise LocationError(
            "PLACE_REQUIRED", "located_in and contained_in require an object_place"
        )
    if place.subject_id:
        subject = db.get(V2Subject, place.subject_id)
        if not subject or subject.deleted_at or subject.subject_type_id != place_type.id:
            raise LocationError(
                "PLACE_NOT_FOUND",
                "object_place.subject_id does not identify an active Place",
                {"subject_id": str(place.subject_id)},
            )
        return subject

    canonical_key = str(place.canonical_key or "").strip()
    name = str(place.name or "").strip()
    identifiers = place.identifiers or {}
    if not canonical_key or not name or not identifiers:
        raise LocationError(
            "PLACE_IDENTITY_INCOMPLETE",
            "A new Place requires name, canonical_key and at least one durable identifier",
        )

    exact = db.scalar(select(V2Subject).where(
        V2Subject.subject_type_id == place_type.id,
        V2Subject.canonical_key == canonical_key,
        V2Subject.deleted_at.is_(None),
    ))
    if exact:
        submitted = _identifier_values(identifiers)
        existing = _identifier_values(exact.identifiers_json or {})
        if submitted and existing and not submitted.intersection(existing):
            raise LocationError(
                "PLACE_CANONICAL_KEY_CONFLICT",
                "The canonical key already belongs to a Place with different identifiers",
                {"subject_id": str(exact.id), "canonical_key": canonical_key},
            )
        return exact

    submitted = _identifier_values(identifiers)
    identifier_matches = []
    name_matches = []
    for candidate in db.scalars(select(V2Subject).where(
        V2Subject.subject_type_id == place_type.id,
        V2Subject.deleted_at.is_(None),
    )).all():
        candidate_values = _identifier_values(candidate.identifiers_json or {})
        if submitted.intersection(candidate_values):
            identifier_matches.append(candidate)
        if candidate.name.strip().casefold() == name.casefold():
            name_matches.append(candidate)
    if len(identifier_matches) == 1:
        return identifier_matches[0]
    if len(identifier_matches) > 1 or name_matches:
        candidates = identifier_matches or name_matches
        raise LocationError(
            "PLACE_IDENTITY_AMBIGUOUS",
            "Existing Place candidates must be resolved before creating another Place",
            {"candidates": [{
                "subject_id": str(item.id),
                "name": item.name,
                "canonical_key": item.canonical_key,
                "identifiers": item.identifiers_json,
            } for item in candidates]},
        )

    return ensure_subject(
        db,
        payload=SubjectEnsure(
            subject_type="place",
            name=name,
            canonical_key=canonical_key,
            identifiers=identifiers,
            attributes={"place_kind": place.place_kind or "place"},
            provenance={"source": source, "created_as": "global_place"},
        ),
        client_id=client_id,
        owner_id=None,
        commit=False,
    )


def _overlaps(a: LocationAssertion, b: LocationAssertion) -> bool:
    a_start = a.valid_from or datetime.min.replace(tzinfo=timezone.utc)
    b_start = b.valid_from or datetime.min.replace(tzinfo=timezone.utc)
    a_end = a.valid_to or datetime.max.replace(tzinfo=timezone.utc)
    b_end = b.valid_to or datetime.max.replace(tzinfo=timezone.utc)
    return a_start <= b_end and b_start <= a_end


def _refresh_conflicts(db: Session, assertion: LocationAssertion) -> None:
    rows = list(db.scalars(select(LocationAssertion).where(
        LocationAssertion.subject_id == assertion.subject_id,
        LocationAssertion.predicate == assertion.predicate,
        LocationAssertion.owner_id == assertion.owner_id,
        LocationAssertion.conflict_state != "rejected",
    )).all())
    for row in rows:
        if row.conflict_state in {"accepted", "rejected"}:
            continue
        overlapping = [other for other in rows if other.id != row.id and _overlaps(row, other)]
        hashes = {row.claim_hash, *(other.claim_hash for other in overlapping)}
        if len(hashes) > 1:
            row.conflict_state = "contested"
        elif row.valid_to and any(
            other.valid_from and row.valid_to <= other.valid_from
            for other in rows if other.id != row.id
        ):
            row.conflict_state = "superseded"
        else:
            row.conflict_state = "uncontested"


def _would_cycle(
    db: Session,
    subject_id: uuid.UUID,
    parent_id: uuid.UUID,
    scheme: str,
    owner_id: uuid.UUID | None,
) -> bool:
    if subject_id == parent_id:
        return True
    frontier = {parent_id}
    visited: set[uuid.UUID] = set()
    while frontier:
        if subject_id in frontier:
            return True
        visited |= frontier
        rows = list(db.scalars(select(LocationAssertion).where(
            LocationAssertion.predicate == "contained_in",
            LocationAssertion.subject_id.in_(frontier),
            LocationAssertion.conflict_state.in_(ACTIVE_STATES),
            or_(LocationAssertion.owner_id == owner_id, LocationAssertion.owner_id.is_(None)),
        )).all())
        frontier = {
            row.object_subject_id for row in rows
            if row.object_subject_id
            and row.qualifiers_json.get("scheme", "administrative") == scheme
        } - visited
    return False


def create_location_assertion(
    db: Session,
    payload: LocationAssertionCreate,
    *,
    owner_id: uuid.UUID,
    client_id: str,
) -> LocationAssertion:
    subject = db.get(V2Subject, payload.subject_id)
    if not subject or subject.deleted_at:
        raise LocationError("SUBJECT_NOT_FOUND", "Subject not found")
    subject_type = db.get(SubjectType, subject.subject_type_id)
    place_type = resolve_subject_type(db, "place")
    if (
        subject_type and place_type and subject_type.id == place_type.id
        and not subject_type.public_location_eligible
    ):
        subject_type.public_location_eligible = True
        db.flush()
    if payload.predicate != "contained_in" and (
        not subject_type or not subject_type.public_location_eligible
    ):
        raise LocationError(
            "LOCATION_SUBJECT_INELIGIBLE",
            "This subject type is not eligible for public-location assertions in v1",
            {"subject_type": subject_type.canonical_name if subject_type else None},
        )
    predicate = payload.predicate
    if predicate not in LOCATION_PREDICATES:
        raise LocationError("LOCATION_PREDICATE_INVALID", "Unsupported location predicate")
    if not _source_valid(payload.source):
        raise LocationError(
            "LOCATION_SOURCE_REQUIRED",
            "Every assertion requires source.url, source.reference or source.source_id",
        )
    if payload.valid_from and payload.valid_to and payload.valid_from > payload.valid_to:
        raise LocationError(
            "LOCATION_VALIDITY_INVALID", "valid_from must not be after valid_to"
        )

    object_subject = None
    value: dict[str, Any] = {}
    qualifiers = deepcopy(payload.qualifiers or {})
    if predicate in {"located_in", "contained_in"}:
        object_subject = _resolve_or_create_place(
            db, payload.object_place, client_id=client_id, source=payload.source
        )
        if payload.value not in (None, {}):
            raise LocationError(
                "LOCATION_VALUE_FORBIDDEN",
                f"{predicate} uses object_place rather than value",
            )
        if predicate == "contained_in":
            place_type = _place_type(db)
            if subject.subject_type_id != place_type.id:
                raise LocationError(
                    "CONTAINMENT_SUBJECT_NOT_PLACE",
                    "contained_in requires the asserted subject to be a Place",
                )
            scheme = str(qualifiers.get("scheme", "administrative")).strip().casefold()
            if not scheme:
                raise LocationError(
                    "CONTAINMENT_SCHEME_REQUIRED", "contained_in requires a scheme"
                )
            qualifiers["scheme"] = scheme
            if _would_cycle(db, subject.id, object_subject.id, scheme, owner_id):
                raise LocationError(
                    "PLACE_CONTAINMENT_CYCLE",
                    "This contained_in assertion would create a cycle",
                    {"subject_id": str(subject.id), "parent_id": str(object_subject.id)},
                )
    else:
        if payload.object_place is not None:
            raise LocationError(
                "LOCATION_OBJECT_FORBIDDEN",
                f"{predicate} uses value rather than object_place",
            )
        value, qualifiers = _normalise_value(predicate, payload.value, qualifiers)

    claim_hash = _assertion_hash(
        predicate, object_subject.id if object_subject else None, value, qualifiers
    )
    existing = db.scalar(select(LocationAssertion).where(
        LocationAssertion.owner_id == owner_id,
        LocationAssertion.subject_id == subject.id,
        LocationAssertion.predicate == predicate,
        LocationAssertion.claim_hash == claim_hash,
        LocationAssertion.asserted_by_client == client_id,
        LocationAssertion.valid_from == payload.valid_from,
        LocationAssertion.valid_to == payload.valid_to,
    ))
    if existing:
        return existing

    item = LocationAssertion(
        owner_id=owner_id,
        subject_id=subject.id,
        predicate=predicate,
        object_subject_id=object_subject.id if object_subject else None,
        value_json=value,
        qualifiers_json=qualifiers,
        claim_hash=claim_hash,
        source_json=payload.source,
        asserted_by_client=client_id,
        observed_at=payload.observed_at or now_utc(),
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        conflict_state="uncontested",
        resolution_json={},
        visibility=payload.visibility,
    )
    db.add(item)
    db.flush()
    _refresh_conflicts(db, item)
    db.commit()
    db.refresh(item)
    return item


def resolve_location_assertion(
    db: Session,
    payload: LocationResolutionCreate,
    *,
    owner_id: uuid.UUID,
    client_id: str,
) -> LocationAssertion:
    item = db.get(LocationAssertion, payload.assertion_id)
    if not item or item.owner_id != owner_id:
        raise LocationError("LOCATION_ASSERTION_NOT_FOUND", "Location assertion not found")
    if item.conflict_state != "contested":
        raise LocationError(
            "LOCATION_ASSERTION_NOT_CONTESTED",
            "Only a contested assertion can be accepted or rejected",
            {"conflict_state": item.conflict_state},
        )
    if not payload.user_approved and item.asserted_by_client == client_id:
        raise LocationError(
            "INDEPENDENT_RESOLUTION_REQUIRED",
            "The client that submitted a contested claim cannot resolve it in its own favour",
        )
    item.conflict_state = payload.decision
    item.resolution_json = {
        "decision": payload.decision,
        "rationale": payload.rationale,
        "resolved_by_client": client_id,
        "user_approved": payload.user_approved,
        "resolved_at": now_utc().isoformat(),
    }
    db.commit()
    db.refresh(item)
    return item


def assertion_body(db: Session, item: LocationAssertion) -> dict[str, Any]:
    obj = db.get(V2Subject, item.object_subject_id) if item.object_subject_id else None
    return {
        "id": str(item.id),
        "owner_id": str(item.owner_id) if item.owner_id else None,
        "subject_id": str(item.subject_id),
        "predicate": item.predicate,
        "object_place": ({
            "subject_id": str(obj.id),
            "name": obj.name,
            "canonical_key": obj.canonical_key,
            "identifiers": obj.identifiers_json,
        } if obj else None),
        "value": item.value_json,
        "qualifiers": item.qualifiers_json,
        "source": item.source_json,
        "asserted_by_client": item.asserted_by_client,
        "observed_at": item.observed_at.isoformat(),
        "valid_from": item.valid_from.isoformat() if item.valid_from else None,
        "valid_to": item.valid_to.isoformat() if item.valid_to else None,
        "conflict_state": item.conflict_state,
        "resolution": item.resolution_json or None,
        "visibility": item.visibility,
        "created_at": item.created_at.isoformat(),
    }


def _legacy_claims(subject: V2Subject) -> dict[str, dict[str, Any]]:
    attributes = subject.attributes_json or {}
    result: dict[str, dict[str, Any]] = {}
    address = attributes.get("address")
    if isinstance(address, str) and address.strip():
        result["published_address"] = {"text": " ".join(address.split())}
        match = UK_POSTCODE_RE.search(address)
        if match:
            result["postcode"] = _normalise_postcode(match.group(0))
    postcode = attributes.get("postcode")
    if isinstance(postcode, str) and postcode.strip():
        result["postcode"] = _normalise_postcode(postcode)
    latitude = attributes.get("latitude")
    longitude = attributes.get("longitude")
    if latitude is not None and longitude is not None:
        try:
            result["position"] = {
                "latitude": float(latitude), "longitude": float(longitude), "datum": "WGS84"
            }
        except (TypeError, ValueError):
            pass
    return result


def assertions_for_subject(
    db: Session, subject_id: uuid.UUID, *, owner_id: uuid.UUID
) -> dict[str, Any]:
    subject = db.get(V2Subject, subject_id)
    if not subject or subject.deleted_at:
        raise LocationError("SUBJECT_NOT_FOUND", "Subject not found")
    rows = list(db.scalars(select(LocationAssertion).where(
        LocationAssertion.subject_id == subject_id,
        or_(LocationAssertion.owner_id == owner_id, LocationAssertion.owner_id.is_(None)),
    ).order_by(LocationAssertion.created_at, LocationAssertion.id)).all())
    legacy = _legacy_claims(subject)
    current: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.conflict_state in ACTIVE_STATES:
            current.setdefault(row.predicate, []).append(row.value_json)
    drift = []
    parity = []
    for predicate, legacy_value in legacy.items():
        values = current.get(predicate, [])
        if any(_canonical_json(item) == _canonical_json(legacy_value) for item in values):
            parity.append(predicate)
        elif values:
            drift.append({
                "predicate": predicate,
                "legacy_value": legacy_value,
                "assertion_values": values,
                "state": "drift_contested",
            })
    return {
        "subject_id": str(subject_id),
        "assertions": [assertion_body(db, row) for row in rows],
        "migration": {
            "legacy_fields_present": sorted(legacy),
            "parity": sorted(parity),
            "drift": drift,
            "legacy_fallback_removal_allowed": not drift,
        },
    }


def location_matches(
    db: Session, query: str, *, owner_id: uuid.UUID
) -> tuple[dict[uuid.UUID, list[dict[str, Any]]], list[dict[str, Any]]]:
    q = str(query or "").strip()
    if not q:
        return {}, []
    place_type = resolve_subject_type(db, "place")
    if not place_type:
        return {}, []
    pattern = f"%{q}%"
    places = list(db.scalars(select(V2Subject).where(
        V2Subject.subject_type_id == place_type.id,
        V2Subject.deleted_at.is_(None),
        or_(
            V2Subject.name.ilike(pattern),
            V2Subject.canonical_key.ilike(pattern),
            cast(V2Subject.identifiers_json, String).ilike(pattern),
            cast(V2Subject.attributes_json, String).ilike(pattern),
        ),
    )).all())
    ambiguities = []
    grouped: dict[str, list[V2Subject]] = {}
    for place in places:
        grouped.setdefault(place.name.casefold(), []).append(place)
    for name, candidates in grouped.items():
        if len(candidates) > 1:
            ambiguities.append({
                "query": q,
                "name": candidates[0].name,
                "candidates": [{
                    "subject_id": str(item.id),
                    "canonical_key": item.canonical_key,
                    "identifiers": item.identifiers_json,
                } for item in candidates],
                "rule": "Return candidates; do not choose by popularity or assertion count.",
            })

    place_ids = {item.id for item in places}
    frontier = set(place_ids)
    visited = set(place_ids)
    while frontier:
        children = list(db.scalars(select(LocationAssertion).where(
            LocationAssertion.predicate == "contained_in",
            LocationAssertion.object_subject_id.in_(frontier),
            LocationAssertion.conflict_state.in_(ACTIVE_STATES),
            or_(LocationAssertion.owner_id == owner_id, LocationAssertion.owner_id.is_(None)),
        )).all())
        new_ids = {
            row.subject_id for row in children
            if row.qualifiers_json.get("scheme", "administrative") == "administrative"
        } - visited
        visited |= new_ids
        frontier = new_ids
    place_ids = visited

    matches: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if place_ids:
        for assertion in db.scalars(select(LocationAssertion).where(
            LocationAssertion.predicate == "located_in",
            LocationAssertion.object_subject_id.in_(place_ids),
            LocationAssertion.conflict_state.in_(ACTIVE_STATES),
            or_(LocationAssertion.owner_id == owner_id, LocationAssertion.owner_id.is_(None)),
        )).all():
            matches.setdefault(assertion.subject_id, []).append({
                "route": "place_or_descendant",
                "assertion_id": str(assertion.id),
                "place_id": str(assertion.object_subject_id),
                "conflict_state": assertion.conflict_state,
            })

    for assertion in db.scalars(select(LocationAssertion).where(
        LocationAssertion.predicate.in_(["published_address", "postcode"]),
        LocationAssertion.conflict_state.in_(ACTIVE_STATES),
        or_(LocationAssertion.owner_id == owner_id, LocationAssertion.owner_id.is_(None)),
        cast(LocationAssertion.value_json, String).ilike(pattern),
    )).all():
        matches.setdefault(assertion.subject_id, []).append({
            "route": assertion.predicate,
            "assertion_id": str(assertion.id),
            "conflict_state": assertion.conflict_state,
        })
    return matches, ambiguities
