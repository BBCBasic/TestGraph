from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable


@dataclass(frozen=True)
class Submission:
    """One synthetic AI concept proposal.

    semantic_key groups proposals that mean the same thing even when clients choose
    different roots. expected_path is the desired eventual canonical location.
    """

    name: str
    proposed_path: str
    semantic_key: str
    expected_path: str


@dataclass
class Decision:
    status: str
    submitted_path: str
    resolved_path: str | None
    reason: str


@dataclass
class SimState:
    active_paths: set[str] = field(default_factory=set)
    semantic_paths: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(self, semantic_key: str, path: str) -> None:
        segments = path.split(".")
        for index in range(1, len(segments) + 1):
            self.active_paths.add(".".join(segments[:index]))
        self.semantic_paths[semantic_key].add(path)

    def locations_for_word(self, word: str) -> set[str]:
        locations: set[str] = set()
        for path in self.active_paths:
            if word in path.split("."):
                locations.add(path)
        return locations


Rule = Callable[[SimState, Submission], Decision]


def _normalise(path: str) -> str:
    return ".".join(part.strip().lower() for part in path.replace("_", ".").replace("-", ".").split(".") if part.strip())


def naive_rule(state: SimState, submission: Submission) -> Decision:
    path = _normalise(submission.proposed_path)
    if path in state.active_paths:
        return Decision("existing", path, path, "Exact path already exists.")
    return Decision("new", path, path, "Accept the submitted path unchanged.")


def word_first_rule(state: SimState, submission: Submission) -> Decision:
    """Vocabulary-first placement without the domain-root requirement."""
    path = _normalise(submission.proposed_path)
    segments = path.split(".")
    if path in state.active_paths:
        return Decision("existing", path, path, "Exact path already exists.")

    for anchor_index in range(max(len(segments) - 2, 0), 0, -1):
        anchor = segments[anchor_index]
        candidates: set[str] = set()
        for location in state.locations_for_word(anchor):
            parts = location.split(".")
            try:
                location_index = parts.index(anchor)
            except ValueError:
                continue
            candidates.add(".".join(parts[: location_index + 1] + segments[anchor_index + 1 :]))
        if len(candidates) == 1:
            resolved = next(iter(candidates))
            return Decision("reuse", path, resolved, f"Reuse established word '{anchor}'.")
        if len(candidates) > 1:
            return Decision("review", path, None, f"Word '{anchor}' exists in multiple locations: {sorted(candidates)}")

    return Decision("new", path, path, "No existing vocabulary word can anchor the path.")


def rooted_word_first_rule(state: SimState, submission: Submission) -> Decision:
    """Current intended policy: vocabulary first plus meaningful domain roots."""
    path = _normalise(submission.proposed_path)
    segments = path.split(".")
    if path in state.active_paths:
        return Decision("existing", path, path, "Exact path already exists.")
    if segments and segments[-1] == "review" and len(segments) < 3:
        return Decision("revise", path, None, "Review paths require domain.subject.review or deeper.")
    return word_first_rule(state, Submission(submission.name, path, submission.semantic_key, submission.expected_path))


RULES: dict[str, Rule] = {
    "naive": naive_rule,
    "word_first": word_first_rule,
    "rooted_word_first": rooted_word_first_rule,
}


DEFAULT_SCENARIOS: list[Submission] = [
    Submission("recipe first", "food.recipe.review", "recipe_review", "food.recipe.review"),
    Submission("recipe alternative root", "dining.recipe.review", "recipe_review", "food.recipe.review"),
    Submission("recipe shallow", "recipe.review", "recipe_review", "food.recipe.review"),
    Submission("restaurant", "dining.restaurant.review", "restaurant_review", "dining.restaurant.review"),
    Submission("gym", "fitness.gym.review", "gym_review", "fitness.gym.review"),
    Submission("hotel", "hospitality.hotel.review", "hotel_review", "hospitality.hotel.review"),
    Submission("park", "leisure.park.review", "park_review", "leisure.park.review"),
    Submission("bookshop", "retail.bookshop.review", "bookshop_review", "retail.bookshop.review"),
    Submission("train station", "transportation.train.station.review", "train_station_review", "transportation.train.station.review"),
    Submission("radio station", "media.radio.station.review", "radio_station_review", "media.radio.station.review"),
]


def run_sequence(rule: Rule, submissions: Iterable[Submission]) -> dict:
    state = SimState()
    decisions: list[dict] = []
    wrong = 0
    reviews = 0
    revisions = 0

    for submission in submissions:
        decision = rule(state, submission)
        if decision.status == "review":
            reviews += 1
        if decision.status == "revise":
            revisions += 1
        if decision.resolved_path is not None:
            if decision.resolved_path != submission.expected_path:
                wrong += 1
            state.add(submission.semantic_key, decision.resolved_path)
        decisions.append({
            "name": submission.name,
            "proposed": submission.proposed_path,
            "expected": submission.expected_path,
            "status": decision.status,
            "resolved": decision.resolved_path,
            "reason": decision.reason,
        })

    duplicate_semantics = sum(max(len(paths) - 1, 0) for paths in state.semantic_paths.values())
    roots = {path.split(".")[0] for path in state.active_paths}
    leaf_paths = {path for paths in state.semantic_paths.values() for path in paths}
    return {
        "wrong_placements": wrong,
        "duplicate_semantic_paths": duplicate_semantics,
        "human_reviews": reviews,
        "revision_requests": revisions,
        "root_count": len(roots),
        "leaf_count": len(leaf_paths),
        "roots": sorted(roots),
        "leaf_paths": sorted(leaf_paths),
        "decisions": decisions,
    }


def compare_rules(submissions: list[Submission], runs: int, seed: int) -> dict:
    rng = random.Random(seed)
    aggregate: dict[str, dict[str, float]] = {}
    signatures: dict[str, set[tuple[str, ...]]] = defaultdict(set)

    for name, rule in RULES.items():
        totals = defaultdict(float)
        for _ in range(runs):
            shuffled = list(submissions)
            rng.shuffle(shuffled)
            result = run_sequence(rule, shuffled)
            for metric in ("wrong_placements", "duplicate_semantic_paths", "human_reviews", "revision_requests", "root_count", "leaf_count"):
                totals[metric] += result[metric]
            signatures[name].add(tuple(result["leaf_paths"]))
        aggregate[name] = {
            metric: round(value / runs, 3)
            for metric, value in totals.items()
        }
        aggregate[name]["order_variants"] = len(signatures[name])
        aggregate[name]["score"] = round(
            100
            - 20 * aggregate[name]["wrong_placements"]
            - 15 * aggregate[name]["duplicate_semantic_paths"]
            - 4 * aggregate[name]["human_reviews"]
            - 2 * aggregate[name]["revision_requests"]
            - 3 * max(aggregate[name]["order_variants"] - 1, 0),
            3,
        )

    winner = max(aggregate, key=lambda key: aggregate[key]["score"])
    return {"runs": runs, "seed": seed, "winner": winner, "strategies": aggregate}


def _load_scenarios(path: str | None) -> list[Submission]:
    if not path:
        return list(DEFAULT_SCENARIOS)
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return [Submission(**item) for item in raw]


def _print_comparison(comparison: dict) -> None:
    metrics = [
        "score",
        "wrong_placements",
        "duplicate_semantic_paths",
        "human_reviews",
        "revision_requests",
        "root_count",
        "order_variants",
    ]
    print(f"Rule simulation: {comparison['runs']} shuffled runs, seed={comparison['seed']}")
    print(f"Winner: {comparison['winner']}\n")
    header = ["strategy", *metrics]
    widths = {key: max(len(key), *(len(str(values.get(key, ""))) for values in comparison["strategies"].values())) for key in metrics}
    widths["strategy"] = max(len("strategy"), *(len(name) for name in comparison["strategies"]))
    print("  ".join(key.ljust(widths[key]) for key in header))
    print("  ".join("-" * widths[key] for key in header))
    for name, values in comparison["strategies"].items():
        row = {"strategy": name, **values}
        print("  ".join(str(row.get(key, "")).ljust(widths[key]) for key in header))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare TasteGraph concept-placement rule versions against synthetic submissions.")
    parser.add_argument("--strategy", choices=[*RULES, "all"], default="all")
    parser.add_argument("--runs", type=int, default=500, help="Number of shuffled-order runs when comparing strategies.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenarios", help="Optional JSON file containing Submission objects.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    submissions = _load_scenarios(args.scenarios)
    if args.strategy == "all":
        output = compare_rules(submissions, max(args.runs, 1), args.seed)
    else:
        output = run_sequence(RULES[args.strategy], submissions)
        output["strategy"] = args.strategy

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    elif args.strategy == "all":
        _print_comparison(output)
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
