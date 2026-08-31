from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.kill_test import load_cases
from benchmarks.replay import replay_records
from benchmarks.runner import write_jsonl


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay frozen kill-test decisions through TestGraph")
    parser.add_argument("--cases", default="benchmarks/kill_cases.json")
    parser.add_argument("--collected", required=True, help="testgraph-collect JSONL")
    parser.add_argument("--results", required=True, help="output scoreable TestGraph JSONL")
    parser.add_argument("--audit", required=True, help="output replay audit JSONL")
    parser.add_argument("--database", default="sqlite+pysqlite:///kill-test-replay.db")
    parser.add_argument("--shakedown", type=int, help="replay only the first N frozen cases")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.shakedown is not None:
        if args.shakedown < 1:
            raise SystemExit("--shakedown must be at least 1")
        cases = cases[: args.shakedown]

    collected = _load_jsonl(Path(args.collected))
    records, audits = replay_records(cases, collected, args.database)
    write_jsonl(args.results, records)
    write_jsonl(args.audit, audits)
    print(json.dumps({
        "cases": len(records),
        "confirmed": sum(bool(row.get("canonical")) for row in records),
        "operational_failures": sum(bool(row.get("operational_failure")) for row in records),
        "results": args.results,
        "audit": args.audit,
        "database": args.database,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
