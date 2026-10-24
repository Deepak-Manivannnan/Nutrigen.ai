# External data degrades to AI fallbacks, and every fallback keeps its provenance

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

Three external sources are unreliable in different ways: TheMealDB has a small catalogue
with no nutrition data, USDA has gaps and rate limits, and Indian mandi prices cover raw
commodities but not packaged goods. The product still has to return something useful
without pretending a guess is a measurement.

## Decision

Define an explicit fallback ladder per source and persist where each value came from.

**Recipes.** Search TheMealDB first. If the catalogue cannot cover the cuisine or diet,
the Planner writes the missing recipes itself. Every meal carries
`source: api | ai_generated | user_specified`. The output guardrail counts AI recipes so
the response can carry that provenance.

**Prices.** Try data.gov.in (Agmarknet), then CEDA, then an AI estimate, then a small
retail baseline table. Wholesale prices are per quintal; the code converts to per kilogram
and applies a configurable 1.25 retail markup. Each grocery item records
`price_source: mandi_api | ai_estimate` (the baseline table is also surfaced as an
estimate). Costs are in INR.

**Nutrition.** A lookup that cannot resolve an ingredient returns `found: false` and may
use a labelled estimate; it never silently invents a USDA match.

**Grocery quantities** are merged deterministically from the meal ingredients before any
price lookup, so the same plan produces the same shopping list.

## Consequences

**Good:** the app works without every key configured, and the UI can tell the user which
parts are estimates. Provenance is stored with the artifact, not reconstructed later.
Deterministic merging keeps grocery totals stable across runs.

**Costly:** the fallback path is the common path for Indian dishes and pantry items, so a
large share of a typical plan can be AI-generated. Mandi wholesale prices are lower than
what a shopper pays even after markup, and the baseline table goes stale. AI prices vary
between runs. `ai_estimate` currently covers both model estimates and static baselines,
which is honest but not precise enough to distinguish them in analytics.

## Code map

- `backend/app/tools/recipes/themealdb.py` and `backend/app/agents/planner/agent.py`
- `backend/app/tools/prices/mandi.py` and `backend/app/agents/shopper/agent.py`
- `backend/app/tools/shopping.py` — deterministic merge
- `backend/app/guardrails/output_checks.py` — AI counts and disclaimer
- `backend/app/schemas/domain.py` — `source` and `price_source` enums
