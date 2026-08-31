# TestGraph kill test

This benchmark is deliberately hostile to TestGraph. Its purpose is to decide whether the extra epistemic workflow is justified, not to demonstrate that the project works.

## Hypothesis

Compare three regimes on the same frozen cases and evidence:

- `single`: one model decides.
- `simple`: two models decide independently; agreement is accepted, disagreement goes to an independent resolver.
- `testgraph`: the real TestGraph V2 classification/convergence workflow is used, including server-enforced independent decisions, dispute state and resolver behavior.

The primary endpoint is **wrong canonicalisation rate**: among items promoted to canonical/confirmed status, the proportion whose final type is not in the frozen answer key.

TestGraph passes only if it reduces wrong canonicalisation by at least **50% relative to `simple`**. If the simple baseline has no wrong canonicalisations, the benchmark returns `ARCHIVE` because no superiority can be established on that sample.

## Corpus

`benchmarks/kill_cases.json` contains 120 frozen cases: 30 easy controls, 30 semantic-head/modifier traps, 30 legitimate compounds, and 30 genuine ambiguity cases. Do not edit the answer key after model outputs have been collected. Any changed corpus is a new benchmark version.

## Result record format

Store one JSON object per line. Required fields are `case_id`, `regime`, and `type`.

```json
{"case_id":"h001","regime":"simple","type":"bale","canonical":true,"consensus":false,"operational_failure":false,"model_calls":3,"resolver_calls":1,"cost_usd":0.012}
```

- `canonical`: the regime promoted the answer to accepted/canonical status.
- `consensus`: the independent first decisions agreed before resolution.
- `operational_failure`: workflow/tool/state failure occurred for that case.
- `model_calls`: total LLM calls attributable to the case.
- `resolver_calls`: resolver/judge calls attributable to the case.
- `cost_usd`: optional measured provider cost.

For the TestGraph regime, record the actual final server status/type; do not substitute what the calling model says happened.

## Model execution stage

The model runner uses `httpx` directly and requires no provider SDK. Provider secrets are read only from environment variables.

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
```

Default model IDs are `gpt-5.6-terra` and `claude-sonnet-5`. Pin different IDs without changing code by setting `KILL_TEST_OPENAI_MODEL` / `KILL_TEST_ANTHROPIC_MODEL`, or pin an individual slot with `KILL_TEST_FIRST_MODEL`, `KILL_TEST_SECOND_MODEL`, and `KILL_TEST_RESOLVER_MODEL`.

Run the 10-case machinery shakedown first:

```bash
python scripts/run_kill_test.py \
  --regime simple \
  --shakedown \
  --output /tmp/kill-simple-shakedown.jsonl \
  --audit-output /tmp/kill-simple-shakedown-audit.jsonl
```

Then collect the full single-model and simple-ensemble baselines:

```bash
python scripts/run_kill_test.py --regime single \
  --output kill-single.jsonl --audit-output kill-single-audit.jsonl

python scripts/run_kill_test.py --regime simple \
  --output kill-simple.jsonl --audit-output kill-simple-audit.jsonl
```

The first two blind decisions for the TestGraph regime are collected separately so benchmark subjects are not written into the normal graph:

```bash
python scripts/run_kill_test.py --regime testgraph-collect \
  --output kill-testgraph-decisions.jsonl \
  --audit-output kill-testgraph-decisions-audit.jsonl
```

`testgraph-collect` is deliberately **not a scored TestGraph result**. It freezes the two independent first decisions with `pending_replay=true`. A subsequent isolated replay stage must submit those decisions through the real TestGraph V2 classification/convergence workflow and record the actual final server state. This separation prevents the benchmark runner from polluting the production ontology while preserving blind first judgments.

Use `--case-id h001` (repeatable) for targeted diagnosis or `--limit N` for a bounded runner check. Do not use a subset result to decide the project verdict.

## Run discipline

1. Freeze the corpus before seeing outputs.
2. Give each regime identical observation text and the same available vocabulary/context.
3. For independent first decisions, prevent model B from seeing model A's answer.
4. Use different model identities for the two first decisions where possible.
5. Do not repair TestGraph during a benchmark run. A stuck/incorrect workflow is an operational failure.
6. Keep raw provider responses and TestGraph interaction IDs outside the scored file for later audit.
7. Run all regimes across the complete corpus before looking at the final verdict.

## Scoring

```bash
python scripts/score_kill_test.py --results kill-results.jsonl --output kill-report.json
```

The JSON report includes correctness, wrong-canonicalisation rate, false-consensus rate, operational-failure rate, model/resolver calls, cost, and the predetermined `CONTINUE`/`ARCHIVE` verdict.
