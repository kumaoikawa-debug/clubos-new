# ClubOS NEW v0.25 · Production Hardening

**Status: security-hardening code and offline regressions complete; NOT cleared for live launch.**

## What changed

- Production uses server-side account identity (`platform`, `club`, `leader`, `member`) instead of trusting `club_id`/`user_id` from the browser. Cookie: 1-hour opaque revocable session, HttpOnly/Secure/SameSite=Strict. CSRF token required for state-changing cookie actions; invalid Origin rejected. Bearer auth is reserved for trusted API clients holding an already-issued session token.
- Club identity must match path club; member must own wallet, order, checkout, registration, refund, after-sales, and submitted payer phone. Leader must belong to the club and be assigned to the occurrence. Disabled club cannot access business APIs.
- Production fails closed on demo database, demo clubs/products, MOCK_AI, local commerce and enabled `local` payment accounts. Direct simulate-success, old manual refund/sign-up and unverified money webhooks are disabled. Provider notifications still require provider-side signature verification; Medusa `order.placed` only reconciles and cannot mark an unpaid checkout paid.
- Per-actor write audit ledger, request ID, login throttling, public application throttling, request-size limit and baseline security response headers. Do not write passwords, payloads, card data or raw tokens to audit logs.
- C-end/Club-end/Leader-end initialize identity from `/api/auth/me`. Activity attachments are isolated by `club_id` and random batch. Published media is served only through a path checked against that published activity's media allowlist. `/static/uploads` and `/static/demo` do not allow direct access in production.
- `scripts/backup_sqlite_v025.py` supports consistent online backups with integrity check. Keep the output outside the web root, encrypt it at rest/offsite, and periodically perform a restore rehearsal.

## Provision and start

Use a fresh, separately located production SQLite database. Supply secret environment values through the deployment platform, using `.env.production.example` only as a checklist. `CLUBOS_SECURITY_MODE=production` must be explicitly set; demo remains default for historical tests and standalone previews. Do **not** copy the packaged `clubos.db` into production.

Run `python scripts/auth_account_v025.py create --username operator --role platform` with an interactive password prompt (14+ characters); after a club is verified and approved, create an account bound to its exact `--club-id`. For a member/leader, externally verify the user's identity and bind `--user-id`; this CLI does **not** establish phone ownership or offer self-signup. Disabling an account revokes its sessions.

Run behind an HTTPS reverse proxy and restrict host/proxy trust; `CLUBOS_PUBLIC_BASE_URL` must match the public HTTPS origin. Set `MOCK_AI=0`, configure external AI credentials and `COMMERCE_PROVIDER=medusa`. Payment account must point to a verified WeChat/Alipay provider. No embedded demo secrets in live deployment. One sample command:

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

A successful app startup or offline test alone does **not** establish Medusa/PostgreSQL/Payment Provider reachability.

## Test gates

Local: `./scripts/regression_v025.sh` covers v0.8–v0.24 and production negative tests: missing authentication, 2-club and 2-user IDOR, leader roster, disabled account/club, CSRF, fake callbacks, static upload privacy, public media allowlist and audit. `.github/workflows/v025-security.yml` is defined but has **not** run on an actual GitHub repository from this session.

**Outstanding mandatory live launch gates**: v0.16 Medusa real PostgreSQL migration, seed, build/start, inventory/cart/payment/order/fulfillment/refund E2E; actual WeChat/Alipay certificates, signature/notification/refund integration and verification; real HTTPS ingress, backup restoration, service monitoring, concurrency/load, secrets rotation, incident response and external penetration test. Current data layer remains SQLite; database migration and multi-instance coordination need dedicated engineering before production scale. Audit is append-only in the local DB, not independent tamper-evident external storage; critical write/audit failure may return uncertain status and must be reconciled by X-Request-ID before retries.

ClubOS Domain rules, three ledgers, platform product ownership, `sourceClubId`, Activity/Medusa boundary, and independent 小野AI remain unchanged.
