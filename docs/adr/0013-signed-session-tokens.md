# Sessions are stateless HMAC tokens with a 15-minute absolute lifetime

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

Early versions identified the user from a request parameter, so anyone could read or edit
another account's profile by guessing an id. A full auth framework would add dependencies,
a session store, and refresh-token flows that this project does not otherwise need. The
frontend is a separate origin, so cookies would add CORS and CSRF work.

## Decision

Issue a signed bearer token at login and derive the user from it on every request.

- Format: `base64url(user_id:expiry_epoch).base64url(HMAC-SHA256(payload))`.
- Lifetime: **15 minutes, absolute**. Activity does not extend it; there is no refresh
  token.
- The server stores no session state: signature + expiry are the whole check, compared in
  constant time.
- Protected routes depend on `get_authenticated_user_id`; plan routes also check
  ownership on every read/write.
- Passwords are SHA-256 hex digests; the verifier also accepts an older `salt$hash` format.
- The frontend keeps the token in `sessionStorage`, so closing the tab signs the user out.

## Consequences

**Good:** identity is never taken from a client-supplied id. Any process can validate a
token without shared session storage, which suits a free single-instance deploy and a
future horizontal one. A leaked token stops working quickly.

**Costly:** 15 minutes is short enough to interrupt a slow form, and there is no renewal,
so users sign in again. There is no logout invalidation or revocation list — a copied token
is valid until it expires. The password scheme is **unsalted SHA-256**, which is below
current practice; the legacy salted branch is compatibility, not a target. Accounts with no
stored hash are currently accepted with any password, so legacy or credential-less rows are
a real takeover risk. If `AUTH_TOKEN_SECRET` is unset, the code falls back to a public
development secret and anyone can forge tokens. CORS is wide open (`*` with credentials)
for development convenience. The upgrade path is Argon2/bcrypt, a required secret, stricter
CORS, and refresh or sliding sessions.

## Code map

- `backend/app/api/security.py` — token format, TTL, signing, verification
- `backend/app/api/routes/auth.py` — register/login, password hashing
- `backend/app/api/deps.py` — bearer extraction and repository dependencies
- `backend/app/api/routes/plans.py` — ownership checks
- `frontend/src/utils/sessionStore.js` — session-scoped token storage
- `backend/app/config.py` — `AUTH_TOKEN_SECRET`
- `backend/app/main.py` — permissive CORS middleware
