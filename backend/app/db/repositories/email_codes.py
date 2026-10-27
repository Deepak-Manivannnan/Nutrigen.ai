"""
One-time email codes: create, check, and consume.

Codes and tickets are stored only as HMAC hashes. Times are naive UTC.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.db.models import EmailCode


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _secret() -> bytes:
    return (settings.auth_token_secret or "nutrigen-dev-email-code-secret").encode()


def hash_value(email: str, purpose: str, value: str) -> str:
    msg = f"{purpose}|{email.lower()}|{value}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


class CodeError(Exception):
    """Reason is safe to show the user."""

    def __init__(self, reason: str, status: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class EmailCodeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _rows(self, email: str, purpose: str):
        res = await self.session.execute(
            select(EmailCode)
            .where(EmailCode.email == email, EmailCode.purpose == purpose)
            .order_by(EmailCode.created_at.desc())
        )
        return list(res.scalars().all())

    async def create(self, email: str, purpose: str) -> str:
        """Make a new code (replacing older ones). Enforces cooldown and hourly cap."""
        now = utcnow()
        rows = await self._rows(email, purpose)
        if rows:
            wait = (rows[0].created_at + timedelta(seconds=settings.email_code_resend_seconds) - now).total_seconds()
            if wait > 0:
                raise CodeError(f"Please wait {int(wait) + 1} seconds before asking for a new code.", 429)
        recent = [r for r in rows if r.created_at > now - timedelta(hours=1)]
        if len(recent) >= settings.email_code_max_per_hour:
            raise CodeError("Too many codes requested for this email. Please try again in an hour.", 429)

        code = f"{secrets.randbelow(1_000_000):06d}"
        # Older codes stop working; only keep the last hour of rows for the cap.
        for r in rows:
            if r.created_at <= now - timedelta(hours=1):
                await self.session.delete(r)
            else:
                r.expires_at = now
                r.ticket_hash = None
        self.session.add(EmailCode(
            email=email, purpose=purpose, code_hash=hash_value(email, purpose, code),
            attempts=0, expires_at=now + timedelta(minutes=settings.email_code_ttl_minutes),
            created_at=now,
        ))
        await self.session.commit()
        return code

    async def discard_latest(self, email: str, purpose: str) -> None:
        """Drop the newest code, e.g. when its email could not be sent, so it
        doesn't count against the resend wait or the hourly cap."""
        rows = await self._rows(email, purpose)
        if rows:
            await self.session.delete(rows[0])
            await self.session.commit()

    async def verify(self, email: str, purpose: str, code: str) -> str:
        """Check the code. On success returns a one-time ticket for the next step."""
        now = utcnow()
        rows = await self._rows(email, purpose)
        row = rows[0] if rows else None
        if not row or row.expires_at <= now or row.verified_at:
            raise CodeError("This code has expired. Please ask for a new one.")
        if row.attempts >= settings.email_code_max_attempts:
            raise CodeError("Too many wrong tries. Please ask for a new code.")
        if not hmac.compare_digest(row.code_hash, hash_value(email, purpose, (code or "").strip())):
            row.attempts += 1
            await self.session.commit()
            left = settings.email_code_max_attempts - row.attempts
            raise CodeError("That code is not correct." + (f" {left} tries left." if left > 0 else " Please ask for a new code."))
        ticket = secrets.token_urlsafe(32)
        row.verified_at = now
        row.ticket_hash = hash_value(email, purpose, ticket)
        await self.session.commit()
        return ticket

    async def consume_ticket(self, email: str, purpose: str, ticket: str) -> None:
        """Accept the ticket once, within the verified window, then delete the codes."""
        now = utcnow()
        rows = await self._rows(email, purpose)
        row = rows[0] if rows else None
        ok = (
            row is not None and row.verified_at is not None and row.ticket_hash
            and row.verified_at > now - timedelta(minutes=settings.email_verified_window_minutes)
            and hmac.compare_digest(row.ticket_hash, hash_value(email, purpose, ticket or ""))
        )
        if not ok:
            raise CodeError("Your email verification has expired. Please verify your email again.")
        await self.session.execute(
            delete(EmailCode).where(EmailCode.email == email, EmailCode.purpose == purpose)
        )
        await self.session.commit()
