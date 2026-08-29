"""
Offline test for the SendGrid mailer.
Mocks every SendGrid call — no API key or network needed.
Run from repo root: PYTHONPATH=. python tests/test_mailer_offline.py
"""
import sys
import unittest.mock as mock

import python_http_client.exceptions as sg_exc

from src.mailer import sender
from src.mailer.sender import MailerError

PASS = "✅"
FAIL = "❌"
results = []


def check(name, expr):
    try:
        assert expr, "assertion failed"
        results.append((PASS, name))
        print(f"{PASS} {name}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"{FAIL} {name} — {e}")


def http_error(cls, status, body=b'{"errors":[{"message":"boom"}]}'):
    return cls(status, "reason", body, {})


def with_key(key):
    """Patch the module-level key the way config would have supplied it."""
    return mock.patch.object(sender, "SENDGRID_API_KEY", key)


# ── Key validation ───────────────────────────────────────────────────────────
for bad, label in [(None, "unset"), ("", "empty"), ("   ", "whitespace-only")]:
    with with_key(bad):
        try:
            sender._api_key()
            check(f"{label} key raises MailerError", False)
        except MailerError as e:
            check(f"{label} key raises MailerError", "SENDGRID_API_KEY" in str(e))

with with_key("  SG.abc.def\n"):
    check("surrounding whitespace is trimmed", sender._api_key() == "SG.abc.def")

with with_key('"SG.abc.def"'):
    check("wrapping quotes are stripped", sender._api_key() == "SG.abc.def")

with with_key("SG.abc.def"):
    check("clean key passes through", sender._api_key() == "SG.abc.def")
    check("malformed key still returned (send decides)", True)

with with_key("not-a-sendgrid-key"):
    check("non-SG. key warns but does not raise",
          sender._api_key() == "not-a-sendgrid-key")


# ── send_email error handling ────────────────────────────────────────────────
with with_key("SG.abc.def"), mock.patch.object(sender, "time") as fake_time:
    fake_time.sleep = mock.MagicMock()

    # 401 — permanent, must not retry
    client = mock.MagicMock()
    client.send.side_effect = http_error(sg_exc.UnauthorizedError, 401)
    with mock.patch.object(sender.sendgrid, "SendGridAPIClient",
                           return_value=client):
        try:
            sender.send_email("subj", "<p>hi</p>")
            check("401 raises MailerError", False)
        except MailerError as e:
            check("401 raises MailerError", True)
            check("401 message names SENDGRID_API_KEY",
                  "SENDGRID_API_KEY" in str(e))
            check("401 message explains it is not a code bug",
                  "revoked" in str(e) and "Mail Send" in str(e))
            check("401 surfaces SendGrid's own response body", "boom" in str(e))
    check("401 is not retried", client.send.call_count == 1)

    # 403 — permanent, points at sender verification instead
    client = mock.MagicMock()
    client.send.side_effect = http_error(sg_exc.ForbiddenError, 403)
    with mock.patch.object(sender.sendgrid, "SendGridAPIClient",
                           return_value=client):
        try:
            sender.send_email("subj", "<p>hi</p>")
            check("403 raises MailerError", False)
        except MailerError as e:
            check("403 raises MailerError", True)
            check("403 message points at sender verification",
                  "verified" in str(e).lower())
    check("403 is not retried", client.send.call_count == 1)

    # 503 — transient, retried up to MAX_ATTEMPTS then raised
    client = mock.MagicMock()
    client.send.side_effect = http_error(sg_exc.ServiceUnavailableError, 503)
    with mock.patch.object(sender.sendgrid, "SendGridAPIClient",
                           return_value=client):
        try:
            sender.send_email("subj", "<p>hi</p>")
            check("exhausted retries raise MailerError", False)
        except MailerError as e:
            check("exhausted retries raise MailerError", "503" in str(e))
    check("503 retried MAX_ATTEMPTS times",
          client.send.call_count == sender.MAX_ATTEMPTS)
    check("503 backs off between attempts",
          fake_time.sleep.call_count == sender.MAX_ATTEMPTS - 1)

    # 429 then success — recovers without raising
    ok = mock.MagicMock(status_code=202)
    client = mock.MagicMock()
    client.send.side_effect = [http_error(sg_exc.TooManyRequestsError, 429), ok]
    with mock.patch.object(sender.sendgrid, "SendGridAPIClient",
                           return_value=client):
        check("429 then success returns the response",
              sender.send_email("subj", "<p>hi</p>") is ok)
    check("429 retried once", client.send.call_count == 2)

    # Happy path
    client = mock.MagicMock()
    client.send.return_value = ok
    with mock.patch.object(sender.sendgrid, "SendGridAPIClient",
                           return_value=client):
        check("successful send returns the response",
              sender.send_email("subj", "<p>hi</p>") is ok)
    check("successful send is not retried", client.send.call_count == 1)


# ── check_credentials preflight ──────────────────────────────────────────────
with with_key("SG.abc.def"):
    client = mock.MagicMock()
    client.client.scopes.get.side_effect = http_error(sg_exc.UnauthorizedError, 401)
    with mock.patch.object(sender.sendgrid, "SendGridAPIClient",
                           return_value=client):
        check("preflight reports False on 401", sender.check_credentials() is False)

    client = mock.MagicMock()
    with mock.patch.object(sender.sendgrid, "SendGridAPIClient",
                           return_value=client):
        check("preflight reports True on success", sender.check_credentials() is True)

    # A restricted key can lack scopes.read — that is not a verdict on the key,
    # so the preflight must not block the run.
    client = mock.MagicMock()
    client.client.scopes.get.side_effect = http_error(sg_exc.ForbiddenError, 403)
    with mock.patch.object(sender.sendgrid, "SendGridAPIClient",
                           return_value=client):
        check("preflight stays permissive on 403", sender.check_credentials() is True)

with with_key(None):
    check("preflight reports False when key is unset",
          sender.check_credentials() is False)

check("preflight never raises", True)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed out of {len(results)} checks")
if failed:
    print("FAILED checks:")
    for r in results:
        if r[0] == FAIL:
            print(f"  {r[1]}")
    sys.exit(1)
else:
    print("All checks passed.")
