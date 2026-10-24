# A plan is built in one pass, revised at most three rounds, and capped at seven days

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

Planning could be incremental (build day by day), conversational (let the Planner keep
talking to the reviewers), or bounded (one draft, a fixed number of review cycles). Each
cycle costs several model calls, and an unbounded loop can burn quota while converging on
nothing. Plan length has a separate practical limit: the Planner writes the whole plan as
one structured object, and longer writes come back truncated or internally inconsistent.

## Decision

- The Planner produces the entire plan in one structured response.
- Nutrition and Allergy review it in parallel.
- A deterministic routing function chooses exactly one of: proceed to the Shopper, send
  the draft back with consolidated feedback, or escalate.
- At most **3 rounds**. On the third failure the run stops and asks the user what to relax;
  it does not silently ship a plan.
- At most **7 days**. A longer request is declined with an explanation and an offer of a
  7-day plan (`action: "days_too_many"`), not partially fulfilled.
- The Planner receives all reviewer feedback together, with allergy items marked as
  vetoes and nutrition items marked as adjustments.

## Consequences

**Good:** cost and latency per request are bounded and predictable. A stuck Planner cannot
loop forever. The escalation message names the actual conflicts, so the user has something
to negotiate with.

**Costly:** seven days is a product limit enforced because of a serialisation limit, not a
nutrition one; supporting 14 days means chunking the plan and reviewing each chunk, not
just raising the number. Three rounds may be too few for a heavily constrained request
(multiple allergies plus fixed meals), in which case the user gets a question instead of a
plan. The whole-plan write also means one bad day can invalidate the entire artifact, and
revision feedback is applied to the whole plan rather than surgically.

## Code map

- `backend/app/config.py` — `max_rounds`, `max_plan_days`, `default_plan_days`
- `backend/app/orchestration/graph.py` — draft → parallel reviews → decision
- `backend/app/orchestration/supervisor/routing.py` — proceed / retry / escalate
- `backend/app/guardrails/input_checks.py` — length and calorie gates
- `backend/app/agents/planner/agent.py` — full-plan structured generation
