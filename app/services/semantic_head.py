from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.v2 import normalise_term


_DESCRIPTOR_WORDS: dict[str, set[str]] = {
    "material": {
        "straw", "wood", "wooden", "steel", "metal", "plastic", "rubber", "glass",
        "concrete", "stone", "paper", "cardboard", "aluminium", "aluminum", "cotton", "wool",
    },
    "arrangement": {
        "stack", "group", "bundle", "pile", "collection", "cluster", "batch", "heap", "row",
    },
    "condition": {
        "broken", "damaged", "faulty", "failed", "repaired", "used", "new", "wet", "dry",
        "empty", "full", "open", "closed",
    },
    "colour": {
        "red", "orange", "yellow", "green", "blue", "purple", "violet", "pink", "brown",
        "black", "white", "grey", "gray", "silver", "gold", "beige",
    },
    "size": {"tiny", "small", "medium", "large", "big", "huge", "compact", "mini", "giant"},
    "quantity": {"single", "double", "triple", "pair", "multiple", "several", "many"},
    "location": {"indoor", "outdoor", "upstairs", "downstairs", "local", "remote"},
}

_ARRANGEMENT_OF_RE = re.compile(
    r"^(?P<modifier>stack|group|bundle|pile|collection|cluster|batch|heap|row)\s+of\s+(?P<head>.+)$",
    re.IGNORECASE,
)
_DISTINCT_JUSTIFICATION_CUES = {
    "distinct", "identity", "behaviour", "behavior", "relationship", "classification",
    "retrieval", "function", "functionality", "role", "semantics", "meaning",
}


@dataclass(frozen=True)
class SemanticHeadAnalysis:
    original: str
    semantic_head: str
    modifiers: tuple[tuple[str, str], ...]

    @property
    def descriptor_derived(self) -> bool:
        return bool(self.modifiers)


def _singularise_phrase(value: str) -> str:
    words = value.split()
    if not words:
        return value
    last = words[-1]
    if len(last) > 3 and last.endswith("ies"):
        last = last[:-3] + "y"
    elif len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us", "is")):
        last = last[:-1]
    words[-1] = last
    return " ".join(words)


def analyze_semantic_type_name(name: str) -> SemanticHeadAnalysis:
    """Conservatively identify obvious descriptor-derived type phrases.

    This is intentionally a high-precision guard, not a general English parser. It only blocks
    phrases that match generic descriptor categories strongly enough that the server can safely
    say the modifier belongs in attributes/relationships instead of the type hierarchy.
    """
    original = str(name).strip()
    normalized = normalise_term(original)
    modifiers: list[tuple[str, str]] = []

    arrangement_of = _ARRANGEMENT_OF_RE.match(original.strip())
    if arrangement_of:
        modifier = normalise_term(arrangement_of.group("modifier"))
        head = _singularise_phrase(normalise_term(arrangement_of.group("head")))
        return SemanticHeadAnalysis(original, head, ((modifier, "arrangement"),))

    words = normalized.split()
    if len(words) < 2:
        return SemanticHeadAnalysis(original, normalized, ())

    # Arrangement nouns at the right edge normally describe how instances are grouped.
    while len(words) > 1 and words[-1] in _DESCRIPTOR_WORDS["arrangement"]:
        modifiers.append((words.pop(), "arrangement"))

    # Strong adjectival/prefix modifiers describe the remaining entity rather than replacing it.
    changed = True
    while len(words) > 1 and changed:
        changed = False
        first = words[0]
        for category in ("material", "condition", "colour", "size", "quantity", "location"):
            if first in _DESCRIPTOR_WORDS[category]:
                modifiers.append((words.pop(0), category))
                changed = True
                break

    # Explicit "X for Y" constructions express use/purpose, not a new entity class, unless justified.
    if "for" in words[1:-1]:
        idx = words.index("for")
        purpose = " ".join(words[idx + 1 :])
        modifiers.append((purpose, "purpose/use"))
        words = words[:idx]

    head = _singularise_phrase(" ".join(words))
    return SemanticHeadAnalysis(original, head, tuple(modifiers))


def _justifies_distinct_class(justification: str | None) -> bool:
    text = str(justification or "").strip().casefold()
    if len(text) < 20:
        return False
    words = set(re.findall(r"[a-z]+", text))
    return bool(words & _DISTINCT_JUSTIFICATION_CUES)


def validate_semantic_type_name(
    name: str,
    *,
    distinct_class_justification: str | None = None,
) -> SemanticHeadAnalysis:
    analysis = analyze_semantic_type_name(name)
    if not analysis.descriptor_derived or _justifies_distinct_class(distinct_class_justification):
        return analysis

    detail = ", ".join(f"'{value}' looks like {category}" for value, category in analysis.modifiers)
    categories = ", ".join(sorted({category for _, category in analysis.modifiers}))
    raise ValueError(
        f"Semantic head validation failed for '{analysis.original}': the fundamental entity appears to be "
        f"'{analysis.semantic_head}', while {detail}. Represent {categories} as an attribute or relationship "
        "instead of a subject type. If the combined phrase is genuinely a distinct semantic class, supply "
        "semantic_justification explaining its materially different identity, behaviour, relationships, "
        "classification meaning, or retrieval needs."
    )
