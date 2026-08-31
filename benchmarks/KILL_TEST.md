# TestGraph kill test

This benchmark is deliberately hostile to TestGraph. Its purpose is to decide whether the extra epistemic workflow is justified, not to demonstrate that the project works.

## Hypothesis

Compare three regimes on the same frozen cases and evidence:

- `single`: use the first decision from one frozen blind model pair.
- `simple`: use both decisions from that same frozen blind pair; agreement is accepted, disagreement goes to an independent resolver.
- `testgraph`: replay that exact same frozen blind pair through the real TestGraph V2 classification/convergence workflow, including server-enforced independent decisions, dispute state and resolver behavior.

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

### Freeze the blind decisions once

The experiment makes exactly one blind GPT/Claude decision pair per case. That pair is then reused by all three regimes. This prevents stochastic sampling differences from being mistaken for a protocol effect.

Ten-case shakedown collection:

```bash
python scripts/run_kill_test.py \
  --regime testgraph-collect \
  --shakedown \
  --output /tmp/kill-frozen-shakedown.jsonl \
  --audit-output /tmp/kill-frozen-shakedown-audit.jsonl
```

Full collection:

```bash
python scripts/run_kill_test.py \
  --regime testgraph-collect \
  --output kill-frozen-decisions.jsonl \
  --audit-output kill-frozen-decisions-audit.jsonl
```

`testgraph-collect` is deliberately **not a scored result**. It freezes the two independent first decisions with `pending_replay=true`. Model B receives the same observation prompt and cannot see model A's answer.

### Score Single from the frozen first decision

```bash
python scripts/run_kill_test.py \
  --regime single \
  --collected-input kill-frozen-decisions.jsonl \
  --output kill-single.jsonl \
  --audit-output kill-single-audit.jsonl
```

### Score Simple from the same frozen pair

```bash
python scripts/run_kill_test.py \
  --regime simple \
  --collected-input kill-frozen-decisions.jsonl \
  --output kill-simple.jsonl \
  --audit-output kill-simple-audit.jsonl
```

If the two frozen decisions agree, Simple accepts the shared answer without another model call. If they disagree, only the independent Simple resolver is called.

The direct live `single` and `simple` runner paths remain available for diagnostics, but **must not be used for the final comparative benchmark** because they would resample the first decisions.

## Isolated TestGraph replay

Replay the same frozen decisions through the real classification, dispute and `tg-ai` resolver services in a separate SQLite database. The normal application database is never opened by this stage.

Ten-case shakedown:

```bash
python scripts/replay_kill_test.py \
  --collected /tmp/kill-frozen-shakedown.jsonl \
  --results /tmp/kill-testgraph-shakedown.jsonl \
  --audit /tmp/kill-testgraph-shakedown-audit.jsonl \
  --database sqlite+pysqlite:////tmp/kill-test-shakedown.db \
  --shakedown 10
```

Full replay:

```bash
python scripts/replay_kill_test.py \
  --collected kill-frozen-decisions.jsonl \
  --results kill-testgraph.jsonl \
  --audit kill-testgraph-audit.jsonl \
  --database sqlite+pysqlite:///kill-test-replay.db
```

Each benchmark case starts from a synthetic `benchmark entity` type with the two frozen candidate types placed beneath it only inside the isolated database. The two model decisions are submitted through `propose_reclassification`. If they disagree, the existing `tg-ai` dispute hook is allowed to run normally. If the resolver is disabled, missing credentials, rejects the case, or otherwise leaves the subject disputed, that case is an operational failure. Final scoring uses `classification_state` from the server services; it never substitutes a model claim for server state.

Use `--case-id h001` (repeatable) for targeted model-runner diagnosis or `--limit N` for a bounded collection check. Do not use a subset result to decide the project verdict.

## Run discipline

1. Freeze the corpus before seeing outputs.
2. Collect one blind first/second model pair per case and reuse that exact pair for Single, Simple and TestGraph.
3. Prevent model B from seeing model A's answer during the frozen collection.
4. Use different model identities for the two first decisions where possible.
5. Do not repair TestGraph during a benchmark run. A stuck/incorrect workflow is an operational failure.
6. Keep raw provider responses and TestGraph interaction IDs outside the scored file for later audit.
7. Run all regimes across the complete corpus before looking at the final verdict.
8. Do not change prompts, answer keys or protocol based on classification correctness observed in the 10-case machinery shakedown.

## Scoring

Combine the three scoreable regime files before scoring, for example:

```bash
cat kill-single.jsonl kill-simple.jsonl kill-testgraph.jsonl > kill-results.jsonl
python scripts/score_kill_test.py --results kill-results.jsonl --output kill-report.json
```

The JSON report includes correctness, wrong-canonicalisation rate, false-consensus rate, operational-failure rate, model/resolver calls, cost, and the predetermined `CONTINUE`/`ARCHIVE` verdict.

Provider `cost_usd` is currently zero unless explicit pricing is supplied by the runner. Do not claim a comparative dollar-cost result from zero-valued records; model and resolver call counts remain valid operational-cost proxies.
