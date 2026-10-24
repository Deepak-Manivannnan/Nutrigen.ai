# The React SPA recovers a reply after reload and keeps chat history on the device

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

A chat turn can take minutes: intent, planning, two reviews, possibly three rounds, then
the Shopper. Users refresh, lose the tab, or come back on a flaky connection. If the reply
existed only in the HTTP response that was in flight, a refresh would lose a paid-for run.
At the same time, the app should not pretend to have a full server-side chat history it
does not store.

## Decision

Keep the server authoritative for plans, profiles, and traces, and make chat delivery
reload-safe with a small amount of client state.

- The token and cached identity live in `sessionStorage`: closing the tab signs out,
  reloading does not.
- Chat messages and the current pending workflow live in `localStorage`, keyed by user id,
  so a conversation survives a closed tab and is only read after that user signs in.
- Before sending, the client records an inflight marker (`taskId`, `sentAt`) in
  `sessionStorage` and sends that `task_id` with the request.
- The backend files each successful response in a process-local inbox keyed by `task_id`
  (30-minute TTL, 200 entries). On reload, the chat page sees the marker, shows the typing
  state, and polls `/api/chat/result/{task_id}` until the reply arrives or a ten-minute
  deadline passes.
- Replies are deduplicated so the original response path and the inbox poll cannot render
  the same message twice.
- Empty states use clearly separated demo generators (`generateDemoPlan`,
  `generateDemoGrocery`); they are not mixed into a real run.

## Consequences

**Good:** a refresh no longer loses a completed run, and users keep their conversation
across tab closes. No chat-message table or server-side conversation state is required.
The inflight marker makes recovery deterministic rather than a guess.

**Costly:** history is device-local and per browser, not synced; a new device starts empty.
The inbox is in-memory, so a backend restart loses undelivered replies, and multiple
instances do not share it. The result endpoint is keyed only by `task_id`, with no
ownership check. Polling every five seconds for up to ten minutes is wasteful for a run
that has actually died. Multiple tabs share `localStorage` history but not `sessionStorage`
tokens, so tabs can disagree about who is signed in. A token that expires during a long
wait surfaces as a 401 and requires signing in again.

## Code map

- `frontend/src/utils/sessionStore.js` — token, identity, inflight storage
- `frontend/src/context/AppContext.jsx` — per-user messages and pending workflow
- `frontend/src/pages/ChatPage.jsx` — inflight recovery, inbox polling, dedupe
- `frontend/src/utils/api.js` — `API_BASE`, `authFetch`, `fetchChatResult`
- `backend/app/api/routes/chat.py` — response inbox
- `frontend/src/utils/workflowSimulator.js` — empty-state demo data
