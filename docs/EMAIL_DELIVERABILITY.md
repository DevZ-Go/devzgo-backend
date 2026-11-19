# Email deliverability (DevZ-Go)

## Providers

- Prefer **Resend** with a verified custom domain (`EMAIL_FROM` on that domain).
- **SMTP** (`SMTP_HOST` set) takes priority over Resend when both are configured.
- Without either, the API enqueues outbox rows when applicable but `send_email` logs a skip — it never prints OTP codes or reset tokens.

## Worker

Run a separate process that drains `email_outbox`:

```bash
python -m app.worker.email_worker
```

High-priority paths (OTP, password reset) also call `try_send_now` so delivery works briefly without the worker; the worker is still required for retries and backlog.

## Flags

| Variable | Purpose |
| --- | --- |
| `REQUIRE_EMAIL_VERIFICATION` | Pending registration + OTP before a `users` row exists. Default off. |
| `EMAIL_OTP_PEPPER` | HMAC pepper for OTP hashes (else `SECRET_KEY`). |
| `RESEND_WEBHOOK_SECRET` | Required for `POST /webhooks/resend` signature checks. |

## Domains and Microsoft inbox placement

- Verify SPF, DKIM, and DMARC for the sending domain in Resend (or your SMTP provider).
- Avoid `onboarding@resend.dev` in production — Resend restricts that sender to the account owner.
- Track bounce/complaint classes via Resend webhooks into `email_delivery_events` (`recipient_domain_class` includes `microsoft` / `google` / `other`).

## Categories

Security emails (verification, password reset/changed, email changed, account lifecycle) are always eligible.

Optional categories respect profile prefs and signed `/email/unsubscribe?token=…` links:

- connection requests / milestones
- messages digest
- collaborations / project activity
- recruiter / weekly summary

## Migration

Apply deliberately (do not enable `MIGRATE_ON_START` on shared Neon):

```bash
alembic upgrade head
```

Revision `035_email_system` adds outbox, pending registrations, delivery events, email-change requests, and preference columns. Existing users keep `email_verified=true`.
