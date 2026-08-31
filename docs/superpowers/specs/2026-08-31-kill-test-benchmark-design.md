# TestGraph Kill-Test Benchmark Design

## Goal

Create a non-production benchmark subsystem that can falsify the claim that TestGraph's server-enforced epistemic workflow materially improves canonical knowledge over a much simpler independent ensemble.

## Constraints

- No production API, database, workflow, resolver, or guidance behavior changes.
- Frozen 120-case corpus, balanced across four semantic difficulty classes.
- Same evidence and vocabulary/context across compared regimes.
- Primary endpoint: wrong canonicalisation rate.
- Pass threshold fixed before results: at least 50% relative reduction versus the simple ensemble.
- Operational failures count separately and are not silently repaired during a run.
- Result files retain model-call/resolver-call/cost fields for efficiency comparison.

## Components

- `benchmarks/kill_cases.json`: frozen answer key and case metadata.
- `benchmarks/kill_test.py`: pure scoring/validation code.
- `benchmarks/KILL_TEST.md`: protocol and result-record contract.
- `scripts/score_kill_test.py`: CLI report generator.
- `tests/test_kill_test.py`: unit tests for scoring and verdict logic.

The benchmark does not implement another classification workflow. The TestGraph arm must exercise the existing V2 server and record its observed final state.
