# Plans are versioned drafts, and approval is an idempotent human step

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

A generated plan is not the same thing as an accepted plan. The user must see the meals,
grocery list, and cost before anything is treated as final, and a double-click on Approve
must not create two grocery runs or overwrite a newer edit.

## Decision

Treat every plan as a persisted draft with an explicit lifecycle.

- Statuses: `draft`, `reviewing`, `approving`, `approved`, `rejected`, plus `escalated` /
  `failed` for runs that never produced a usable plan.
- The draft is saved before approval, with meals in `plan_meals`; the grocery list, when
  generated, is stored as JSON on the plan.
- `version` increments when a plan is updated (for example an edited meal), and the old
  meal rows are replaced. The version number is a monotonic counter, not a snapshot.
- Approval is a separate endpoint. It sets `approving` first, generates the grocery list
  if needed, sets `approved_at`, and returns the plan. If the plan is already approved with
  a grocery list, it returns it unchanged. A concurrent call gets `409`.
- If grocery generation fails, the status reverts to `draft` and the API returns an error.
- Every plan endpoint checks that the caller owns the plan.

## Consequences

**Good:** nothing is presented as approved without a human action, and retries are safe.
Grocery generation is deferred until the user commits, so abandoned drafts do not spend
Shopper tokens. The status field makes the lifecycle visible in the UI and the database.

**Costly:** `version` gives an audit number but not history — previous meal sets are
deleted, so "what did version 2 contain?" cannot be answered. The `approving` flag is an
application-level lock, not a database row lock, so two processes can still race. Editing
an approved plan is last-write-wins. The grocery list is generated at approval time, which
means an approval can fail for reasons unrelated to the meals. An approved plan is not
re-validated when the user's allergies change later; that needs an explicit re-check path.

## Code map

- `backend/app/db/models.py` — `Plan`, `PlanMeal`, statuses, version, grocery JSON
- `backend/app/db/repositories/plans.py` — create, update, versioning
- `backend/app/api/routes/plans.py` — approval, idempotency, ownership
- `backend/app/orchestration/runner.py` — draft persistence and edit updates
