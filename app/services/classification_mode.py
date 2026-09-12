from __future__ import annotations

from typing import Literal

from app.core.config import get_settings


ClassificationMode = Literal["legacy", "typed"]
LEGACY_TAXONOMY_RELATIONSHIP = "belongs_to"
TYPED_TAXONOMY_RELATIONSHIP = "is_a"
TYPED_CLASSIFICATION_RELATIONSHIPS = ("is_a", "part_of")
TYPED_REASONING_GUIDANCE = (
    "Typed classification separates two questions: what fundamentally is this thing, and what larger "
    "thing or system is it part of, if any? Use is_a for the first and part_of only when the second is "
    "semantically justified. Do not force either answer to imply the other, and do not force every type "
    "to have a part_of edge. Keep brand, quantity, colour, material, condition, size, location and purpose "
    "as attributes or relationships unless they genuinely define the semantic-head type."
)


def classification_mode() -> ClassificationMode:
    return get_settings().classification_mode


def taxonomy_relationship() -> str:
    if classification_mode() == "typed":
        return TYPED_TAXONOMY_RELATIONSHIP
    return LEGACY_TAXONOMY_RELATIONSHIP


def supported_classification_relationships() -> tuple[str, ...]:
    if classification_mode() == "typed":
        return TYPED_CLASSIFICATION_RELATIONSHIPS
    return (LEGACY_TAXONOMY_RELATIONSHIP,)


def validate_classification_relationship(relationship: str) -> str:
    relationship = relationship.strip().casefold().replace(" ", "_")
    if classification_mode() == "typed" and relationship not in TYPED_CLASSIFICATION_RELATIONSHIPS:
        raise ValueError(
            "Typed classification requires an explicit relationship; "
            "choose 'is_a' or 'part_of'."
        )
    return relationship
