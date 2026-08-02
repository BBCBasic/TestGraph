from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import urllib.request
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import Experience, Subject, User
from app.schemas.common import ExperienceCreate, SubjectCreate, UserCreate
from app.schemas.domains import RecipeReviewData


DATASET_ID = "uci-911"
DATASET_NAME = "Recipe Reviews and User Feedback"
DATASET_URL = "https://archive.ics.uci.edu/dataset/911/recipe%2Breviews%2Band%2Buser%2Bfeedback%2Bdataset"
DOWNLOAD_URL = "https://archive.ics.uci.edu/static/public/911/recipe%2Breviews%2Band%2Buser%2Bfeedback%2Bdataset.zip"
DATASET_DOI = "10.24432/C5FG95"
LICENSE = "CC BY 4.0"
ATTRIBUTION = "Ali, A., Matuszewski, S., & Czupyt, J. (2023). Recipe Reviews and User Feedback. UCI Machine Learning Repository. https://doi.org/10.24432/C5FG95"
NAMESPACE = uuid.UUID("9e49932c-81e4-46d1-92cd-817c17ba3ebf")


def stable_uuid(kind: str, source_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{DATASET_ID}:{kind}:{source_id}")


def normalise_key(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", "_").split())


def normalise_row(row: dict[str, str]) -> dict[str, str]:
    return {normalise_key(k): (v or "").strip() for k, v in row.items() if k is not None}


def first(row: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = row.get(name, "")
        if value != "":
            return value
    return default


def as_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def iso_from_unix(value: str) -> str | None:
    timestamp = as_int(value, -1)
    if timestamp < 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def open_source_csv(csv_path: Path | None) -> Iterable[dict[str, str]]:
    if csv_path:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)
        return

    request = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "TasteGraph/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        archive_bytes = response.read()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError("The UCI download did not contain a CSV file")
        with archive.open(csv_names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            yield from csv.DictReader(text)


def choose_rows(rows: Iterable[dict[str, str]], max_recipes: int, max_reviews_per_recipe: int) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    recipe_order: list[str] = []
    for raw in rows:
        row = normalise_row(raw)
        recipe_code = first(row, "recipe_code", "recipe_number", "recipe_name")
        if not recipe_code:
            continue
        if recipe_code not in grouped:
            if len(recipe_order) >= max_recipes:
                continue
            recipe_order.append(recipe_code)
        if len(grouped[recipe_code]) < max_reviews_per_recipe:
            grouped[recipe_code].append(row)
    return [row for recipe_code in recipe_order for row in grouped[recipe_code]]


INTERPRETATION_TERMS = re.compile(
    r"\b(easy|simple|quick|delicious|tasty|flavou?r|again|keeper|go-to|added|add|"
    r"substitut(?:e|ed)|replac(?:e|ed)|omit(?:ted)?|left out|cut back|followed|"
    r"directions|instructions|time|minutes?|hours?|difficult|hard|confus(?:ed|ing)|"
    r"missing|thick|thin|bland|dry|moist|sweet|spicy|rich)\b",
    re.IGNORECASE,
)


def information_score(row: dict[str, str]) -> int:
    """Prefer reviews that contain usable evidence without rewarding huge essays."""
    text = html.unescape(first(row, "text", "review", "comment"))
    rating_bonus = 50 if 1 <= as_int(first(row, "stars")) <= 5 else 0
    return len(INTERPRETATION_TERMS.findall(text)) * 30 + min(len(text), 700) - max(0, len(text) - 1200) + rating_bonus


def choose_representative_rows(rows: Iterable[dict[str, str]], max_reviews: int) -> list[dict[str, str]]:
    """Choose at most one evidence-rich review per recipe, in recipe rank order."""
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for raw in rows:
        row = normalise_row(raw)
        recipe_code = first(row, "recipe_code", "recipe_number", "recipe_name")
        if recipe_code:
            grouped[recipe_code].append(row)
    selected = [max(recipe_rows, key=information_score) for recipe_rows in grouped.values()]
    selected.sort(key=lambda row: as_int(first(row, "recipe_number"), 1_000_000))
    return selected[:max_reviews]


def sentences(text: str) -> list[str]:
    clean = " ".join(html.unescape(text).replace("\n", " ").split())
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()]


def evidence_sentence(parts: list[str], pattern: str) -> str | None:
    matcher = re.compile(pattern, re.IGNORECASE)
    return next((part for part in parts if matcher.search(part)), None)


def extract_modifications(parts: list[str]) -> list[str]:
    """Keep the reviewer's own evidence sentences instead of inventing changes."""
    modification = re.compile(
        r"\b(I|we)\s+(?:also\s+|only\s+|just\s+|did\s+not\s+|didn['’]t\s+)?"
        r"(?:added?|used?|substitut(?:e|ed)|replac(?:e|ed)|omit(?:ted)?|left out|cut back|"
        r"reduced?|increased?|doubled?|halved?|followed|baked?|cooked?|mixed?|served?|"
        r"thickened?|pureed?|blended?|changed?)\b",
        re.IGNORECASE,
    )
    return [part for part in parts if modification.search(part)][:8]


def infer_review_text(review_text: str) -> dict[str, Any]:
    """Populate only dimensions supported by an explicit, retained sentence."""
    parts = sentences(review_text)
    dimensions: dict[str, float | None] = {
        "flavour": None,
        "instruction_clarity": None,
        "preparation_time_accuracy": None,
        "ingredient_availability": None,
        "difficulty": None,
        "repeat_worthiness": None,
    }
    evidence: dict[str, str] = {}

    bad = evidence_sentence(parts, r"\b(terrible|flavou?rless|bland|not (?:very )?good|unappealing|too (?:sweet|salty|creamy)|dry tasting)\b")
    good = evidence_sentence(parts, r"\b(delicious|tasty|yummy|excellent|fantastic|amazing|phenomenal|great flavou?r|so good|loved it|perfect flavou?r)\b")
    if bad:
        dimensions["flavour"], evidence["flavour"] = 2.5, bad
    elif good:
        dimensions["flavour"], evidence["flavour"] = 9.0, good

    bad = evidence_sentence(parts, r"\b(directions? (?:were|was|are|is) (?:confusing|unclear)|confused|missing from (?:the )?(?:ingredients|directions)|misprint)\b")
    good = evidence_sentence(parts, r"\b(easy to follow|simple directions?|directions? (?:were|was) (?:clear|easy)|followed (?:the )?(?:recipe|directions) exactly)\b")
    if bad:
        dimensions["instruction_clarity"], evidence["instruction_clarity"] = 3.0, bad
    elif good:
        dimensions["instruction_clarity"], evidence["instruction_clarity"] = 9.0, good

    bad = evidence_sentence(parts, r"\b(time (?:is|was) (?:way )?off|took (?:much|far|\d+[^.]{0,20}) longer|hard pressed to prepare|prep(?:aration)? time[^.]{0,25}(?:wrong|inaccurate))\b")
    good = evidence_sentence(parts, r"\b(baking|bake|cooking|cook) time was exact\b")
    if bad:
        dimensions["preparation_time_accuracy"], evidence["preparation_time_accuracy"] = 2.5, bad
    elif good:
        dimensions["preparation_time_accuracy"], evidence["preparation_time_accuracy"] = 9.5, good

    bad = evidence_sentence(parts, r"\b(expensive|not inexpensive|hard to find|couldn['’]t find)\b")
    good = evidence_sentence(parts, r"\b(all the ingredients|ingredients (?:I|we) (?:usually )?(?:have|had) on hand|affordable|common ingredients)\b")
    if bad:
        dimensions["ingredient_availability"], evidence["ingredient_availability"] = 3.0, bad
    elif good:
        dimensions["ingredient_availability"], evidence["ingredient_availability"] = 9.0, good

    hard = evidence_sentence(parts, r"\b(labor-intensive|time consuming|took me a long time|difficult to|hard to (?:make|prepare|cut))\b")
    easy = evidence_sentence(parts, r"\b(easy|simple|quick) (?:to make|to prepare|recipe|and quick|meal|way)\b|\bwent together quickly\b")
    if hard:
        dimensions["difficulty"], evidence["difficulty"] = 8.0, hard
    elif easy:
        dimensions["difficulty"], evidence["difficulty"] = 2.0, easy

    no = evidence_sentence(parts, r"\b(?:will|would) never make (?:it|this)? ?again\b|\bnot worth (?:making|the effort)\b")
    yes = evidence_sentence(parts, r"\b(?:will|would|definitely|certainly|going to) (?:be )?(?:make|making|fix) (?:it|this)? ?again\b|\b(?:keeper|go-to|regular rotation|staple meal|family fav(?:ou?rite)?)\b")
    if no:
        dimensions["repeat_worthiness"], evidence["repeat_worthiness"] = 0.5, no
    elif yes:
        dimensions["repeat_worthiness"], evidence["repeat_worthiness"] = 9.5, yes

    modifications = extract_modifications(parts)
    impressions = []
    for category, statement in evidence.items():
        value = dimensions[category]
        sentiment = (0.8 if value is not None and value <= 3 else -0.7) if category == "difficulty" else ((value or 5) - 5) / 5
        impressions.append({"category": category, "statement": statement, "sentiment": round(sentiment, 2), "importance_to_reviewer": 0.7, "confidence": 0.9})
    positive = {key for key, value in dimensions.items() if value is not None and ((key == "difficulty" and value <= 3) or (key != "difficulty" and value >= 7))}
    negative = {key for key, value in dimensions.items() if value is not None and ((key == "difficulty" and value >= 7) or (key != "difficulty" and value <= 4))}
    return {
        "dimensions": dimensions,
        "modifications": modifications,
        "evidence": evidence,
        "impressions": impressions,
        "strengths": sorted(positive),
        "weaknesses": sorted(negative),
        "would_repeat": True if dimensions["repeat_worthiness"] and dimensions["repeat_worthiness"] >= 7 else (False if dimensions["repeat_worthiness"] is not None else None),
    }


def review_to_experience(row: dict[str, str], user_id: uuid.UUID, subject_id: uuid.UUID) -> dict[str, Any]:
    comment_id = first(row, "comment_id", default=f"row-{first(row, 'num_records', default='unknown')}")
    stars = as_int(first(row, "stars"), 0)
    review_text = html.unescape(first(row, "text", "review", "comment"))
    source_created_at = iso_from_unix(first(row, "created_at"))
    observations = []
    impressions = []
    overall_rating = None
    if 1 <= stars <= 5:
        overall_rating = float(stars * 2)
        observations.append({"category": "source_rating", "statement": f"Rated {stars} out of 5 stars.", "confidence": 1.0})
    interpretation = infer_review_text(review_text)
    impressions.extend(interpretation["impressions"])
    if overall_rating is not None:
        impressions.insert(0, {
            "category": "overall",
            "statement": f"The reviewer gave this recipe {stars} out of 5 stars.",
            "sentiment": (stars - 3) / 2,
            "importance_to_reviewer": 0.5,
            "confidence": 1.0,
        })
    domain_data = RecipeReviewData(
        overall_rating=overall_rating,
        **interpretation["dimensions"],
        modifications=interpretation["modifications"],
    ).model_dump(mode="json")
    inferred_fields = (["domain_data.overall_rating", "common_data.subjective_impressions.overall"] if overall_rating is not None else [])
    inferred_fields.extend(f"domain_data.{name}" for name, value in interpretation["dimensions"].items() if value is not None)
    if interpretation["modifications"]:
        inferred_fields.append("domain_data.modifications")
    payload = {
        "owner_id": user_id,
        "subject_id": subject_id,
        "subject_type": "recipe",
        "schema_version": "1.0",
        "visibility": "public",
        "headline": f"{stars}-star review" if stars else "Unrated review",
        "summary": review_text,
        "common_data": {
            "observations": observations,
            "subjective_impressions": impressions,
            "strengths": interpretation["strengths"],
            "weaknesses": interpretation["weaknesses"],
            "would_repeat": interpretation["would_repeat"],
            "special_journey_worthy": None,
            "confidence": {"overall_rating": 1.0 if overall_rating is not None else 0.0},
        },
        "domain_data": domain_data,
        "provenance": {
            "source_method": "licensed_dataset_import",
            "source_client": "tastegraph-uci-importer",
            "source_url": DATASET_URL,
            "source_record_id": comment_id,
            "source_created_at": source_created_at,
            "license": LICENSE,
            "attribution": ATTRIBUTION,
            "source_metadata": {
                "dataset_id": DATASET_ID,
                "dataset_doi": DATASET_DOI,
                "source_stars": stars if stars else None,
                "source_rating_scale": {"minimum": 1, "maximum": 5, "zero_means_unrated": True},
                "reply_count": as_int(first(row, "reply_count")),
                "thumbs_up": as_int(first(row, "thumbs_up")),
                "thumbs_down": as_int(first(row, "thumbs_down")),
                "best_score": as_int(first(row, "best_score")),
                "original_review_text": review_text,
                "interpretation_evidence": interpretation["evidence"],
            },
            "raw_conversation_stored": False,
            "raw_conversation_published": False,
            "inferred_fields": inferred_fields,
            "notes": "Evidence-grounded text interpretation v1. Only dimensions supported by an explicit source sentence are populated; unsupported dimensions remain null.",
        },
        "consent": {
            "user_approved": False,
            "authorization_basis": "licensed_source",
            "license_reference": f"{LICENSE}; {DATASET_DOI}",
        },
    }
    return ExperienceCreate.model_validate(payload).model_dump(mode="json")


def build_bundle(rows: list[dict[str, str]]) -> dict[str, Any]:
    users: dict[str, dict[str, Any]] = {}
    subjects: dict[str, dict[str, Any]] = {}
    experiences: list[dict[str, Any]] = []

    for row in rows:
        source_user_id = first(row, "user_id", default="unknown")
        recipe_code = first(row, "recipe_code", "recipe_number", "recipe_name")
        comment_id = first(row, "comment_id", default=f"row-{first(row, 'num_records', default='unknown')}")
        user_id = stable_uuid("user", source_user_id)
        subject_id = stable_uuid("recipe", recipe_code)
        experience_id = stable_uuid("review", comment_id)

        if source_user_id not in users:
            user_payload = UserCreate(
                display_name=first(row, "user_name", default=f"UCI user {source_user_id}"),
                bio="Reviewer imported from the UCI Recipe Reviews and User Feedback dataset.",
                profile_data={
                    "external_source": DATASET_ID,
                    "source_user_id": source_user_id,
                    "user_reputation": as_int(first(row, "user_reputation")),
                },
            ).model_dump(mode="json")
            users[source_user_id] = {"id": str(user_id), **user_payload}

        if recipe_code not in subjects:
            subject_payload = SubjectCreate(
                subject_type="recipe",
                name=first(row, "recipe_name", default=f"UCI recipe {recipe_code}"),
                canonical_key=f"uci-911-recipe-{recipe_code}",
                canonical_identifiers={"uci_recipe_code": recipe_code, "dataset_doi": DATASET_DOI},
                metadata_json={
                    "source_dataset": DATASET_NAME,
                    "recipe_number": as_int(first(row, "recipe_number")),
                    "source_url": DATASET_URL,
                    "license": LICENSE,
                    "attribution": ATTRIBUTION,
                },
            ).model_dump(mode="json")
            subjects[recipe_code] = {"id": str(subject_id), **subject_payload}

        experiences.append({
            "id": str(experience_id),
            "publication_status": "published",
            **review_to_experience(row, user_id, subject_id),
        })

    return {
        "format": "tastegraph-import-bundle",
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "id": DATASET_ID,
            "name": DATASET_NAME,
            "url": DATASET_URL,
            "doi": DATASET_DOI,
            "license": LICENSE,
            "attribution": ATTRIBUTION,
        },
        "counts": {"users": len(users), "subjects": len(subjects), "experiences": len(experiences)},
        "users": list(users.values()),
        "subjects": list(subjects.values()),
        "experiences": experiences,
    }


def load_bundle(bundle: dict[str, Any]) -> dict[str, int]:
    counts = {"users_added": 0, "subjects_added": 0, "experiences_added": 0, "existing_skipped": 0}
    db = SessionLocal()
    try:
        for item in bundle["users"]:
            item_id = uuid.UUID(item["id"])
            if db.get(User, item_id):
                counts["existing_skipped"] += 1
                continue
            payload = UserCreate.model_validate({k: v for k, v in item.items() if k != "id"})
            db.add(User(id=item_id, **payload.model_dump()))
            counts["users_added"] += 1
        db.flush()

        for item in bundle["subjects"]:
            item_id = uuid.UUID(item["id"])
            existing = db.get(Subject, item_id) or db.scalar(select(Subject).where(
                Subject.subject_type == item["subject_type"], Subject.canonical_key == item["canonical_key"]
            ))
            if existing:
                counts["existing_skipped"] += 1
                continue
            payload = SubjectCreate.model_validate({k: v for k, v in item.items() if k != "id"})
            db.add(Subject(id=item_id, **payload.model_dump()))
            counts["subjects_added"] += 1
        db.flush()

        for item in bundle["experiences"]:
            item_id = uuid.UUID(item["id"])
            if db.get(Experience, item_id):
                counts["existing_skipped"] += 1
                continue
            payload = ExperienceCreate.model_validate({
                k: v for k, v in item.items() if k not in {"id", "publication_status"}
            })
            values = payload.model_dump(exclude={"common_data", "provenance", "consent"})
            obj = Experience(
                id=item_id,
                **values,
                common_data=payload.common_data.model_dump(mode="json"),
                provenance=payload.provenance.model_dump(mode="json"),
                consent=payload.consent.model_dump(mode="json"),
                publication_status="published",
                published_at=datetime.now(timezone.utc),
                created_by_client="tastegraph-uci-importer",
                auth_subject="licensed-dataset:uci-911",
            )
            db.add(obj)
            counts["experiences_added"] += 1
        db.commit()
        return counts
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download, convert and optionally load UCI recipe reviews.")
    parser.add_argument("--csv", type=Path, help="Use an already-downloaded CSV instead of downloading from UCI.")
    parser.add_argument("--output", type=Path, default=Path("data/uci_recipe_reviews_100.json"))
    parser.add_argument("--max-recipes", type=int, default=100)
    parser.add_argument("--max-reviews-per-recipe", type=int, default=20)
    parser.add_argument("--representative-reviews", type=int, help="Choose this many evidence-rich reviews, at most one per recipe.")
    parser.add_argument("--load-bundle", type=Path, help="Load an existing TasteGraph JSON bundle without downloading UCI again.")
    parser.add_argument("--load", action="store_true", help="Load the converted bundle into the configured TasteGraph database.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.load_bundle:
        bundle = json.loads(args.load_bundle.read_text(encoding="utf-8"))
        print(json.dumps(load_bundle(bundle), indent=2))
        return
    if args.max_recipes < 1 or args.max_reviews_per_recipe < 1:
        raise SystemExit("Limits must be positive integers")
    source_rows = open_source_csv(args.csv)
    rows = (choose_representative_rows(source_rows, args.representative_reviews)
            if args.representative_reviews else choose_rows(source_rows, args.max_recipes, args.max_reviews_per_recipe))
    bundle = build_bundle(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {bundle['counts']['subjects']} recipes and {bundle['counts']['experiences']} reviews to {args.output}")
    if args.load:
        print(json.dumps(load_bundle(bundle), indent=2))


if __name__ == "__main__":
    main()
