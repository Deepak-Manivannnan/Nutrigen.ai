# The React SPA recovers a reply after reload and keeps chat history on the device

**Status:** Accepted · **Recorded:** 2026-10-22 · **Updated:** 2026-10-25

## Context

A chat turn can take minutes: intent, planning, two reviews, possibly three rounds, then
the Shopper. Users refresh, lose the tab, or come back on a flaky connection. If the reply
existed only in the HTTP response that was in flight, a refresh would lose a paid-for run.
At the same time, the app should not pretend to have a full server-side chat history it
does not store.

## Decision

Keep the server authoritative for plans, profiles, and traces, and make chat delivery
reload-safe with a small amount of client state.

- The access token, refresh key and cached identity live in `localStorage` (ADR 0013), so
  closing the tab or the browser keeps you signed in and reloading does not sign out.
  `authFetch` refreshes an expired access token with one shared call and retries the
  request, so a long wait or an idle tab does not interrupt the user.
- Chat messages and the current pending workflow live in `localStorage`, keyed by user id,
  so a conversation survives a closed tab and is only read after that user signs in. Only
  an explicit Sign out wipes them (plans stay in the database). When the session really
  ends (the refresh key is rejected) only the credentials are cleared; the conversation
  and any half-finished workflow are kept for the next sign-in.
- Before sending, the client records an inflight marker (`taskId`, `sentAt`) in
  `sessionStorage` - it answers "is *this tab* waiting on a run?" - and sends that
  `task_id` with the request.
- The backend files each finished response in a process-local inbox keyed by
  **(user, `task_id`)** (30-minute TTL, 200 entries). A failed run (unexpected crash or the
  AI service being down) is filed too, as a `failed` response carrying the same friendly
  message and the caller's pending workflow, so a reload shows the error straight away
  instead of waiting out the timeout. On reload, the chat page sees the marker, shows the
  typing state, and polls `/api/chat/result/{task_id}` (sign-in required, own replies only,
  404 otherwise) until the reply arrives or a ten-minute deadline passes.
- When the live page itself receives a server-reported error it clears the marker, so a
  later reload does not collect the same error a second time. A dropped connection keeps
  the marker, because the server may still finish.
- Replies are deduplicated so the original response path and the inbox poll cannot render
  the same message twice.
- Empty states use clearly separated demo generators (`generateDemoPlan`,
  `generateDemoGrocery`); they are not mixed into a real run.

## Consequences

**Good:** a refresh no longer loses a completed run, and users keep their conversation
across tab closes. No chat-message table or server-side conversation state is required.
The inflight marker makes recovery deterministic rather than a guess.

**Costly:** history is device-local and per browser, not synced; a new device starts empty.
The inbox is in-memory, so a backend restart loses undelivered replies (failures
included), and multiple instances do not share it. A run started in a tab that is then
closed is not recovered, because the marker is per tab. Polling every five seconds for up
to ten minutes is wasteful for a run that has actually died by restart. Tabs share the
signed-in session and the chat history through `localStorage`, but not the inflight
marker. The refresh key sits in `localStorage` where injected script could read it (see
ADR 0013).

## Code map

- `frontend/src/utils/sessionStore.js` — persistent (tokens, identity) vs per-tab (inflight) storage
- `frontend/src/context/AppContext.jsx` — per-user messages and pending workflow, sign-out vs expiry
- `frontend/src/pages/ChatPage.jsx` — inflight recovery, inbox polling, dedupe
- `frontend/src/utils/api.js` — `API_BASE`, `authFetch` (shared refresh + retry), `fetchChatResult`
- `backend/app/api/routes/chat.py` — response inbox, failed-run filing
- `backend/tests/unit/test_chat_result_auth.py` — inbox ownership and failed runs
- `frontend/src/utils/workflowSimulator.js` — empty-state demo data
