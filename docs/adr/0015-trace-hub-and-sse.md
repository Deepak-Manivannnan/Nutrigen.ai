# Agent events are persisted and streamed, so a run can be audited and watched

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

A multi-agent run is hard to trust if the only output is a final plan. Users want to see
that checks actually happened; developers need to reconstruct why a veto fired. The trace
also has to serve two different consumers: a live UI that wants events immediately, and an
audit reader that wants the complete ordered history after the fact.

## Decision

Make every meaningful step an `AgentMessage` and send it through one hub.

- Message fields: `message_id`, `task_id`, `from`, `to`, `type`, `content`, `payload`,
  `plan_version`, `timestamp`. Types include `task`, `draft`, `feedback`, `veto`,
  `approve`, `error`, `escalate`, `thought`, `tool_call`, `status`.
- `trace_hub.log_event` writes the message to `agent_trace` when a database session is
  supplied, then broadcasts it to every queue subscribed to that `task_id`.
- `GET /api/streaming/{task_id}` is an SSE stream with a 15-second heartbeat and disconnect
  detection. It feeds the compact `AgentTraceBar` on the chat page.
- `GET /api/trace/{task_id}` returns the persisted history for the same task.
- Workers only ever address the Supervisor (or `all` / `user` for supervisor messages), so
  the trace reads as a hub-and-spoke conversation.

## Consequences

**Good:** the live view and the audit log share one event shape. Events logged with a
session are both persisted and broadcast, so the UI and the record agree. Debugging a run
means reading one ordered list. Vetoes and retries are visible rather than implicit.

**Costly:** the hub is in-memory and per process; a run pinned to one worker cannot be
streamed from another, and a restart drops live subscribers (the DB history survives).
SSE has no replay: a client that connects late sees only new events and must fetch the
trace endpoint for history. Persistence is opt-in per call, so an event logged without a
session reaches the UI but not the audit table. The trace endpoint is keyed only by
`task_id` and does not verify ownership, so a leaked task id exposes the run's payload.
Payloads can contain profile and plan content, and there is no retention or redaction
policy for `agent_trace`.

## Code map

- `backend/app/schemas/messages.py` — `AgentMessage`, message types
- `backend/app/observability/trace.py` — `TraceHub`, DB write + broadcast
- `backend/app/api/streaming.py` — SSE endpoint
- `backend/app/api/routes/trace.py` — persisted history
- `backend/app/orchestration/supervisor/agent.py` — `log_step`
- `frontend/src/components/AgentTraceBar.jsx` — live consumer
