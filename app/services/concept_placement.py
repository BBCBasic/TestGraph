from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import Concept
from app.services.v2 import normalise_path
from app.services.vocabulary_governance import vocabulary_index


# Direct review subjects whose broad canonical domain is part of the durable taxonomy.
# These rules remove submission-order dependence for well-established concepts while
# leaving unknown subjects free to create a sensible new domain-rooted branch.
CANONICAL_ROOT_BY_SUBJECT: dict[str, str] = {
    "recipe": "food",
    "restaurant": "dining",
    "gym": "fitness",
    "hotel": "hospitality",
    "park": "leisure",
    "bookshop": "retail",
    "dentist": "healthcare",
    "ferry": "transportation",
}


def _candidate_paths_for_segment(db: Session, segment: str) -> list[dict[str, Any]]:
    """Return active DNS positions for one proposed concept word."""
    indexed = vocabulary_index(db, segment)
    return [
        match
        for match in indexed.get("matches", [])
        if match.get("kind") == "concept_path_segment"
        and match.get("status") == "active"
        and match.get("surface") == segment
    ]


def _result(*, status: str, submitted_path: str, path: str | None, reason: str,
            candidates: list[str] | None = None, matched_word: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "path": path,
        "submitted_path": submitted_path,
        "reason": reason,
        "candidates": candidates or [],
    }
    if matched_word:
        result["matched_word"] = matched_word
    return result


def resolve_concept_path(db: Session, proposed_path: str) -> dict[str, Any]:
    """Resolve a proposed DNS concept path against canonical vocabulary.

    Placement policy:
    * review concepts must be domain rooted;
    * known direct subjects use a stable canonical domain, independent of arrival order;
    * deeper repeated words are reused only when their immediate parent context matches;
    * an unclassified direct subject already used under another root is returned for
      revision rather than silently duplicated or blindly redirected;
    * otherwise a sensible domain-rooted proposal may extend the vocabulary.
    """
    submitted_path = normalise_path(proposed_path)
    segments = submitted_path.split(".") if submitted_path else []

    if segments and segments[-1] == "review" and len(segments) < 3:
        return _result(
            status="revise",
            submitted_path=submitted_path,
            path=None,
            reason=(
                "Review concepts must start with a broad domain root before the subject; "
                "for example use food.recipe.review rather than recipe.review."
            ),
        )

    # Stabilise well-known direct review subjects before consulting arrival-order state.
    # Example: dining.recipe.review becomes food.recipe.review even if dining.recipe was
    # submitted first. This prevents the first client from permanently choosing the root.
    path = submitted_path
    if len(segments) == 3 and segments[-1] == "review":
        subject = segments[1]
        canonical_root = CANONICAL_ROOT_BY_SUBJECT.get(subject)
        if canonical_root and segments[0] != canonical_root:
            segments = [canonical_root, subject, "review"]
            path = ".".join(segments)

    exact = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
    if exact:
        status = "existing" if path == submitted_path else "reuse"
        return _result(
            status=status,
            submitted_path=submitted_path,
            path=exact.path,
            reason=(
                "The proposed concept path is already canonical."
                if status == "existing"
                else f"Use the canonical domain for '{segments[1]}' instead of creating a parallel branch."
            ),
            candidates=[exact.path],
            matched_word=segments[1] if status == "reuse" else None,
        )

    if path != submitted_path:
        return _result(
            status="reuse",
            submitted_path=submitted_path,
            path=path,
            reason=f"Use the stable canonical domain for '{segments[1]}' instead of making placement depend on submission order.",
            candidates=[path],
            matched_word=segments[1],
        )

    # Deeper words such as station are only the same concept when their immediate parent
    # also agrees. train.station and radio.station therefore remain distinct meanings.
    for anchor_index in range(len(segments) - 2, 1, -1):
        anchor = segments[anchor_index]
        parent_word = segments[anchor_index - 1]
        recommendations: set[str] = set()
        for match in _candidate_paths_for_segment(db, anchor):
            candidate_segments = str(match["concept_path"]).split(".")
            segment_index = int(match["segment_index"])
            if segment_index < 1 or candidate_segments[segment_index - 1] != parent_word:
                continue
            prefix = candidate_segments[: segment_index + 1]
            suffix = segments[anchor_index + 1 :]
            recommendations.add(".".join(prefix + suffix))

        if len(recommendations) == 1:
            recommended = next(iter(recommendations))
            if recommended != path:
                return _result(
                    status="reuse",
                    submitted_path=submitted_path,
                    path=recommended,
                    reason=f"The words '{parent_word}.{anchor}' already have one compatible canonical placement.",
                    candidates=[recommended],
                    matched_word=anchor,
                )
        elif len(recommendations) > 1:
            return _result(
                status="revise",
                submitted_path=submitted_path,
                path=None,
                reason=f"The context '{parent_word}.{anchor}' exists in more than one canonical position; choose the intended existing meaning.",
                candidates=sorted(recommendations),
                matched_word=anchor,
            )

    # For unknown direct subjects, do not let a single earlier root silently win. If the
    # same subject already exists directly beneath another root, return the alternatives
    # to the submitting AI for a better proposal.
    if len(segments) == 3 and segments[-1] == "review":
        subject = segments[1]
        alternatives: set[str] = set()
        for match in _candidate_paths_for_segment(db, subject):
            candidate_segments = str(match["concept_path"]).split(".")
            segment_index = int(match["segment_index"])
            if segment_index != 1:
                continue
            alternatives.add(".".join(candidate_segments[:2] + ["review"]))
        alternatives.discard(path)
        if alternatives:
            return _result(
                status="revise",
                submitted_path=submitted_path,
                path=None,
                reason=(
                    f"The subject '{subject}' already exists directly beneath another domain. "
                    "Confirm the existing meaning or materially distinguish this new concept instead of relying on first-writer placement."
                ),
                candidates=sorted(alternatives),
                matched_word=subject,
            )

    return _result(
        status="new",
        submitted_path=submitted_path,
        path=path,
        reason="No compatible canonical vocabulary placement was found; the domain-rooted path may be extended.",
    )


def validate_review_save_path(db: Session, proposed_path: str) -> dict[str, Any]:
    """Require direct experiences to use a canonical review leaf.

    Saving is deliberately stricter than proposing. An active broad ancestor such as
    transportation is a vocabulary node, not a valid place to store a ferry review.
    """
    submitted_path = normalise_path(proposed_path)
    segments = submitted_path.split(".") if submitted_path else []
    if len(segments) < 3 or segments[-1] != "review":
        return _result(
            status="revise",
            submitted_path=submitted_path,
            path=None,
            reason=(
                "Direct review experiences must be saved at a specific domain-rooted "
                "review leaf such as transportation.ferry.review; broad ancestor "
                "concepts cannot store reviews."
            ),
        )

    placement = resolve_concept_path(db, submitted_path)
    if placement["status"] == "reuse" and placement["path"] != submitted_path:
        return _result(
            status="revise",
            submitted_path=submitted_path,
            path=None,
            reason=placement["reason"],
            candidates=[placement["path"]],
            matched_word=placement.get("matched_word"),
        )
    return placement


__all__ = [
    "CANONICAL_ROOT_BY_SUBJECT",
    "resolve_concept_path",
    "validate_review_save_path",
]
