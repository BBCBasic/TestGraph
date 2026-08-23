from __future__ import annotations

import csv
import gzip
import html
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

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


@router.get("/view", response_class=HTMLResponse, include_in_schema=False)
def browser_view(q: str = "", recipe_code: str | None = None, stars: int | None = Query(None, ge=0, le=5)):
    rows, source, recipe_list, by_recipe = _index()
    needle = q.strip().lower()
    filtered = recipe_list if not needle else [r for r in recipe_list if needle in r["recipe_name"].lower()]
    if recipe_code is None and filtered:
        recipe_code = filtered[0]["recipe_code"]
    selected_rows = by_recipe.get(recipe_code or "", [])
    if stars is not None:
        selected_rows = [r for r in selected_rows if str(r.get("stars", "")) == str(stars)]
    selected_rows = selected_rows[:200]
    selected_name = selected_rows[0].get("recipe_name") if selected_rows else next((r["recipe_name"] for r in recipe_list if r["recipe_code"] == recipe_code), "Choose a recipe")

    recipe_links = []
    for item in filtered:
        params = {"recipe_code": item["recipe_code"]}
        if q:
            params["q"] = q
        if stars is not None:
            params["stars"] = str(stars)
        recipe_links.append(
            f'<a class="recipe{" active" if item["recipe_code"] == recipe_code else ""}" href="/tools/recipe-reviews/view?{urlencode(params)}">'
            f'<b>{html.escape(item["recipe_name"])}</b><span>{item["review_count"]} reviews</span></a>'
        )

    review_cards = []
    for i, row in enumerate(selected_rows):
        text = row.get("text", "")
        reviewer = row.get("user_name") or "Anonymous"
        rating = row.get("stars") or "Unrated"
        gpt = (
            f"Recipe: {selected_name}\nRecipe code: {recipe_code or ''}\nExternal human review\n"
            f"Source stars: {row.get('stars', '')}\nReviewer: {reviewer}\nComment ID: {row.get('comment_id', '')}\n\nReview:\n{text}"
        )
        review_cards.append(
            f'<article class="review"><div class="head"><b>{html.escape(str(rating))} star · {html.escape(reviewer)}</b></div>'
            f'<div class="text">{html.escape(text)}</div>'
            f'<textarea id="r{i}" class="copybox">{html.escape(text)}</textarea>'
            f'<textarea id="g{i}" class="copybox">{html.escape(gpt)}</textarea>'
            f'<div class="actions"><button type="button" onclick="copyBox(\'r{i}\')">Copy</button>'
            f'<button type="button" onclick="copyBox(\'g{i}\')">Copy for GPT</button></div></article>'
        )

    star_options = ['<option value="">All stars</option>'] + [
        f'<option value="{n}"{" selected" if stars == n else ""}>{n} stars</option>' for n in range(5, -1, -1)
    ]
    star_params = {"recipe_code": recipe_code or "", "q": q}
    return HTMLResponse(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UCI Recipe Reviews</title><style>
body{{font-family:system-ui;margin:0;background:#f5f6f8;color:#172033}}header{{position:sticky;top:0;background:#fff;padding:14px;border-bottom:1px solid #ddd;z-index:2}}h1{{font-size:22px;margin:0 0 10px}}input,select,button{{font:inherit;padding:10px;border:1px solid #bbb;border-radius:9px}}main{{display:grid;grid-template-columns:330px 1fr;gap:12px;padding:12px}}.panel{{background:#fff;border:1px solid #ddd;border-radius:12px;overflow:hidden}}.recipe{{display:block;padding:11px 13px;border-bottom:1px solid #eee;color:inherit;text-decoration:none}}.recipe.active{{background:#eef3ff}}.recipe span,.muted{{display:block;color:#6b7280;font-size:13px}}.list{{max-height:75vh;overflow:auto}}.toolbar{{padding:12px;border-bottom:1px solid #eee}}.review{{padding:14px;border-bottom:1px solid #eee}}.text{{white-space:pre-wrap;font-family:Georgia,serif;font-size:16px;line-height:1.55;margin-top:8px}}.actions{{display:flex;gap:8px;margin-top:10px}}.copybox{{position:absolute;left:-9999px;width:1px;height:1px}}form{{display:flex;gap:8px;flex-wrap:wrap}}form input{{flex:1;min-width:180px}}@media(max-width:800px){{main{{grid-template-columns:1fr}}.list{{max-height:300px}}}}
</style></head><body><header><h1>UCI Recipe Reviews</h1><form method="get" action="/tools/recipe-reviews/view"><input name="q" value="{html.escape(q)}" placeholder="Search recipes"><button>Search</button></form><div class="muted">{len(rows)} reviews · {len(recipe_list)} recipes · {html.escape(source)}</div></header><main>
<section class="panel"><div class="toolbar"><b>Recipes</b></div><div class="list">{''.join(recipe_links) or '<div class="toolbar muted">No recipes found.</div>'}</div></section>
<section class="panel"><div class="toolbar"><b>{html.escape(selected_name)}</b><form method="get" action="/tools/recipe-reviews/view" style="margin-top:8px"><input type="hidden" name="recipe_code" value="{html.escape(recipe_code or '')}"><input type="hidden" name="q" value="{html.escape(q)}"><select name="stars">{''.join(star_options)}</select><button>Filter</button></form><div class="muted">{len(selected_rows)} reviews shown</div></div>{''.join(review_cards) or '<div class="review muted">No reviews.</div>'}</section>
</main><script>async function copyBox(id){{const el=document.getElementById(id);try{{await navigator.clipboard.writeText(el.value)}}catch(e){{el.style.position='fixed';el.style.left='10px';el.style.top='10px';el.style.width='90vw';el.style.height='50vh';el.focus();el.select();document.execCommand('copy');el.style.position='absolute';el.style.left='-9999px';}}}}</script></body></html>''')
