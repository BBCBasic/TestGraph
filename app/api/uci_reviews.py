from __future__ import annotations

import csv
import gzip
import io
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/tools/recipe-reviews", tags=["UCI recipe reviews"])

FULL_DATASET = Path(__file__).parents[2] / "data" / "uci_recipe_reviews_full.csv.gz"
BUNDLED = Path(__file__).parents[2] / "data" / "uci_recipe_reviews_100.json"


def _normalise_key(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", "_").split())


def _normalise_row(row: dict[str, str]) -> dict[str, str]:
    return {_normalise_key(k): (v or "").strip() for k, v in row.items() if k is not None}


@lru_cache(maxsize=1)
def _full_rows() -> tuple[dict[str, str], ...]:
    """Read the vendored UCI CSV from the Railway deployment; never call UCI at request time."""
    if not FULL_DATASET.exists():
        return ()
    with gzip.open(FULL_DATASET, "rt", encoding="utf-8-sig", newline="") as handle:
        return tuple(_normalise_row(row) for row in csv.DictReader(handle))


def _bundled_rows() -> tuple[dict[str, str], ...]:
    if not BUNDLED.exists():
        return ()
    bundle = json.loads(BUNDLED.read_text(encoding="utf-8"))
    subjects = {item["id"]: item for item in bundle.get("subjects", [])}
    rows = []
    for exp in bundle.get("experiences", []):
        subject = subjects.get(exp.get("subject_id"), {})
        metadata = (exp.get("provenance") or {}).get("source_metadata") or {}
        rows.append({
            "recipe_code": str((subject.get("canonical_identifiers") or {}).get("uci_recipe_code", "")),
            "recipe_number": str((subject.get("metadata_json") or {}).get("recipe_number", "")),
            "recipe_name": subject.get("name", "Unknown recipe"),
            "comment_id": str((exp.get("provenance") or {}).get("source_record_id") or exp.get("id", "")),
            "user_name": "UCI reviewer",
            "stars": str(metadata.get("source_stars") or ""),
            "text": metadata.get("original_review_text") or exp.get("summary", ""),
            "thumbs_up": str(metadata.get("thumbs_up") or ""),
            "thumbs_down": str(metadata.get("thumbs_down") or ""),
            "reply_count": str(metadata.get("reply_count") or ""),
            "created_at": str((exp.get("provenance") or {}).get("source_created_at") or ""),
        })
    return tuple(rows)


def _rows() -> tuple[tuple[dict[str, str], ...], str]:
    full = _full_rows()
    if full:
        return full, "vendored-uci-911"
    fallback = _bundled_rows()
    if fallback:
        return fallback, "bundled-fallback"
    raise RuntimeError("No local UCI recipe review data is installed")


@router.get("/api/recipes")
def recipes(q: str = ""):
    try:
        rows, source = _rows()
    except Exception as exc:
        raise HTTPException(503, f"Could not load local recipe dataset: {exc}") from exc
    grouped: dict[str, dict] = {}
    needle = q.strip().lower()
    for row in rows:
        code = row.get("recipe_code") or row.get("recipe_number") or row.get("recipe_name")
        name = row.get("recipe_name") or f"Recipe {code}"
        if not code or (needle and needle not in name.lower()):
            continue
        item = grouped.setdefault(code, {
            "recipe_code": code,
            "recipe_name": name,
            "recipe_number": row.get("recipe_number"),
            "review_count": 0,
        })
        item["review_count"] += 1
    items = list(grouped.values())
    items.sort(key=lambda x: (
        int(x["recipe_number"]) if str(x.get("recipe_number", "")).isdigit() else 999999,
        x["recipe_name"],
    ))
    return {"count": len(items), "recipes": items, "source": source, "row_count": len(rows)}


@router.get("/api/reviews")
def reviews(
    recipe_code: str,
    stars: int | None = Query(None, ge=0, le=5),
    limit: int = Query(200, ge=1, le=500),
):
    try:
        rows, source = _rows()
    except Exception as exc:
        raise HTTPException(503, f"Could not load local recipe dataset: {exc}") from exc
    matches = []
    recipe_name = None
    for row in rows:
        code = row.get("recipe_code") or row.get("recipe_number") or row.get("recipe_name")
        if code != recipe_code:
            continue
        if stars is not None and str(row.get("stars", "")) != str(stars):
            continue
        recipe_name = row.get("recipe_name") or recipe_name
        matches.append({
            "comment_id": row.get("comment_id"),
            "user_name": row.get("user_name") or "Anonymous",
            "stars": int(row["stars"]) if str(row.get("stars", "")).isdigit() else None,
            "text": row.get("text", ""),
            "thumbs_up": int(row["thumbs_up"]) if str(row.get("thumbs_up", "")).isdigit() else None,
            "thumbs_down": int(row["thumbs_down"]) if str(row.get("thumbs_down", "")).isdigit() else None,
            "reply_count": int(row["reply_count"]) if str(row.get("reply_count", "")).isdigit() else None,
            "created_at": row.get("created_at"),
        })
        if len(matches) >= limit:
            break
    return {
        "recipe_code": recipe_code,
        "recipe_name": recipe_name,
        "count": len(matches),
        "reviews": matches,
        "source": source,
    }
