# A plan is built in one pass, revised at most three rounds (flagged meals only), and capped at seven days

**Status:** Accepted · **Recorded:** 2026-10-22 · **Updated:** 2026-10-25

## Context

Planning could be incremental (build day by day), conversational (let the Planner keep
talking to the reviewers), or bounded (one draft, a fixed number of review cycles). Each
cycle costs several model calls, and an unbounded loop can burn quota while converging on
nothing. Plan length has a separate practical limit: the Planner writes the whole plan as
one structured object, and longer writes come back truncated or internally inconsistent.

## Decision

- The Planner produces the entire **first draft** in one structured response.
- Nutrition and Allergy review it in parallel.
- A deterministic routing function chooses exactly one of: proceed to the Shopper, send
  the draft back with consolidated feedback, or escalate.
- **Retry rounds are targeted.** When a review fails, the Supervisor turns the two reports
  into a `RepairTargets` instruction (`find_repair_targets`): an allergy or diet veto
  flags that one meal; a calorie problem flags that whole day (the day's total is what is
  wrong); a veto naming a meal that cannot be found flags that day, never the whole plan.
  Locked (user-fixed) meals are never flagged. The Planner's `repair_plan` asks the AI for
  one replacement meal per flagged slot (single-person portions, deterministic allergen and
  diet check on each new meal with one retry), keeps a replaced meal's calories so the day
  total does not move, and portion-balances a flagged day with the existing code. Every
  other meal is copied across from the previous draft and is never sent to the AI. If the
  repair fails, or nothing specific can be flagged, the whole plan is rewritten as before.
- At most **3 rounds** (the first draft is round 1). The design intent is that on the
  third failure the run stops and asks the user what to relax rather than silently
  shipping a plan - see the known gap below.
- At most **7 days**. A longer request is declined with an explanation and an offer of a
  7-day plan (`action: "days_too_many"`), not partially fulfilled.
- Allergy items are marked as vetoes and nutrition items as adjustments in the written
  feedback, which is still produced for the whole-plan fallback.

## Consequences

**Good:** cost and latency per request are bounded and predictable. A stuck Planner cannot
loop forever. A retry no longer risks changing meals that had already passed, and it costs
one small AI call per flagged meal instead of rewriting the week. Most calorie problems
are removed before a review ever flags them, because the audit balances portions first
(ADR 0009).

**Costly:** seven days is a product limit enforced because of a serialisation limit, not a
nutrition one; supporting 14 days means chunking the plan and reviewing each chunk, not
just raising the number. Three rounds may be too few for a heavily constrained request
(multiple allergies plus fixed meals). The first draft is still one whole-plan write, so
one bad day in it can still be expensive. A repaired meal is written without seeing the
whole week; variety is only approximated by telling the AI which dishes are already in the
plan. Calorie problems are always per day (the audit does not name a meal), so a calorie
repair rewrites that day's unlocked meals.

**Known gap (found in the 2026-10-25 pre-launch review):** the escalation described above
is not built the way this ADR intends. When the round limit is hit the graph returns status
`escalated`, but `runner.py` still saves the last draft (status `escalated`, or `failed`
when a reviewer crashed) and writes the user's reply as "created, ready to review"; no
step asks what to relax, and the approve endpoint does not check the plan's status or
re-run the checks. No `escalated` or `failed` plan exists in the production database so
far, and `agent_trace` is empty because the supervisor's trace events are broadcast live
but never saved, so past escalations cannot be audited.

## Code map

- `backend/app/config.py` — `max_rounds`, `max_plan_days`, `default_plan_days`
- `backend/app/orchestration/graph.py` — draft -> parallel reviews -> decision -> repair
- `backend/app/orchestration/supervisor/routing.py` — proceed / retry / escalate, and
  `find_repair_targets` (reports -> flagged meals/days)
- `backend/app/schemas/domain.py` — `RepairItem` / `RepairTargets`
- `backend/app/schemas/state.py` — `PlanState.repair_targets`
- `backend/app/guardrails/input_checks.py` — length and calorie gates
- `backend/app/agents/planner/agent.py` — full-plan generation and `repair_plan`
- Tests: `tests/unit/test_supervisor_repair_targets.py`, `tests/agents/test_planner_repair.py`,
  `tests/integration/test_targeted_repair_graph.py`
