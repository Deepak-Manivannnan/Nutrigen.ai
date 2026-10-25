# The Supervisor is the only workflow manager; workers never talk to each other

**Status:** Accepted · **Recorded:** 2026-10-22 · **Updated:** 2026-10-25

## Context

The system has four workers (Planner, Nutrition, Allergy, Shopper) whose outputs depend
on each other's. A peer-to-peer design — Nutrition asking Allergy for its allergen list,
or the Shopper negotiating with the Planner — would let any worker widen its own
responsibilities and would scatter the safety rules across the graph.

## Decision

Use a hub-and-spoke topology. The Supervisor is the only component that knows all
agents, the only one that assigns tasks, and the only one that decides what happens
next. Workers receive a task plus the minimum context they need, and return exactly one
report. There are no worker-to-worker edges and no worker-to-worker imports.

- Nutrition and Allergy run in parallel, but only after the Supervisor dispatches both.
- The Planner never sees the reviewers directly; feedback is relayed by the Supervisor.
  On a retry the Supervisor reads the two reports itself, works out exactly which meals
  or days failed (`find_repair_targets`), and hands the Planner a plain `RepairTargets`
  instruction on the shared state. The Planner redoes only those and returns the plan to
  the Supervisor, which re-runs both reviews (see ADR 0007). The Planner never imports or
  reads the Nutrition or Allergy reports.
- The Shopper runs only after the Supervisor has confirmed both reviews passed.
- Cross-cutting concerns (round limits, veto priority, timeouts, tracing) live at the hub.

## Consequences

**Good:** one place to audit, log, and constrain the run; workers stay independently
testable with a fake LLM; a new check can be added without teaching every worker about
it; the trace always has an unambiguous sender and receiver.

**Costly:** adding a worker means touching the graph, the supervisor, and the routing
function, not just dropping in a new agent. Any "Nutrition needs Allergy's answer" idea
must be expressed as two Supervisor-mediated steps. The Supervisor node is on the hot
path for every run, so a bug there stalls everything.

## Code map

- `backend/app/orchestration/graph.py` — nodes and the only edges in the system
- `backend/app/orchestration/supervisor/agent.py` — guardrail, review evaluation, reply composition
- `backend/app/orchestration/supervisor/routing.py` — deterministic next-step rules, and
  `find_repair_targets` (reports -> what the Planner should redo)
- `backend/app/schemas/domain.py` — `RepairTargets`, the Supervisor-to-Planner instruction
- `backend/app/agents/` — workers, each self-contained
