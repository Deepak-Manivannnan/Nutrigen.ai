# MySQL is evolved in place at startup; there is no migration tool

**Status:** Accepted · **Recorded:** 2026-10-22 · **Updated:** 2026-10-25

## Context

The app needed one database that works locally, on a free Render instance, and in tests,
without a migration service to operate. The schema also changed quickly during the build:
accounts were split from profiles, plans gained names/versions/diet types, and calorie
fields were added to users.

## Decision

Use a single MySQL database (`nutrigen`) through async SQLAlchemy and `aiomysql`, and
evolve it at application startup.

- `Base.metadata.create_all` creates missing tables. New tables are simply added to the
  models: `refresh_tokens` (ADR 0013) appeared this way, with no manual step, the first
  time any process ran `init_db` against the database.
- A hand-maintained list of `ALTER TABLE` statements adds or relaxes columns for older
  databases; each statement is attempted and failures are ignored.
- Startup also normalises existing plans (vegetarian substitutions, single-person
  portions, absurd calorie values) and saves them without incrementing the version.
- Structured values that do not need querying are stored as JSON: meal ingredients, meal
  macros, the grocery list, and trace payloads.
- Timestamps use IST (`ist_now`), and plans/meals cascade from their user.
- Tests use the same engine with `NullPool` to avoid cross-event-loop connection reuse.

## Consequences

**Good:** a fresh clone starts with one command, and an older database mostly catches up
on its own. There is no Alembic revision chain to maintain, and JSON keeps evolving shapes
out of the schema.

**Costly:** there is no migration history, no down path, and no record of what ran. Because
failures are swallowed, a broken `ALTER` looks exactly like success and leaves the schema
stale until something breaks at runtime. `create_all` never changes an existing column, so
every alteration must be remembered manually. Startup performs writes and can race when
more than one instance boots. The data normalisation silently rewrites users' saved plans
on every boot — useful as a repair, risky as a habit. Tests are not hermetic; they need a
reachable MySQL instance, and they run `init_db` and create throwaway users in whatever
database `DATABASE_URL` points to. A development machine whose `.env` points at the
production database therefore creates tables and test rows there, so use a separate
database for development and tests. Moving to Postgres would require revisiting the JSON and
timestamp choices.

## Code map

- `backend/app/db/session.py` — engine, `create_all`, ALTER list, boot normalisation
- `backend/app/db/models.py` — tables, cascades, JSON columns, IST timestamps
- `backend/app/db/repositories/` — the only SQL callers
- `render.yaml` — `DATABASE_URL` supplied by the environment
