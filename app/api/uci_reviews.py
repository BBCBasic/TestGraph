from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
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


@lru_cache(maxsize=1)
def _index():
    rows, source = _rows()
    by_recipe: dict[str, list[dict[str, str]]] = defaultdict(list)
    recipes: dict[str, dict] = {}
    for row in rows:
        code = row.get("recipe_code") or row.get("recipe_number") or row.get("recipe_name")
        if not code:
            continue
        name = row.get("recipe_name") or f"Recipe {code}"
        by_recipe[code].append(row)
        item = recipes.setdefault(code, {
            "recipe_code": code,
            "recipe_name": name,
            "recipe_number": row.get("recipe_number"),
            "review_count": 0,
        })
        item["review_count"] += 1
    recipe_list = list(recipes.values())
    recipe_list.sort(key=lambda x: (
        int(x["recipe_number"]) if str(x.get("recipe_number", "")).isdigit() else 999999,
        x["recipe_name"],
    ))
    return rows, source, recipe_list, dict(by_recipe)


@router.get("/api/status")
def status():
    result = {
        "full_dataset_path": str(FULL_DATASET),
        "full_dataset_exists": FULL_DATASET.exists(),
        "full_dataset_bytes": FULL_DATASET.stat().st_size if FULL_DATASET.exists() else 0,
        "fallback_exists": BUNDLED.exists(),
    }
    if FULL_DATASET.exists():
        try:
            with gzip.open(FULL_DATASET, "rt", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                first = next(reader, None)
                result["gzip_ok"] = True
                result["headers"] = reader.fieldnames
                result["first_recipe_name"] = first.get("recipe_name") if first else None
        except Exception as exc:
            result["gzip_ok"] = False
            result["gzip_error"] = f"{type(exc).__name__}: {exc}"
    try:
        rows, source, recipe_list, _ = _index()
        result["load_ok"] = True
        result["source"] = source
        result["row_count"] = len(rows)
        result["recipe_count"] = len(recipe_list)
    except Exception as exc:
        result["load_ok"] = False
        result["load_error"] = f"{type(exc).__name__}: {exc}"
    return result


@router.get("/api/recipes")
def recipes(q: str = ""):
    try:
        rows, source, recipe_list, _ = _index()
    except Exception as exc:
        raise HTTPException(503, f"Could not load local recipe dataset: {exc}") from exc
    needle = q.strip().lower()
    items = recipe_list if not needle else [item for item in recipe_list if needle in item["recipe_name"].lower()]
    return {"count": len(items), "recipes": items, "source": source, "row_count": len(rows)}


@router.get("/api/reviews")
def reviews(
    recipe_code: str,
    stars: int | None = Query(None, ge=0, le=5),
    limit: int = Query(200, ge=1, le=500),
):
    try:
        _, source, _, by_recipe = _index()
    except Exception as exc:
        raise HTTPException(503, f"Could not load local recipe dataset: {exc}") from exc
    rows = by_recipe.get(recipe_code, [])
    matches = []
    recipe_name = rows[0].get("recipe_name") if rows else None
    for row in rows:
        if stars is not None and str(row.get("stars", "")) != str(stars):
            continue
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
