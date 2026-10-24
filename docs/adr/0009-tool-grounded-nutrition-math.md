# Nutrition numbers come from tools; arithmetic stays deterministic

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

A meal plan is only useful if its calories and macros are credible. Letting the Planner
invent "about 520 kcal" produces numbers that cannot be audited and drift between runs.
Public nutrition data is also messy: the same word can resolve to several USDA records,
and many cooked dishes have no direct entry at all.

## Decision

Split nutrition into retrieval and arithmetic.

**Retrieval** (`lookup_nutrition`) tries, in order:

1. A curated staple baseline (never overridden by a model rewrite).
2. A normalised / alias-rewritten name, checked against the baseline again.
3. An agent-supplied estimate, marked as estimated, for ingredients the database cannot
   represent.
4. A skeptically ranked USDA FoodData Central query, with a short timeout.
5. Zeroes with `found: false` if nothing works — never an invented number.

**Arithmetic** (`calculator.py`) is plain code: scale per-100 g values by grams, sum
ingredients into meals and days, and compute averages. It also normalises portions to one
person, applies the 30/40/30 split (25/35/25/15 when a snack is included), converts liquids
to millilitres, caps oil, and substitutes vegetarian proteins in veg/vegan plans.

The Nutrition agent chooses what to look up and interprets ambiguous names, but the final
totals are tool results. The review tolerance is ±5% of the daily target.

## Consequences

**Good:** every number on the final plan has a traceable lookup, and the same inputs
produce the same totals. The staple table removes the worst USDA ambiguities ("chicken"
resolving to chicken spread). Name matching can improve without touching the agents.

**Costly:** the staple table is hand-curated and will always be incomplete; adding a
regional ingredient usually means adding data. USDA's demo key is rate-limited, and the
ranking heuristics are our own. Agent-supplied estimates are still model output — they are
labelled, but they are not measurements. Portion normalisation changes what the Planner
wrote, so the displayed plan can differ from the draft in ways that are easy to miss.

## Code map

- `backend/app/tools/nutrition/staples.py` — curated baseline
- `backend/app/tools/nutrition/usda.py` — aliases, ranking, USDA query, estimates
- `backend/app/tools/nutrition/calculator.py` — scaling, day totals, portion normalisation
- `backend/app/agents/nutrition/agent.py` — lookup strategy and ±5% review
