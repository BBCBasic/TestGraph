from tools.rule_simulator import (
    DEFAULT_SCENARIOS,
    Submission,
    compare_rules,
    rooted_word_first_rule,
    run_sequence,
)


def test_rooted_rule_reuses_existing_recipe_location():
    scenarios = [
        Submission("first", "food.recipe.review", "recipe", "food.recipe.review"),
        Submission("alternative", "dining.recipe.review", "recipe", "food.recipe.review"),
    ]
    result = run_sequence(rooted_word_first_rule, scenarios)

    assert result["wrong_placements"] == 0
    assert result["duplicate_semantic_paths"] == 0
    assert result["decisions"][1]["status"] == "reuse"
    assert result["decisions"][1]["resolved"] == "food.recipe.review"


def test_rooted_rule_refuses_shallow_review_path():
    scenarios = [
        Submission("shallow", "recipe.review", "recipe", "food.recipe.review"),
    ]
    result = run_sequence(rooted_word_first_rule, scenarios)

    assert result["revision_requests"] == 1
    assert result["decisions"][0]["resolved"] is None


def test_comparison_reports_order_stability_and_scores():
    comparison = compare_rules(DEFAULT_SCENARIOS, runs=50, seed=7)

    assert comparison["winner"] in comparison["strategies"]
    assert set(comparison["strategies"]) == {"naive", "word_first", "rooted_word_first"}
    assert comparison["strategies"]["naive"]["duplicate_semantic_paths"] > 0
    assert comparison["strategies"]["rooted_word_first"]["score"] > comparison["strategies"]["naive"]["score"]
