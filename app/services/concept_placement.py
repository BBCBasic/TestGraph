from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import Concept
from app.services.v2 import normalise_path
from app.services.vocabulary_governance import vocabulary_index


def _candidate_paths_for_segment(db: Session, segment: str) -> list[dict[str, Any]]:
    """Return active DNS positions for one proposed concept word.

    This deliberately uses the shared vocabulary index rather than scanning the tree in
    isolation. That makes an established meaning discoverable even when another AI has
    proposed the same word beneath a different root.
    """
    indexed = vocabulary_index(db, segment)
    return [
        match
        for match in indexed.get("matches", [])
        if match.get("kind") == "concept_path_segment"
        and match.get("status") == "active"
        and match.get("surface") == segment
    ]


def resolve_concept_path(db: Session, proposed_path: str) -> dict[str, Any]:
    """Resolve a proposed DNS concept path against the canonical vocabulary.

    Rules:
    * Existing canonical paths always win.
    * Review concepts must be domain rooted (at least ``domain.subject.review``).
    * Before creating new nodes, look up existing occurrences of the proposed words in
      the vocabulary index.
    * If one existing placement can carry the proposed suffix, reuse that placement.
    * If several established placements are plausible, return ``revise`` rather than
      creating another branch silently.
    * If no compatible word exists, keep the AI's sensible domain-rooted proposal.
    """
    path = normalise_path(proposed_path)
    segments = path.split(".") if path else []

    exact = db.scalar(
        select(Concept).where(Concept.path == path, Concept.status == "active")
    )
    if exact:
        return {
            "status": "existing",
            "path": exact.path,
            "submitted_path": path,
            "reason": "The proposed concept path is already canonical.",
            "candidates": [exact.path],
        }

    if segments and segments[-1] == "review" and len(segments) < 3:
        return {
            "status": "revise",
            "path": None,
            "submitted_path": path,
            "reason": (
                "Review concepts must start with a broad domain root before the subject; "
                "for example use food.recipe.review rather than recipe.review."
            ),
            "candidates": [],
        }

    # Work from the most specific meaningful word back towards the root. The first
    # segment is the proposed domain and 'review' is a record type, so neither should
    # drive semantic relocation.
    anchor_indexes = list(range(max(len(segments) - 2, 0), 0, -1))
    for anchor_index in anchor_indexes:
        anchor = segments[anchor_index]
        matches = _candidate_paths_for_segment(db, anchor)
        if not matches:
            continue

        recommendations: set[str] = set()
        for match in matches:
            candidate_segments = str(match["concept_path"]).split(".")
            segment_index = int(match["segment_index"])
            prefix = candidate_segments[: segment_index + 1]
            suffix = segments[anchor_index + 1 :]
            recommendations.add(".".join(prefix + suffix))

        if len(recommendations) == 1:
            recommended = recommendations.pop()
            if recommended != path:
                return {
                    "status": "reuse",
                    "path": recommended,
                    "submitted_path": path,
                    "reason": (
                        f"The word '{anchor}' already has one established DNS placement; "
                        f"reuse that vocabulary position instead of creating a parallel branch."
                    ),
                    "candidates": [recommended],
                    "matched_word": anchor,
                }
            break

        if len(recommendations) > 1:
            return {
                "status": "revise",
                "path": None,
                "submitted_path": path,
                "reason": (
                    f"The word '{anchor}' already exists in more than one canonical context. "
                    "Choose the compatible existing meaning instead of creating another branch."
                ),
                "candidates": sorted(recommendations),
                "matched_word": anchor,
            }

    return {
        "status": "new",
        "path": path,
        "submitted_path": path,
        "reason": "No compatible canonical vocabulary placement was found; the domain-rooted path may be extended.",
        "candidates": [],
    }


__all__ = ["resolve_concept_path"]
