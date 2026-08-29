"""Email delivery via SendGrid."""
import time

import python_http_client
import sendgrid
from sendgrid.helpers.mail import Mail

from src.config import SENDGRID_API_KEY, TO_EMAIL, FROM_EMAIL

# Auth failures are permanent — retrying a 401/403 every day just burns quota
# and hides the real problem. Only transient statuses get a bounded retry.
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2

UNAUTHORIZED_HELP = """
SendGrid rejected SENDGRID_API_KEY (HTTP 401 Unauthorized).

The digest itself built fine — only delivery failed. A 401 is purely an
authentication failure, so no code change in this repo can fix it: the key
has to be replaced. Check these in order:

  1. The key was deleted or revoked in the SendGrid dashboard
     (Settings -> API Keys). A revoked key returns 401 forever.
  2. The key has no Mail Send permission. A restricted-access key needs
     "Mail Send" set to Full Access.
  3. The SendGrid account is suspended or under review. The dashboard shows
     a banner, and every key on the account 401s until it is resolved.
  4. The GitHub secret has stray whitespace or quote characters around the
     value. A key looks like SG.<id>.<secret> with no trailing newline.

Fix: create a new API key with Mail Send access, then update the
SENDGRID_API_KEY secret at
Settings -> Secrets and variables -> Actions in the email-digest repo.

Note: an unverified FROM_EMAIL sender identity returns 403, not 401, so
sender verification is not the cause here.
""".strip()


class MailerError(RuntimeError):
    """Raised when the digest cannot be delivered."""


def _api_key() -> str:
    """Return the API key, trimmed, with a loud warning if it looked wrong."""
    raw = SENDGRID_API_KEY
    if not raw or not raw.strip():
        raise MailerError(
            "SENDGRID_API_KEY is unset or empty. In GitHub Actions, set it "
            "under Settings -> Secrets and variables -> Actions; locally, add "
            "it to .env."
        )

    key = raw.strip()
    if key != raw:
        print(
            "  ⚠️  SENDGRID_API_KEY had surrounding whitespace — trimmed it. "
            "Re-paste the secret without a trailing newline."
        )
    if (key.startswith('"') and key.endswith('"')) or (
        key.startswith("'") and key.endswith("'")
    ):
        print(
            "  ⚠️  SENDGRID_API_KEY is wrapped in quotes — stripping them. "
            "Store the raw key, without quotes."
        )
        key = key[1:-1].strip()
    if not key.startswith("SG."):
        print(
            "  ⚠️  SENDGRID_API_KEY does not start with 'SG.' — this does not "
            "look like a SendGrid API key. A 401 below is expected."
        )
    return key


def _client() -> sendgrid.SendGridAPIClient:
    return sendgrid.SendGridAPIClient(api_key=_api_key())


def _detail(err: python_http_client.exceptions.HTTPError) -> str:
    """SendGrid puts the actual reason in the response body — surface it."""
    body = getattr(err, "body", b"") or b""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    return body.strip()


def check_credentials() -> bool:
    """
    Best-effort auth preflight, run before the expensive digest work so a dead
    key shows up on line 1 of the log instead of in a traceback 90 seconds in.

    Never raises and never blocks the run: a restricted-access key can lack
    the scopes.read permission and still send mail perfectly well, so only a
    401 is treated as a real signal. Returns True when the key looks usable.
    """
    try:
        _client().client.scopes.get()
        print("  ✅ SendGrid: API key accepted")
        return True
    except python_http_client.exceptions.UnauthorizedError as e:
        print("  ❌ SendGrid: API key rejected (401) — the digest will not send")
        detail = _detail(e)
        if detail:
            print(f"     SendGrid said: {detail}")
        print(UNAUTHORIZED_HELP)
        return False
    except MailerError as e:
        print(f"  ❌ SendGrid: {e}")
        return False
    except Exception as e:
        # Restricted key, transient network blip, anything else — not a
        # verdict on the key. Let the real send be the judge.
        print(f"  ⚠️  SendGrid preflight inconclusive ({type(e).__name__}: {e})")
        return True


def send_email(subject: str, html_body: str):
    sg = _client()
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject=subject,
        html_content=html_body,
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = sg.send(message)
            print(f"Email sent: {response.status_code}")
            return response

        except python_http_client.exceptions.UnauthorizedError as e:
            detail = _detail(e)
            raise MailerError(
                UNAUTHORIZED_HELP
                + (f"\n\nSendGrid said: {detail}" if detail else "")
            ) from e

        except python_http_client.exceptions.ForbiddenError as e:
            detail = _detail(e)
            raise MailerError(
                "SendGrid refused the send (HTTP 403 Forbidden). The key "
                "authenticated, so this is usually an unverified sender: "
                f"FROM_EMAIL ({FROM_EMAIL}) must be a verified Single Sender "
                "or sit on an authenticated domain in SendGrid "
                "(Settings -> Sender Authentication)."
                + (f"\n\nSendGrid said: {detail}" if detail else "")
            ) from e

        except python_http_client.exceptions.HTTPError as e:
            status = getattr(e, "status_code", None)
            if status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                detail = _detail(e)
                raise MailerError(
                    f"SendGrid send failed with HTTP {status}."
                    + (f"\n\nSendGrid said: {detail}" if detail else "")
                ) from e
            wait = BACKOFF_BASE_SECONDS ** attempt
            print(
                f"  ⚠️  SendGrid HTTP {status} (attempt {attempt}/{MAX_ATTEMPTS})"
                f" — retrying in {wait}s"
            )
            time.sleep(wait)

        except (TimeoutError, OSError) as e:
            if attempt == MAX_ATTEMPTS:
                raise MailerError(f"SendGrid unreachable: {e}") from e
            wait = BACKOFF_BASE_SECONDS ** attempt
            print(
                f"  ⚠️  SendGrid unreachable ({e}) "
                f"(attempt {attempt}/{MAX_ATTEMPTS}) — retrying in {wait}s"
            )
            time.sleep(wait)
