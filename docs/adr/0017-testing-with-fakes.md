# Tests use fake models and fake APIs; live-model tests are a separate suite

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

Almost every code path here touches a paid, non-deterministic external service. A test
suite that calls Gemini, USDA, TheMealDB, and a mandi API would be slow, flaky, expensive,
and unusable offline. But a suite that mocks everything would never catch the failures that
matter most: a model ignoring the output schema, misreading an ingredient, or a real API
changing shape.

## Decision

Use two layers.

**Default suite (`backend/tests/`)** runs offline against fakes:

- `FakeLLMClient` returns queued text/structured responses and records calls, so an agent's
  loop and its tool choices can be asserted without a network.
- External tools are replaced with deterministic fixtures.
- Unit tests cover guardrails, calorie maths, routing, tools, and API contracts. Agent tests
  exercise each worker alone. Integration tests run the graph with fakes.
- Tests create uniquely named throwaway users instead of hardcoding `user_id = 1`; an
  earlier version deleted real users' plans during setup.

**Live suite (`backend/tests_live/`)** is manual and billed: it calls the real model to
check ingredient resolution and other behaviour fakes cannot reproduce. It is not part of
the default run.

## Consequences

**Good:** the default suite is fast, free, and deterministic enough to run on every change.
Failures point at our logic rather than at quota. Agent loops, veto paths, and guardrails
can be tested without a key.

**Costly:** fakes encode our assumptions about the model; they cannot catch prompt drift,
schema non-compliance, or a new model's quirks. The live suite is easy to forget, so real
regressions can survive until a manual run. The default suite is not hermetic either: it
uses `AsyncSessionLocal`, so it needs the configured MySQL instance and the startup schema,
and it writes rows to that database. There is no separate test database or transaction
rollback by default. Coverage of "the LLM reasons correctly about curry paste" is
necessarily thin.

## Code map

- `backend/tests/fakes/fake_llm.py` — queued fake model
- `backend/tests/conftest.py` — throwaway users
- `backend/tests/unit/`, `agents/`, `integration/` — the offline layers
- `backend/tests_live/` — real-model checks
- `pytest.ini` — asyncio mode and import path
