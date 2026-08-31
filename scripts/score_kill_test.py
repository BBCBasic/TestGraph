from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.kill_test import build_report, evaluate_results, load_cases


def _load_records(path: Path) -> list[dict]:
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the TestGraph kill-test benchmark")
    parser.add_argument("--cases", default="benchmarks/kill_cases.json")
    parser.add_argument("--results", required=True, help="JSONL file containing result records")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()

    summaries = evaluate_results(load_cases(args.cases), _load_records(Path(args.results)))
    report = build_report(summaries)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
