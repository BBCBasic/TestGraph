from __future__ import annotations

import argparse
import os
from pathlib import Path

from benchmarks.kill_test import load_cases
from benchmarks.providers import AnthropicProvider, OpenAIProvider
from benchmarks.runner import read_jsonl, run_cases, run_cases_from_collected, write_jsonl


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "kill_cases.json"


def provider_from_name(name: str, slot: str):
    slot = slot.upper()
    if name == "openai":
        model = os.getenv(f"KILL_TEST_{slot}_MODEL") or os.getenv("KILL_TEST_OPENAI_MODEL") or "gpt-5.6-terra"
        return OpenAIProvider(os.getenv("OPENAI_API_KEY", ""), model)
    if name == "anthropic":
        model = os.getenv(f"KILL_TEST_{slot}_MODEL") or os.getenv("KILL_TEST_ANTHROPIC_MODEL") or "claude-sonnet-5"
        return AnthropicProvider(os.getenv("ANTHROPIC_API_KEY", ""), model)
    raise ValueError(f"unsupported provider: {name}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the TestGraph hostile kill-test model stage")
    parser.add_argument("--regime", choices=["single", "simple", "testgraph-collect"], required=True)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--output", required=True)
    parser.add_argument("--audit-output", required=True)
    parser.add_argument("--collected-input", help="Reuse frozen testgraph-collect decisions for single/simple scoring")
    parser.add_argument("--first-provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--second-provider", choices=["openai", "anthropic"], default="anthropic")
    parser.add_argument("--resolver-provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--shakedown", action="store_true", help="Run the first 10 selected cases only")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    cases = load_cases(args.cases)
    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case.id in wanted]
        missing = wanted - {case.id for case in cases}
        if missing:
            raise SystemExit(f"Unknown case id(s): {', '.join(sorted(missing))}")
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be at least 1")
        cases = cases[: args.limit]

    if args.collected_input:
        if args.regime not in {"single", "simple"}:
            raise SystemExit("--collected-input is only valid for single or simple regimes")
        collected_rows = read_jsonl(args.collected_input)
        resolver = provider_from_name(args.resolver_provider, "resolver") if args.regime == "simple" else None
        records, audits = run_cases_from_collected(
            cases,
            args.regime,
            collected_rows,
            resolver=resolver,
            shakedown=10 if args.shakedown else None,
        )
    else:
        first = provider_from_name(args.first_provider, "first")
        second = None
        resolver = None
        if args.regime in {"simple", "testgraph-collect"}:
            second = provider_from_name(args.second_provider, "second")
        if args.regime == "simple":
            resolver = provider_from_name(args.resolver_provider, "resolver")

        records, audits = run_cases(
            cases,
            args.regime,
            first=first,
            second=second,
            resolver=resolver,
            shakedown=10 if args.shakedown else None,
        )
    write_jsonl(args.output, records)
    write_jsonl(args.audit_output, audits)
    print(f"wrote {len(records)} {args.regime} records to {args.output}")
    print(f"wrote {len(audits)} audit records to {args.audit_output}")


if __name__ == "__main__":
    main()
