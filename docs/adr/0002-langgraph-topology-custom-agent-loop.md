# LangGraph defines the topology; each worker runs a hand-written think–act–observe loop

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

The workflow needs branching (retry, escalate, proceed) and shared state. The workers
also need to call tools, but their step budgets, timeouts, and output schemas are part
of the product's safety story, not implementation trivia. A general-purpose agent
executor would hide those controls behind its own loop and memory model.

## Decision

Use LangGraph only for the graph: a `StateGraph` over `PlanState`, six nodes, two
conditional edges, and Nutrition + Allergy gathered in parallel inside a single review
node. Each worker is a subclass of `BaseAgent` with its own loop:

1. Ask the model for one structured `AgentStepAction`
   (`thought`, `action`, `tool_name`, `tool_args`, `finish_summary`).
2. If `call_tool`, execute only from that agent's whitelist and append the observation.
3. If `finish`, stop and request the agent's output schema.
4. Hard limits: 6 steps and 30 seconds by default. On timeout, force a final answer from
   the observations collected so far.

Tool descriptions include the real Python signature, generated with `inspect`, so the
model cannot guess argument names.

## Consequences

**Good:** every agent has the same observable loop; step/timeout budgets are enforceable;
tool permissions are explicit; the final artifact is always schema-validated; prompts and
limits can be tuned per agent without changing the framework.

**Costly:** the loop is ours to maintain — malformed JSON, truncated thoughts, and
ambiguous tool args are our failure modes. A step decision gets one retry, not an
unbounded recovery loop. The final structured call is separate from the loop, so an
agent can finish its investigation and still fail to serialise a plan. Long observations
are truncated (6,000 chars), which can cut a recipe mid-way.

## Code map

- `backend/app/agents/base.py` — loop, limits, tool permissions, schema finalisation
- `backend/app/orchestration/graph.py` — graph nodes and edges
- `backend/app/schemas/state.py` — `PlanState`, the shared graph state
- `backend/app/schemas/domain.py` — each worker's output contract
