# Sessions: short-lived signed tokens plus a revocable, rotating refresh key

**Status:** Accepted · **Recorded:** 2026-10-22 · **Updated:** 2026-10-25

## Context

Early versions identified the user from a request parameter, so anyone could read or edit
another account's profile by guessing an id. A full auth framework would add dependencies
and a session store that this project does not otherwise need. The frontend is a separate
origin (Render serves the site and the API from different `onrender.com` subdomains, which
browsers treat as different *sites*), so cookies would add CORS and CSRF work and are blocked
as third-party cookies by Safari and Brave.

The first version of this decision used one 15-minute token with no renewal. That was safe
but unusable: people were signed out mid-conversation, and closing the tab signed them out.
The 2026-10-25 update keeps the short token and adds a long-lived refresh key.

## Decision

Two keys, the way Gmail or ChatGPT keep you signed in.

**Access token (short key).** A signed bearer token that proves who you are on each request.

- Format: `base64url(user_id:expiry_epoch).base64url(HMAC-SHA256(payload))`.
- Lifetime: **15 minutes, absolute**. The server stores no state for it: signature + expiry
  are the whole check, compared in constant time.
- Protected routes depend on `get_authenticated_user_id`; plan routes also check ownership.

**Refresh key (long key).** A random 384-bit value only ever sent to `/api/auth/refresh`
and `/api/auth/logout`.

- Lifetime: **30 days since last use** (`REFRESH_TOKEN_TTL_DAYS`, default 30). Each use
  hands out a fresh key, so someone who keeps using the app is never asked to sign in; someone
  who stays away for 30 days is.
- Stored in the `refresh_tokens` table as a **SHA-256 hash only**, so a database leak does
  not hand out sessions. One row per sign-in per device, not one per user. Columns:
  `user_id`, `token_hash`, `family_id`, `created_at`, `expires_at`, `rotated_at`,
  `revoked_at`.
- **Rotation:** using a key retires it (`rotated_at`) and inserts the next one in the same
  `family_id`.
- **Theft detection:** a retired key that comes back after a 20-second grace period means
  it was copied, so the whole family is revoked and both the thief and the real user must sign
  in again. Inside the grace period (two tabs refreshing at the same moment) the caller gets
  a new access token but no new refresh key, because the other tab already stored one.
- **Sign out** (`POST /api/auth/logout`) revokes the family, so the key stops working even if
  a copy exists. A retired key cannot slip through the grace window after a logout.
- Expired rows for a user are deleted when they sign in and on every refresh.
- The row's `user_id` has `ON DELETE CASCADE`, so deleting an account removes its keys.

**Passwords.** Argon2id (`argon2-cffi`), salted per password. Old unsalted or salted
SHA-256 hashes still verify and are silently upgraded to Argon2 on the next successful login.
An account with no stored hash can never log in. Login failures return one message
("Invalid email or password.") whether or not the email exists, and an email is locked out for
15 minutes after 5 failed attempts (kept in memory, so it resets on restart). Registration
enforces an 8–128 character password and a valid email on the server, not only in the form.

**Browser storage.** The access token, refresh key and cached identity live in `localStorage`,
so closing the tab or the browser keeps you signed in. Only the "waiting for a reply" note
stays in `sessionStorage`, because it describes *this tab*. When a request gets a 401 and a
refresh key exists, the frontend makes **one** shared call to `/auth/refresh` (concurrent
failures reuse it), stores the new keys and retries the original request once. If the server
cannot be reached during that call the user is *not* signed out. If the key is rejected the
app reports the session as expired and returns to sign-in, keeping the chat history.

Other safeguards from the same review: `AUTH_TOKEN_SECRET` is set on Render; CORS allows only
`FRONTEND_ORIGINS` in production (any localhost port in development) and no credentials;
`/docs` is off in production; server errors return a short reference instead of the raw
exception text; `/api/chat/result/{task_id}` requires sign-in and only returns the caller's
own replies.

## Consequences

**Good:** identity is never taken from a client-supplied id. Active users are never signed
out, yet a leaked access token is useful for at most 15 minutes and a leaked refresh key can
be cancelled, and is cancelled automatically if it is replayed. A stolen database does not
contain usable keys or recoverable passwords.

**Costly:**

- The refresh key sits in `localStorage`, where a script injected into the page could read
  it. This is a deliberate trade for working across `onrender.com` subdomains and Safari; if the
  site and API ever share one domain, move it to an `httpOnly` cookie.
- The access token alone still cannot be revoked before its 15 minutes are up.
- The `refresh_tokens` table grows by one row per refresh (about every 15 active minutes) and
  keeps retired rows until they expire, which is what makes replay detection possible. That is
  a few thousand small rows per heavy user over 30 days.
- Login lockout is per process and in memory; it also lets someone lock a victim's email out
  for 15 minutes. Multiple server processes would each count separately.
- `/auth/refresh` and `/auth/logout` have no rate limit of their own yet.
- If `AUTH_TOKEN_SECRET` is unset the code still falls back to a public development secret
  and anyone can forge access tokens; production relies on the variable being set.
- The table is created by `create_all` at startup (see ADR 0012), so there is no migration
  record for it.

## Code map

- `backend/app/api/security.py` — access-token format, TTL, signing, verification
- `backend/app/api/routes/auth.py` — register, login, `/refresh`, `/logout`, password hashing
  and lockout
- `backend/app/db/repositories/refresh_tokens.py` — issue, rotate, revoke, theft detection
- `backend/app/db/models.py` — `RefreshToken` table
- `backend/app/api/deps.py` — bearer extraction and repository dependencies
- `backend/app/api/routes/plans.py` — ownership checks
- `frontend/src/utils/api.js` — `authFetch`, shared refresh, retry, sign-out call
- `frontend/src/utils/sessionStore.js` — persistent vs per-tab storage
- `frontend/src/context/AppContext.jsx` — sign-in, sign-out, expired-session handling
- `backend/app/config.py` — `AUTH_TOKEN_SECRET`, `REFRESH_TOKEN_TTL_DAYS`, `FRONTEND_ORIGINS`
- `backend/app/main.py` — CORS and production docs switch
- Tests: `backend/tests/unit/test_refresh_tokens.py`, `test_auth_passwords.py`,
  `test_cors_and_docs.py`, `test_chat_result_auth.py`
