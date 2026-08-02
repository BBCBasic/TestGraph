import csv
import json
from pathlib import Path

from scripts.import_uci_recipe_reviews import (
    build_bundle,
    choose_representative_rows,
    choose_rows,
    infer_review_text,
    load_bundle,
    open_source_csv,
)


def write_sample(path: Path):
    rows = [
        {"recipe_number": "1", "recipe_code": "101", "recipe_name": "Soup", "comment_id": "c1", "user_id": "u1", "user_name": "Alex", "user_reputation": "7", "created_at": "1700000000", "reply_count": "1", "thumbs_up": "3", "thumbs_down": "0", "stars": "5", "best_score": "9", "text": "Excellent soup."},
        {"recipe_number": "1", "recipe_code": "101", "recipe_name": "Soup", "comment_id": "c2", "user_id": "u2", "user_name": "Sam", "user_reputation": "2", "created_at": "1700000100", "reply_count": "0", "thumbs_up": "0", "thumbs_down": "1", "stars": "1", "best_score": "2", "text": "Not for me."},
        {"recipe_number": "2", "recipe_code": "202", "recipe_name": "Pie", "comment_id": "c3", "user_id": "u1", "user_name": "Alex", "user_reputation": "7", "created_at": "1700000200", "reply_count": "0", "thumbs_up": "2", "thumbs_down": "0", "stars": "0", "best_score": "4", "text": "Useful tip only."},
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_build_bundle_preserves_source_and_only_infers_supported_dimensions(tmp_path):
    source = tmp_path / "sample.csv"
    write_sample(source)
    rows = choose_rows(open_source_csv(source), max_recipes=2, max_reviews_per_recipe=1)
    bundle = build_bundle(rows)
    assert bundle["counts"] == {"users": 1, "subjects": 2, "experiences": 2}
    rated = bundle["experiences"][0]
    assert rated["domain_data"] == {"overall_rating": 10.0, "flavour": 9.0, "instruction_clarity": None, "preparation_time_accuracy": None, "ingredient_availability": None, "difficulty": None, "repeat_worthiness": None, "modifications": []}
    assert rated["provenance"]["source_metadata"]["source_stars"] == 5
    assert rated["consent"]["authorization_basis"] == "licensed_source"
    assert bundle["experiences"][1]["domain_data"]["overall_rating"] is None


def test_load_bundle_is_idempotent(tmp_path, migrate_db):
    source = tmp_path / "sample.csv"
    write_sample(source)
    bundle = build_bundle(choose_rows(open_source_csv(source), 2, 2))
    first = load_bundle(bundle)
    second = load_bundle(bundle)
    assert first["users_added"] == 2
    assert first["subjects_added"] == 2
    assert first["experiences_added"] == 3
    assert second["experiences_added"] == 0
    assert second["existing_skipped"] == 7


def test_interpretation_uses_explicit_evidence_and_leaves_unknowns_null():
    result = infer_review_text("Delicious and easy to make. I used turkey instead of beef. I will definitely make this again.")
    assert result["dimensions"]["flavour"] == 9.0
    assert result["dimensions"]["difficulty"] == 2.0
    assert result["dimensions"]["repeat_worthiness"] == 9.5
    assert result["dimensions"]["instruction_clarity"] is None
    assert result["would_repeat"] is True
    assert result["modifications"] == ["I used turkey instead of beef."]


def test_representative_selection_chooses_one_informative_review_per_recipe(tmp_path):
    source = tmp_path / "sample.csv"
    write_sample(source)
    rows = choose_representative_rows(open_source_csv(source), max_reviews=100)
    assert len(rows) == 2
    assert {row["recipe_code"] for row in rows} == {"101", "202"}
    assert rows[0]["comment_id"] == "c1"
