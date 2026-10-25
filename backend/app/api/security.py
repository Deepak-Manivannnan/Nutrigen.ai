"""Signed session tokens.

A deliberately small, dependency-free replacement for a full auth library. The
token carries the user id and an expiry, signed with HMAC-SHA256, so the server
can tell whether a request belongs to a signed-in user without keeping any
server-side session state.

Format:  <base64url(user_id:expiry_epoch)>.<base64url(hmac)>

This is what closes the gap where any caller could read or edit anyone's
profile by guessing a user id: endpoints now identify the user from the token,
never from a request parameter.
"""
import base64
import hashlib
import hmac
import time

from backend.app.config import settings

# 15 minutes: this token is sent with every request, so if it leaks - copied
# out of storage or replayed from a log - it stops being useful almost
# immediately.
#
# It is an absolute deadline, not an idle timeout. Staying signed in is the job
# of the long-lived refresh key (see db/repositories/refresh_tokens.py and
# POST /auth/refresh): when this token runs out the browser quietly trades that
# key for a new one, so someone who keeps using the app is never asked to sign
# in again. Without a valid refresh key the API answers 401 and the user must
# sign in.
TOKEN_TTL_SECONDS = 15 * 60


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _secret() -> bytes:
    return (settings.auth_token_secret or "nutrigen-dev-secret").encode("utf-8")


def _sign(payload: str) -> str:
    digest = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64encode(digest)


def create_token(user_id: int) -> str:
    """Issue a signed token identifying `user_id`."""
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{user_id}:{expires_at}"
    return f"{_b64encode(payload.encode())}.{_sign(payload)}"


def decode_token(token: str):
    """Return the user id if the token is authentic and unexpired, else None."""
    if not token or "." not in token:
        return None
    encoded_payload, provided_signature = token.rsplit(".", 1)
    try:
        payload = _b64decode(encoded_payload).decode("utf-8")
    except Exception:
        return None

    # Constant-time compare so a forged signature cannot be probed byte by byte.
    if not hmac.compare_digest(_sign(payload), provided_signature):
        return None

    try:
        user_id_raw, expires_at_raw = payload.split(":")
        if int(expires_at_raw) < int(time.time()):
            return None
        return int(user_id_raw)
    except (ValueError, TypeError):
        return None
