from tools.rule_simulator import (
    DEFAULT_SCENARIOS,
    Submission,
    compare_rules,
    context_aware_rule,
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
    scenarios = [Submission("shallow", "recipe.review", "recipe", "food.recipe.review")]
    result = run_sequence(rooted_word_first_rule, scenarios)

    assert result["revision_requests"] == 1
    assert result["decisions"][0]["resolved"] is None


def test_context_rule_is_order_independent_for_recipe_root():
    forward = [
        Submission("good", "food.recipe.review", "recipe", "food.recipe.review"),
        Submission("alt", "dining.recipe.review", "recipe", "food.recipe.review"),
    ]
    reverse = list(reversed(forward))

    for scenarios in (forward, reverse):
        result = run_sequence(context_aware_rule, scenarios)
        assert result["wrong_placements"] == 0
        assert result["duplicate_semantic_paths"] == 0
        assert result["leaf_paths"] == ["food.recipe.review"]


def test_context_rule_distinguishes_same_word_with_different_parent():
    scenarios = [
        Submission("train", "transportation.train.station.review", "train_station", "transportation.train.station.review"),
        Submission("radio", "media.radio.station.review", "radio_station", "media.radio.station.review"),
    ]
    result = run_sequence(context_aware_rule, scenarios)

    assert result["wrong_placements"] == 0
    assert result["duplicate_semantic_paths"] == 0
    assert result["human_reviews"] == 0
    assert set(result["leaf_paths"]) == {
        "media.radio.station.review",
        "transportation.train.station.review",
    }


def test_context_rule_has_no_substantive_errors_on_default_scenarios():
    result = run_sequence(context_aware_rule, DEFAULT_SCENARIOS)

    assert result["wrong_placements"] == 0
    assert result["duplicate_semantic_paths"] == 0
    assert result["human_reviews"] == 0
    # The one revision is intentional: recipe.review is too shallow.
    assert result["revision_requests"] == 1


def test_comparison_selects_context_aware_rule():
    comparison = compare_rules(DEFAULT_SCENARIOS, runs=500, seed=7)

    assert set(comparison["strategies"]) == {
        "naive",
        "word_first",
        "rooted_word_first",
        "context_aware",
    }
    context = comparison["strategies"]["context_aware"]
    assert comparison["winner"] == "context_aware"
    assert context["wrong_placements"] == 0
    assert context["duplicate_semantic_paths"] == 0
    assert context["human_reviews"] == 0
    assert context["order_variants"] == 1
    assert context["revision_requests"] == 1
    assert context["score"] == 98.0
