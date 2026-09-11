"""
Email delivery.

Prefers Resend (RESEND_API_KEY) and falls back to SendGrid
(SENDGRID_API_KEY). Both are plain HTTPS POSTs — no vendor SDK.

The SendGrid key had been returning 401 Unauthorized since mid-2026,
which silently killed every digest run at the final step; delivery
errors now name the provider and status so a dead key is obvious.
"""
import requests
from src.config import RESEND_API_KEY, SENDGRID_API_KEY, TO_EMAIL, FROM_EMAIL


def send_email(subject: str, html_body: str):
    if not TO_EMAIL:
        raise RuntimeError("TO_EMAIL is not set — nowhere to send the digest")

    errors = []

    if RESEND_API_KEY:
        try:
            return _send_resend(subject, html_body)
        except Exception as e:
            errors.append(f"Resend: {e}")
            print(f"  ⚠️  Resend delivery failed: {e}")

    if SENDGRID_API_KEY:
        try:
            return _send_sendgrid(subject, html_body)
        except Exception as e:
            errors.append(f"SendGrid: {e}")
            print(f"  ⚠️  SendGrid delivery failed: {e}")

    if not errors:
        raise RuntimeError(
            "No email provider configured — set RESEND_API_KEY or SENDGRID_API_KEY"
        )
    raise RuntimeError("Email delivery failed — " + "; ".join(errors))


def _send_resend(subject: str, html_body: str):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from":    FROM_EMAIL,
            "to":      [TO_EMAIL],
            "subject": subject,
            "html":    html_body,
        },
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    print(f"  ✉️  Email sent via Resend: {resp.status_code}")
    return resp


def _send_sendgrid(subject: str, html_body: str):
    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {SENDGRID_API_KEY}"},
        json={
            "personalizations": [{"to": [{"email": TO_EMAIL}]}],
            "from":    {"email": FROM_EMAIL},
            "subject": subject,
            "content": [{"type": "text/html", "value": html_body}],
        },
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    print(f"  ✉️  Email sent via SendGrid: {resp.status_code}")
    return resp
