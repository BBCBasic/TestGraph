# TG-AI Resolver Design

## Goal
Add a dormant, TestGraph-funded AI resolver that wakes only when the existing classification workflow enters a genuine dispute.

## V1 Scope
- Trigger only when independent classification decisions diverge and the subject enters `disputed`.
- Call OpenAI over HTTPS from TestGraph; no public third MCP and no permanent AI process.
- Keep the resolver disabled unless `TG_AI_RESOLVER_ENABLED=true` and `OPENAI_API_KEY` is configured.
- Request strict JSON output containing a chosen existing candidate type, confidence, reason, and action.
- Treat the model answer as a proposal. TestGraph validates it before applying any classification state change.
- Preserve the existing classification audit records; the resolver adds its own independent decision record using the configured resolver model identity.
- Resolver failure must never break the caller's normal classification request: the subject remains `disputed` and the failure is logged.

## Architecture
The existing classification service remains authoritative. When it detects more than one candidate target type, it commits the `disputed` state and then invokes a small resolver service. That service builds a bounded case payload from the subject and current classification decisions, calls OpenAI's Responses API via the already-installed `httpx`, validates the returned JSON, and submits a resolver decision back through the existing classification decision path.

The resolver may only choose among types already proposed in the current dispute. This prevents a paid model from inventing vocabulary or bypassing the normal semantic and hierarchy checks in V1.

## Configuration
- `TG_AI_RESOLVER_ENABLED` default `false`
- `OPENAI_API_KEY` optional secret
- `TG_AI_RESOLVER_MODEL` default `gpt-5-mini`
- `TG_AI_RESOLVER_TIMEOUT_SECONDS` default `20`

No secret is stored in git. Railway receives `OPENAI_API_KEY` as an environment variable.

## Failure Behaviour
Network errors, provider errors, malformed responses, unknown choices, or resolver disagreement with its own constraints are caught. TestGraph logs the event and leaves the subject in `disputed` for later resolution.

## Testing
Unit tests cover configuration gating, strict response validation, candidate-choice restriction, and the classification dispute hook without making a real network request. Existing classification tests must continue to pass.
