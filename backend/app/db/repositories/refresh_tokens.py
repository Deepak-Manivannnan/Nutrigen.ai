"""Long-lived "stay signed in" keys.

The short key (signed token, 15 minutes) proves who you are on each request.
This long key is only ever shown to /auth/refresh, which trades it for a new
short key and a new long key. Using a key retires it, so a key that turns up a
second time later is a sign it was copied; the whole sign-in is then cancelled.
"""
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.db.models import RefreshToken

# Two tabs can refresh at the same moment with the same key. The second one
# arrives just after the first retired it. That is not theft, so a key retired
# less than this long ago still gets a new short key (but no new long key: the
# other tab already stored one).
ROTATION_GRACE = timedelta(seconds=20)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(raw: str) -> str:
    # The key is 384 random bits, so a plain fast hash is enough here.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class RotationResult:
    user_id: int
    # None when the caller lost a harmless race (see ROTATION_GRACE).
    refresh_token: Optional[str]


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- storage primitives (the only methods that touch the database) -----
    async def _find(self, token_hash: str) -> Optional[RefreshToken]:
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def _add(self, record: RefreshToken) -> None:
        self.session.add(record)

    async def _revoke_family(self, family_id: str, when: datetime) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=when)
        )

    async def _purge_expired(self, user_id: int, now: datetime) -> None:
        await self.session.execute(
            delete(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.expires_at < now)
        )

    async def _commit(self) -> None:
        await self.session.commit()

    # --- behaviour ---------------------------------------------------------
    def _new_record(self, user_id: int, family_id: str, now: datetime):
        raw = secrets.token_urlsafe(48)
        record = RefreshToken(
            user_id=user_id,
            token_hash=hash_token(raw),
            family_id=family_id,
            created_at=now,
            expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        )
        return raw, record

    async def issue(self, user_id: int) -> str:
        """Start a new sign-in: returns the raw long key (only ever held by the browser)."""
        now = utcnow()
        await self._purge_expired(user_id, now)
        raw, record = self._new_record(user_id, str(uuid.uuid4()), now)
        await self._add(record)
        await self._commit()
        return raw

    async def rotate(self, raw: str) -> Optional[RotationResult]:
        """Trade a long key for the next one. None means: sign in again."""
        if not raw:
            return None
        now = utcnow()
        record = await self._find(hash_token(raw))
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            return None

        # Every refresh adds a row, so an active user who never signs in again
        # would otherwise collect expired rows forever. Clean up as we go.
        await self._purge_expired(record.user_id, now)

        if record.rotated_at is not None:
            if now - record.rotated_at <= ROTATION_GRACE:
                return RotationResult(user_id=record.user_id, refresh_token=None)
            # A retired key came back after the grace period: treat it as
            # stolen and cancel this whole sign-in.
            await self._revoke_family(record.family_id, now)
            await self._commit()
            return None

        record.rotated_at = now
        new_raw, new_record = self._new_record(record.user_id, record.family_id, now)
        await self._add(new_record)
        await self._commit()
        return RotationResult(user_id=record.user_id, refresh_token=new_raw)

    async def revoke(self, raw: str) -> None:
        """Sign out: cancel this sign-in (every key in its family)."""
        if not raw:
            return
        record = await self._find(hash_token(raw))
        if record is None:
            return
        await self._revoke_family(record.family_id, utcnow())
        await self._commit()
