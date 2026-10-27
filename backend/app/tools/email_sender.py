"""
Send one-time codes by email through Brevo's HTTP API.

Render's free plan blocks SMTP, so this uses Brevo's REST endpoint instead.
Plain function, no LLM. Raises EmailSendError when the email could not be sent.
"""
import html

import httpx

from backend.app.config import settings

BREVO_URL = "https://api.brevo.com/v3/smtp/email"

_SUBJECTS = {
    "signup": "Your NutriGen.ai verification code",
    "reset": "Your NutriGen.ai password reset code",
    "delete": "Confirm deleting your NutriGen.ai account",
}
_INTROS = {
    "signup": "Use this code to verify your email and finish creating your account.",
    "reset": "Use this code to reset your password.",
    "delete": "Use this code to confirm you want to permanently delete your account.",
}


class EmailSendError(Exception):
    pass


def _body(code: str, purpose: str, minutes: int) -> str:
    intro = html.escape(_INTROS.get(purpose, _INTROS["signup"]))
    return (
        "<div style='font-family:Arial,sans-serif;max-width:480px;margin:auto;color:#1f2937'>"
        "<h2 style='color:#16a34a;margin-bottom:8px'>NutriGen.ai</h2>"
        f"<p>{intro}</p>"
        f"<p style='font-size:30px;font-weight:bold;letter-spacing:6px;margin:20px 0'>{html.escape(code)}</p>"
        f"<p>This code expires in {minutes} minutes. If you didn't ask for it, you can ignore this email.</p>"
        "</div>"
    )


async def send_code_email(to_email: str, code: str, purpose: str) -> None:
    await _send(to_email, _SUBJECTS.get(purpose, _SUBJECTS["signup"]),
                _body(code, purpose, settings.email_code_ttl_minutes))


async def send_account_deleted_email(to_email: str, name: str = "", used_google: bool = False) -> None:
    hello = f"Hi {html.escape(name)}," if name else "Hi,"
    body = (
        "<div style='font-family:Arial,sans-serif;max-width:480px;margin:auto;color:#1f2937'>"
        "<h2 style='color:#16a34a;margin-bottom:8px'>NutriGen.ai</h2>"
        f"<p>{hello}</p>"
        "<p>Your NutriGen.ai account and all of its data (profile, meal plans and grocery lists) "
        "have been permanently deleted.</p>"
        + ("<p>You signed in with Google, so you can also remove NutriGen.ai from your Google Account "
           "under <b>Security &rarr; Your connections to third-party apps &amp; services</b>.</p>"
           if used_google else "")
        + "<p>If you didn't do this, please reply to this email right away. You're always welcome "
        "to create a new account with this email.</p>"
        "</div>"
    )
    await _send(to_email, "Your NutriGen.ai account was deleted", body)


async def _send(to_email: str, subject: str, html_content: str) -> None:
    if not settings.brevo_api_key or not settings.email_from:
        raise EmailSendError("Email sending is not configured.")
    payload = {
        "sender": {"name": settings.email_from_name or "NutriGen.ai", "email": settings.email_from},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                BREVO_URL, json=payload,
                headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
            )
    except httpx.HTTPError as e:
        raise EmailSendError(f"Brevo request failed: {type(e).__name__}") from e
    if resp.status_code >= 300:
        raise EmailSendError(f"Brevo returned {resp.status_code}")
