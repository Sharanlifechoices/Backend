# My LifeChoices API

FastAPI backend for the My LifeChoices store (frontend repo: `Sharanlifechoices/Frontend`).
Supabase (Postgres + auth), Resend for email, Razorpay for payments. Deployed on Render.

## Run locally

```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env          # then fill it in (see below)
.venv\Scripts\python -m uvicorn app.main:app --port 8000
.venv\Scripts\python -m pytest -q          # needs SUPABASE_URL=https://x.supabase.co SUPABASE_ANON_KEY=a.b.c in the environment
```

Logs: JSON lines on stdout and `logs/api.log`. Every request, response and outgoing call (Supabase, Resend, Razorpay)
is logged with a `request_id` (also returned as `X-Request-ID`). Passwords, tokens, signatures, OTP codes and secrets are
redacted. With `EMAIL_MODE=mock` emails (including OTP codes) are logged instead of sent.

## Supabase setup
Run `supabase/migrations/0001` … `0007` in the SQL editor, in order. In Authentication → Providers → Email, switch
"Confirm email" **off** (the API sends its own OTP and confirms users itself).

## Environment
See `.env.example`. Required: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (server only),
`OTP_SECRET`, `ADMIN_SECRET`, `JOBS_SECRET` (generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`).
Payments: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`. Email: `EMAIL_MODE=resend`, `RESEND_API_KEY`,
`RESEND_FROM_EMAIL`. `FRONTEND_URL` / `EXTRA_CORS_ORIGIN` control CORS; `PUBLIC_API_URL` is used in email links.

## API overview
| Area | Endpoints |
|---|---|
| Auth | `POST /auth/signup, /resend-otp, /verify-otp, /login, /refresh, /logout, /forgot-password, /reset-password`, `GET/PATCH /me` |
| Catalog and cart | `GET /catalog`, `POST /cart/recommendations`, `GET/POST/PATCH/DELETE /cart…`, `POST /cart/merge` |
| Orders | `POST /checkout`, `/checkout/session`, `/checkout/verify`, `/webhooks/razorpay`, `GET /orders`, `GET /availability?date=` |
| Public | `GET /gallery`, `GET/POST /unsubscribe?token=`, `PUT /me/subscription` |
| Admin (`X-Admin-Secret`) | `/admin/orders`, `/users` (+ password, delete), `/bookings`, `/availability`, `/gallery`, `/newsletters`, `/newsletter/audience` |
| Jobs (`X-Jobs-Secret`) | `POST /jobs/daily` (birthdays, 7-day follow-up, stale-signup cleanup, resumes interrupted newsletters) |

Payments: the server computes the price and creates the Razorpay order; `/checkout/verify` checks the signature and the
webhook is a safety net. Both go through one idempotent routine, so replays never double-book or double-email.

## Deploying (Render)
`render.yaml` defines a Docker web service. Set the secrets in the Render dashboard. Then:
1. Razorpay dashboard → webhook for `payment.captured` → `https://<render-url>/webhooks/razorpay` (secret → `RAZORPAY_WEBHOOK_SECRET`).
2. cron-job.org → daily `POST https://<render-url>/jobs/daily` with header `X-Jobs-Secret: <JOBS_SECRET>`.
3. Resend: verify the sending domain (DNS records at the Zoho-managed domain), then set `EMAIL_MODE=resend`.
4. Vercel: `VITE_API_URL=https://<render-url>`.

## Importing the old Firestore data (one-off)
`python -m app.tools.firestore_migration` is a dry run (read-only on Firestore, saves a JSON backup under `backups/`);
add `--apply` to write to Supabase. Safe to re-run. See the module docstring.

## Known limits
- Rate limiting is in memory (single instance).
- Changing a user's password does not end their existing sessions (they expire within about an hour).
