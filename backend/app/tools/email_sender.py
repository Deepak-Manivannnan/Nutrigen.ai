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
}
_INTROS = {
    "signup": "Use this code to verify your email and finish creating your account.",
    "reset": "Use this code to reset your password.",
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
    if not settings.brevo_api_key or not settings.email_from:
        raise EmailSendError("Email sending is not configured.")
    payload = {
        "sender": {"name": settings.email_from_name or "NutriGen.ai", "email": settings.email_from},
        "to": [{"email": to_email}],
        "subject": _SUBJECTS.get(purpose, _SUBJECTS["signup"]),
        "htmlContent": _body(code, purpose, settings.email_code_ttl_minutes),
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
