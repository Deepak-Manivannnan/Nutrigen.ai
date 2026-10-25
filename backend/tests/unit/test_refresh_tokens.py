"""Stay-signed-in keys: issuing, rotating, theft detection, sign out (no database)."""
from datetime import timedelta

import pytest
from fastapi import HTTPException

from backend.app.api.routes import auth
from backend.app.api.security import decode_token
from backend.app.config import settings
from backend.app.db.repositories import refresh_tokens as rt
from backend.app.db.repositories.refresh_tokens import RefreshTokenRepository


class MemoryRepo(RefreshTokenRepository):
    """Same behaviour, but the five storage primitives use a list."""

    def __init__(self):
        self.rows = []

    async def _find(self, token_hash):
        return next((r for r in self.rows if r.token_hash == token_hash), None)

    async def _add(self, record):
        self.rows.append(record)

    async def _revoke_family(self, family_id, when):
        for r in self.rows:
            if r.family_id == family_id and r.revoked_at is None:
                r.revoked_at = when

    async def _purge_expired(self, user_id, now):
        self.rows = [r for r in self.rows if not (r.user_id == user_id and r.expires_at < now)]

    async def _commit(self):
        pass


def _age(repo, seconds=0, days=0):
    """Pretend the stored rows were written `seconds`/`days` ago."""
    delta = timedelta(seconds=seconds, days=days)
    for r in repo.rows:
        r.created_at -= delta
        r.expires_at -= delta
        if r.rotated_at:
            r.rotated_at -= delta


async def test_only_a_hash_is_stored_and_key_is_long_lived():
    repo = MemoryRepo()
    raw = await repo.issue(7)
    row = repo.rows[0]
    assert raw not in (row.token_hash, row.family_id)
    assert len(row.token_hash) == 64
    assert (row.expires_at - row.created_at).days == settings.refresh_token_ttl_days


async def test_rotate_gives_new_key_and_retires_old_one():
    repo = MemoryRepo()
    first = await repo.issue(7)
    res = await repo.rotate(first)
    assert res.user_id == 7 and res.refresh_token and res.refresh_token != first
    assert len(repo.rows) == 2
    assert repo.rows[0].family_id == repo.rows[1].family_id
    # The new key keeps working (staying signed in while in use).
    again = await repo.rotate(res.refresh_token)
    assert again.refresh_token


async def test_unknown_or_empty_key_is_rejected():
    repo = MemoryRepo()
    assert await repo.rotate("") is None
    assert await repo.rotate("not-a-real-key") is None


async def test_expired_key_is_rejected():
    repo = MemoryRepo()
    raw = await repo.issue(7)
    _age(repo, days=settings.refresh_token_ttl_days + 1)
    assert await repo.rotate(raw) is None


async def test_unused_key_dies_but_use_extends_life():
    repo = MemoryRepo()
    raw = await repo.issue(7)
    _age(repo, days=settings.refresh_token_ttl_days - 1)  # one day left
    res = await repo.rotate(raw)  # used just in time
    assert res.refresh_token
    new_row = repo.rows[-1]
    assert (new_row.expires_at - rt.utcnow()).days >= settings.refresh_token_ttl_days - 1


async def test_two_tabs_racing_do_not_sign_the_user_out():
    repo = MemoryRepo()
    raw = await repo.issue(7)
    await repo.rotate(raw)            # tab A
    second = await repo.rotate(raw)   # tab B, a moment later, same key
    assert second is not None and second.user_id == 7
    assert second.refresh_token is None   # B keeps the key A already stored
    assert all(r.revoked_at is None for r in repo.rows)


async def test_reused_old_key_after_grace_cancels_the_whole_sign_in():
    repo = MemoryRepo()
    raw = await repo.issue(7)
    newest = (await repo.rotate(raw)).refresh_token
    _age(repo, seconds=rt.ROTATION_GRACE.total_seconds() + 5)
    assert await repo.rotate(raw) is None          # thief replays the old key
    assert await repo.rotate(newest) is None       # real user's key is cancelled too


async def test_sign_in_on_another_device_is_unaffected_by_theft_response():
    repo = MemoryRepo()
    phone = await repo.issue(7)
    laptop = await repo.issue(7)
    await repo.revoke(phone)
    assert await repo.rotate(phone) is None
    assert (await repo.rotate(laptop)).refresh_token


async def test_logout_cancels_key_even_inside_grace_window():
    repo = MemoryRepo()
    raw = await repo.issue(7)
    newest = (await repo.rotate(raw)).refresh_token
    await repo.revoke(newest)
    assert await repo.rotate(newest) is None
    assert await repo.rotate(raw) is None   # retired key must not slip through the grace window


async def test_issue_cleans_out_expired_rows_for_that_user_only():
    repo = MemoryRepo()
    await repo.issue(7)
    await repo.issue(8)
    _age(repo, days=settings.refresh_token_ttl_days + 1)
    await repo.issue(7)
    assert sorted(r.user_id for r in repo.rows) == [7, 8]
    assert sum(1 for r in repo.rows if r.user_id == 7) == 1


async def test_refresh_endpoint_returns_working_short_token_and_new_key():
    repo = MemoryRepo()
    raw = await repo.issue(7)
    out = await auth.refresh(auth.RefreshRequest(refresh_token=raw), repo)
    assert decode_token(out.token) == 7
    assert out.refresh_token and out.refresh_token != raw


async def test_refresh_endpoint_rejects_bad_key_with_401():
    with pytest.raises(HTTPException) as exc:
        await auth.refresh(auth.RefreshRequest(refresh_token="nope"), MemoryRepo())
    assert exc.value.status_code == 401


async def test_logout_endpoint_stops_the_key_working():
    repo = MemoryRepo()
    raw = await repo.issue(7)
    await auth.logout(auth.RefreshRequest(refresh_token=raw), repo)
    with pytest.raises(HTTPException):
        await auth.refresh(auth.RefreshRequest(refresh_token=raw), repo)
    # Signing out with an unknown key is harmless.
    await auth.logout(auth.RefreshRequest(refresh_token="unknown"), repo)


async def test_rotate_also_cleans_expired_rows_for_that_user():
    repo = MemoryRepo()
    live = await repo.issue(7)
    await repo.issue(7)
    repo.rows[1].expires_at = rt.utcnow() - timedelta(days=1)   # one leftover has expired
    await repo.rotate(live)
    assert len(repo.rows) == 2                                   # live row + its replacement
    assert all(r.expires_at > rt.utcnow() for r in repo.rows)
