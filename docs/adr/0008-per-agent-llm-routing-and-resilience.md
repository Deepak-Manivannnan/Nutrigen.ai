# Two Gemini keys, a model per agent, and retries that fail honestly

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

Agents run concurrently and each makes several calls per request. On a single free-tier
key, the Supervisor's intent call, the Planner's draft, two reviewers, and the Shopper
compete for the same requests-per-minute budget. A quota error in the middle of a review
must not be mistaken for a passing check, and model choice matters: allergen judgement
deserves a stronger model than grocery arithmetic.

## Decision

Centralise every model call in `LLMClient` and route by role.

- **Key 1:** Supervisor, Planner. **Key 2:** Nutrition, Allergy, Shopper. If key 2 is
  absent, it falls back to key 1 — the app still runs, with shared quota.
- **Models:** Supervisor `gemini-3.1-flash-lite`, Planner/Nutrition
  `gemini-3.5-flash-lite`, Allergy `gemini-3.5-flash`, Shopper `gemini-3.1-flash-lite`.
- Mechanical Nutrition sub-calls (rewriting a food name, supplying macros) temporarily
  switch to `gemini-2.5-flash` via `using_model`, then restore the agent's model.
- **Structured calls** use temperature 0 and a response schema.
- **Resilience:** three attempts per model with short backoff; a failed model falls through
  a chain (`gemini-3.5-flash-lite`, then `gemini-2.5-flash`); quota errors with a long
  retry delay fail fast instead of sleeping for hours.
- **Circuit breaker:** three consecutive failures on a model open it for 300 seconds, so
  turns fail in milliseconds rather than hanging.
- `LLMUnavailableError` propagates. No component substitutes a canned reply.

## Consequences

**Good:** one key's quota exhaustion does not take down the other group. Model changes are
configuration, not refactors. Retries and breaker state are shared by every caller, and the
UI gets an honest service-trouble message instead of fabricated content.

**Costly:** the fallback chain can silently change which model answered, so quality varies
during an incident. The breaker is per process and in-memory — with multiple workers each
has its own view. Two keys double the credential surface and must both be set in
deployment. The client is Gemini-specific despite the wrapper; switching providers means
rewriting the transport, not just a config value. Allergy checks inherit the stronger
model's quota cost.

## Code map

- `backend/app/config.py` — keys, per-agent models, lookup model
- `backend/app/llm/client.py` — routing, retries, fallback chain, breaker, structured output
- `backend/app/agents/base.py` — calls through the shared client
