from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable


@dataclass(frozen=True)
class Submission:
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

    def occurrences(self, word: str) -> list[tuple[list[str], int]]:
        found: list[tuple[list[str], int]] = []
        for path in self.active_paths:
            parts = path.split(".")
            for index, part in enumerate(parts):
                if part == word:
                    found.append((parts, index))
        return found


Rule = Callable[[SimState, Submission], Decision]

CANONICAL_ROOT_BY_SUBJECT: dict[str, str] = {
    "recipe": "food",
    "restaurant": "dining",
    "gym": "fitness",
    "hotel": "hospitality",
    "park": "leisure",
    "bookshop": "retail",
    "dentist": "healthcare",
}


def _normalise(path: str) -> str:
    return ".".join(
        part.strip().lower()
        for part in path.replace("_", ".").replace("-", ".").split(".")
        if part.strip()
    )


def naive_rule(state: SimState, submission: Submission) -> Decision:
    path = _normalise(submission.proposed_path)
    if path in state.active_paths:
        return Decision("existing", path, path, "Exact path already exists.")
    return Decision("new", path, path, "Accept the submitted path unchanged.")


def word_first_rule(state: SimState, submission: Submission) -> Decision:
    path = _normalise(submission.proposed_path)
    segments = path.split(".")
    if path in state.active_paths:
        return Decision("existing", path, path, "Exact path already exists.")

    for anchor_index in range(max(len(segments) - 2, 0), 0, -1):
        anchor = segments[anchor_index]
        candidates: set[str] = set()
        for parts, location_index in state.occurrences(anchor):
            candidates.add(".".join(parts[: location_index + 1] + segments[anchor_index + 1 :]))
        if len(candidates) == 1:
            resolved = next(iter(candidates))
            return Decision("reuse", path, resolved, f"Reuse established word '{anchor}'.")
        if len(candidates) > 1:
            return Decision("review", path, None, f"Word '{anchor}' exists in multiple locations.")

    return Decision("new", path, path, "No existing vocabulary word can anchor the path.")


def rooted_word_first_rule(state: SimState, submission: Submission) -> Decision:
    path = _normalise(submission.proposed_path)
    segments = path.split(".")
    if path in state.active_paths:
        return Decision("existing", path, path, "Exact path already exists.")
    if segments and segments[-1] == "review" and len(segments) < 3:
        return Decision("revise", path, None, "Review paths require domain.subject.review or deeper.")
    return word_first_rule(state, Submission(submission.name, path, submission.semantic_key, submission.expected_path))


def context_aware_rule(state: SimState, submission: Submission) -> Decision:
    """Stable root rules plus parent-context matching for repeated deeper words."""
    submitted = _normalise(submission.proposed_path)
    segments = submitted.split(".")
    if segments and segments[-1] == "review" and len(segments) < 3:
        return Decision("revise", submitted, None, "Review paths require a broad domain root.")

    path = submitted
    if len(segments) == 3 and segments[-1] == "review":
        subject = segments[1]
        canonical_root = CANONICAL_ROOT_BY_SUBJECT.get(subject)
        if canonical_root:
            path = f"{canonical_root}.{subject}.review"
            segments = path.split(".")

    if path in state.active_paths:
        return Decision("existing" if path == submitted else "reuse", submitted, path, "Use stable canonical placement.")

    if path != submitted:
        return Decision("reuse", submitted, path, "Use the subject's stable canonical domain.")

    # Deeper repeated words only match when the immediate parent also matches.
    for anchor_index in range(len(segments) - 2, 1, -1):
        anchor = segments[anchor_index]
        parent = segments[anchor_index - 1]
        candidates: set[str] = set()
        for parts, location_index in state.occurrences(anchor):
            if location_index < 1 or parts[location_index - 1] != parent:
                continue
            candidates.add(".".join(parts[: location_index + 1] + segments[anchor_index + 1 :]))
        if len(candidates) == 1:
            resolved = next(iter(candidates))
            return Decision("reuse", submitted, resolved, f"Reuse matching context '{parent}.{anchor}'.")
        if len(candidates) > 1:
            return Decision("review", submitted, None, f"Context '{parent}.{anchor}' is genuinely ambiguous.")

    # Unknown direct subjects that already exist under another root should be revised,
    # not silently duplicated or redirected by first-writer wins.
    if len(segments) == 3 and segments[-1] == "review":
        subject = segments[1]
        alternatives: set[str] = set()
        for parts, location_index in state.occurrences(subject):
            if location_index == 1:
                candidate = ".".join(parts[:2] + ["review"])
                if candidate != path:
                    alternatives.add(candidate)
        if alternatives:
            return Decision("revise", submitted, None, f"Subject '{subject}' already exists under another root.")

    return Decision("new", submitted, path, "No compatible canonical placement exists.")


RULES: dict[str, Rule] = {
    "naive": naive_rule,
    "word_first": word_first_rule,
    "rooted_word_first": rooted_word_first_rule,
    "context_aware": context_aware_rule,
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
    Submission("dentist", "healthcare.dentist.review", "dentist_review", "healthcare.dentist.review"),
    Submission("dentist alternate root", "services.dentist.review", "dentist_review", "healthcare.dentist.review"),
    Submission("train station", "transportation.train.station.review", "train_station_review", "transportation.train.station.review"),
    Submission("radio station", "media.radio.station.review", "radio_station_review", "media.radio.station.review"),
    Submission("car park", "transportation.car.park.review", "car_park_review", "transportation.car.park.review"),
    Submission("airport terminal", "transportation.airport.terminal.review", "airport_terminal_review", "transportation.airport.terminal.review"),
    Submission("computer terminal", "computing.terminal.review", "computer_terminal_review", "computing.terminal.review"),
    Submission("school gym", "education.school.gym.review", "school_gym_review", "education.school.gym.review"),
    Submission("hotel restaurant", "hospitality.hotel.restaurant.review", "hotel_restaurant_review", "hospitality.hotel.restaurant.review"),
]


def run_sequence(rule: Rule, submissions: Iterable[Submission]) -> dict:
    state = SimState()
    decisions: list[dict] = []
    wrong = 0
    reviews = 0
    revisions = 0

    for submission in submissions:
        decision = rule(state, submission)
        reviews += decision.status == "review"
        revisions += decision.status == "revise"
        if decision.resolved_path is not None:
            wrong += decision.resolved_path != submission.expected_path
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
    aggregate: dict[str, dict[str, float]] = {}
    signatures: dict[str, set[tuple[str, ...]]] = defaultdict(set)

    for rule_index, (name, rule) in enumerate(RULES.items()):
        rng = random.Random(seed + rule_index)
        totals = defaultdict(float)
        for _ in range(runs):
            shuffled = list(submissions)
            rng.shuffle(shuffled)
            result = run_sequence(rule, shuffled)
            for metric in ("wrong_placements", "duplicate_semantic_paths", "human_reviews", "revision_requests", "root_count", "leaf_count"):
                totals[metric] += result[metric]
            signatures[name].add(tuple(result["leaf_paths"]))
        aggregate[name] = {metric: round(value / runs, 3) for metric, value in totals.items()}
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
    metrics = ["score", "wrong_placements", "duplicate_semantic_paths", "human_reviews", "revision_requests", "root_count", "order_variants"]
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
    parser.add_argument("--runs", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenarios", help="Optional JSON file containing Submission objects.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    submissions = _load_scenarios(args.scenarios)
    output = compare_rules(submissions, max(args.runs, 1), args.seed) if args.strategy == "all" else run_sequence(RULES[args.strategy], submissions)
    if args.strategy != "all":
        output["strategy"] = args.strategy

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    elif args.strategy == "all":
        _print_comparison(output)
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
