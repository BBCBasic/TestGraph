from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.vocabulary_traversal import run_vocabulary_traversal_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare full-index and progressive TestGraph vocabulary traversal.",
    )
    parser.add_argument("--nodes", type=int, default=10_000)
    parser.add_argument("--branching-factor", type=int, default=10)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    report = run_vocabulary_traversal_benchmark(
        node_count=args.nodes,
        branching_factor=args.branching_factor,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True)
    print(encoded)
    if args.json_output:
        args.json_output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
