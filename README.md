# AutoPay AI

> AI-powered bill automation for Nigerian users. Send a bill photo, PDF, or text to a Telegram bot (or upload via web dashboard) and the platform pays it on your behalf via Nomba — with a LangGraph decision agent deciding **pay-now** / **schedule** / **hold**.

## Architecture

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   Telegram Bot   │    │   Web / REST API │    │   Nomba Webhook  │
│  (in-process)    │    │     (FastAPI)    │    │ (payment_success │
└────────┬─────────┘    └─────────┬────────┘    │  payout_*)       │
         │                        │               └────────┬─────────┘
         │                        │                        │
         └────────────────┬───────┴───────────┬────────────┘
                          ▼                   ▼
                  ┌───────────────────────────────────┐
                  │            FastAPI app           │
                  │  /api/v1/auth /bills /kyc        │
                  │  /api/v1/wallet /telegram        │
                  │  /webhooks/nomba                 │
                  │  /telegram/webhook               │
                  └────────────────┬──────────────────┘
                                   │
       ┌───────────────┬───────────┼─────────────┐
       ▼               ▼           ▼             ▼
  ┌─────────┐   ┌─────────────┐ ┌──────────┐ ┌────────┐
  │Postgres │   │    Nomba    │ │ LangGraph│ │ APSched│
  │ 8 tables│   │ virtual acct│ │  agent   │ │  jobs  │
  │         │   │ / transfers │ │          │ │        │
  └─────────┘   └─────────────┘ └──────────┘ └────────┘
```

## Quickstart

```bash
# 1. Generate secrets
make keygen
# 2. Copy .env.example to .env and paste the secrets
cp .env.example .env
# 3. Bring up the app + Postgres
make up
# 4. Open the API docs
open http://localhost:8000/docs
```

## Environment variables

| Var | Default | Required? | Notes |
|---|---|---|---|
| `ENVIRONMENT` | `development` | yes | `development` / `staging` / `production` / `test` |
| `LOG_LEVEL` | `INFO` | no | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `SECRET_KEY` | `change-me` | prod | Used by itsdangerous (session cookies). |
| `DATABASE_URL` | `postgresql://postgres:...` | yes | SQLAlchemy URL. |
| `PAYMENT_PROVIDER` | `nomba` | no | Selects the `PaymentProvider` implementation. `nomba` is currently the only option. |
| `NOMBA_CLIENT_ID` | — | yes | OAuth client id, from the Nomba dashboard. |
| `NOMBA_CLIENT_SECRET` | — | yes | OAuth client secret. Used to obtain access tokens, not sent on every request. |
| `NOMBA_ACCOUNT_ID` | — | yes | UUID sent as the `accountId` header on every Nomba call. |
| `NOMBA_WEBHOOK_SECRET` | — | yes | Signing key entered when registering the webhook URL in the Nomba dashboard. Verifies inbound webhook authenticity — a separate value from `NOMBA_CLIENT_SECRET`. |
| `NOMBA_BASE_URL` | `https://api.nomba.com` | no | Override for sandbox/staging environments. |
| `BVN_ENCRYPTION_KEY` | — | yes | Fernet key. `make keygen` prints one. |
| `JWT_SECRET_KEY` | — | yes | ≥32 chars. `make keygen` prints one. |
| `JWT_ALGORITHM` | `HS256` | no | |
| `JWT_ACCESS_TTL_MIN` | `15` | no | Access-token lifetime. |
| `JWT_REFRESH_TTL_DAYS` | `7` | no | Refresh-token lifetime. |
| `PAYOUT_FEE_NGN` | `50.00` | no | Flat fee per payout, in NGN. |
| `TELEGRAM_BOT_TOKEN` | (empty) | optional | From `@BotFather`. Empty = bot disabled. |
| `TELEGRAM_BOT_USERNAME` | (empty) | optional | Bot username WITHOUT `@`. Used in deep-link URLs. |
| `WEBHOOK_URL` | (empty) | optional | Production: Telegram pushes here. Empty = polling. |
| `GROQ_API_KEY` | (empty) | optional | If unset, bill loader falls back to regex. |
| `LANGCHAIN_API_KEY` | (empty) | optional | Tracing only. |
| `AUTO_PROVISION_DVA_ON_SIGNUP` | `false` | no | If true, signup calls Nomba to create a virtual account inline instead of deferring to `POST /wallet/provision`. |

Generate a `.env`:

```bash
cp .env.example .env
# then fill in or:
make keygen   # prints FERNET_KEY + JWT_SECRET_KEY
```

## API reference

All routes are versioned under `/api/v1/` unless noted.

### Auth (no auth required)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/v1/auth/signup` | `SignupRequest` | `201 TokenResponse` |
| POST | `/api/v1/auth/login` | `LoginRequest` | `200 TokenResponse` |
| POST | `/api/v1/auth/refresh` | `RefreshRequest` | `200 TokenResponse` |
| POST | `/api/v1/auth/telegram/link-code` | — | `200 TelegramLinkCodeResponse` |
| DELETE | `/api/v1/auth/telegram/link-code` | — | `204` |
| DELETE | `/api/v1/auth/telegram/link` | — | `204` (unlink Telegram) |

### Auth (Bearer required)

| Method | Path | Returns |
|---|---|---|
| POST | `/api/v1/auth/logout` | `204` |
| GET | `/api/v1/auth/me` | `200 UserPublic` |
| GET | `/api/v1/auth/wallet` | `200 {balance, currency}` |

### Bills (Bearer required)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/v1/bills` | `BillCreateRequest` (JSON) | `201 BillActionResponse` |
| POST | `/api/v1/bills/upload` | `request_bill` (text) OR `file` (PDF/PNG/JPG) | `201 BillActionResponse` |
| GET | `/api/v1/bills?status=pending` | — | `200 BillResponse[]` |
| GET | `/api/v1/bills/{id}` | — | `200 BillResponse` |
| POST | `/api/v1/bills/{id}/pay` | — | `200 BillActionResponse` |
| POST | `/api/v1/bills/{id}/cancel` | — | `200 BillActionResponse` |

### KYC (Bearer required)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/v1/kyc/bvn` | `{bvn: 11-digit-string}` | `201 KycStatusResponse` |
| GET | `/api/v1/kyc/bvn` | — | `200 KycStatusResponse` |

### Wallet (Bearer required)

| Method | Path | Returns |
|---|---|---|
| POST | `/api/v1/wallet/provision` | `201 {virtual_account, already_existed}` |

### Webhooks (no auth; signature-verified)

| Method | Path | Purpose |
|---|---|---|
| POST | `/webhooks/nomba` | Nomba events (`payment_success`, `payout_success`/`payout_failed`/`payout_refund`, normalized internally to `charge.success`/`transfer.success`/`.failed`/`.reversed`). HMAC-SHA256 verified (`nomba-signature` header). Replay-safe. |
| POST | `/telegram/webhook` | Telegram bot updates. Only used in webhook mode. |

### Health (no auth)

| Method | Path | Returns |
|---|---|---|
| GET | `/healthz` | `200 {status: alive}` |
| GET | `/readyz` | `200 {status: ready, database: ok}` |
| GET | `/` | App banner |

## Telegram bot commands

| Command | What it does |
|---|---|
| `/start` | Welcome message with /link instructions |
| `/link ABC123` | Link your account using the 6-char code from the web dashboard |
| `/unlink` | Disconnect your Telegram from the web account |
| `/wallet` | Show balance + virtual account details |
| `/bills` | List recent bills |
| `/help` | Help message |
| `/cancel` | Cancel the current conversation |

Send a bill (photo, PDF, or text) at any time to start the upload flow.s

## Architecture decision: deferred DVA

Signup does **not** call Nomba to provision a virtual account by default. Provisioning is deferred so a failure (transient provider error, misconfigured credentials) never blocks account creation.

Two ways to provision a virtual account:

* `POST /api/v1/wallet/provision` (recommended) — call from the dashboard, the bot, or anywhere after signup.
* Set `AUTO_PROVISION_DVA_ON_SIGNUP=true` — signup does it inline, best-effort (failure is audit-logged, signup still succeeds).

## Architecture decision: webhook replay defense

A provider may redeliver the same webhook event on a network blip. We dedup on `(provider, event_id)` via the `webhook_events` table. The second delivery is a `200` no-op with a `webhook.replay` audit row, not a double-credit / double-debit.

`event_id` is Nomba's `requestId` when present; we fall back to a SHA-256 of the raw body when the provider omits it.

## Tests

```bash
make test                 # full suite (integration + unit)
make test-unit            # unit only
make test-integration     # integration only
make cov                  # coverage report
pytest -k "wallet"        # filter by name
```

The integration tests need a Postgres database. The Makefile target `make test` spins up `docker-compose.test.yml` for you. For local runs, set:

```bash
export DATABASE_URL=postgresql://postgres:David*2020*@localhost:5432/autopay_test
export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
export FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
pytest
```

Last known-good count before the Nomba migration: **178 tests passing** (12 auth, 6 wallet, 8 bill, 5 kyc, 9 webhook, 3 db-rollback, 12 telegram-handler, 5 scheduler, 10 agent, 8 audit, 5 crypto, 23 models, 7 payment-math, 17 paystack, 9 security, 4 smoke, 30 date-parser, 4 status/loader, + 1 OpenAPI security scheme test, + 1 WWW-Authenticate header test).

> **Stale after the Nomba migration.** `tests/unit/test_paystack.py` and `tests/integration/test_webhook_endpoints.py` still import `PaystackProvider`/`settings.paystack_secret_key`, which no longer exist — both fail at collection until they're rewritten for `NombaProvider`. Treat the 178 figure as pre-migration, not current.

## Project layout

```
app/
  api/             HTTP routers (auth, bills, kyc, wallet, webhooks, health)
  core/            config, database, logging, scheduler, security, http
  handlers/        Telegram bot conversation handlers
  models/          SQLModel ORM (8 tables)
  schemas/         Pydantic DTOs
  services/        business logic (auth, audit, loaders, payout, date_parser,
                   payments/{base,exceptions,paystack} — paystack.py now holds
                   the NombaProvider implementation, kept under its old
                   filename, telegram)
  agents/          LangGraph decision agent
  static/          static assets
  templates/       Jinja templates
migrations/        Alembic
scripts/           entrypoint.sh, seed.py
tests/             pytest suite
.github/workflows/ CI
```

## Webhook testing with ngrok (dev only)

For real Nomba webhook testing, you need a public HTTPS URL pointing at your local `/webhooks/nomba`. `ngrok` is the cheapest way:

```bash
# 1. Install: https://ngrok.com/download  (or `winget install ngrok`)
# 2. Sign up, grab your authtoken from the dashboard, run once:
ngrok config add-authtoken <your-token>

# 3. Start the tunnel
make ngrok
# Copy the https URL it prints (e.g. https://abc123.ngrok-free.app)

# 4. Update .env:
WEBHOOK_URL=https://abc123.ngrok-free.app/telegram/webhook
# (you'll also need to register the webhook URL + signing key in the
# Nomba dashboard, pointing at https://abc123.ngrok-free.app/webhooks/nomba)
```

Free ngrok gives you a random URL that changes every restart. `ngrok Pro` ($8/mo) lets you pin a static subdomain. Either is fine for development.

## Deploy to Railway

1. Push to GitHub.
2. Create a new Railway project → "Deploy from GitHub repo".
3. Add a Postgres database (Railway's "Provision PostgreSQL" plugin).
4. Set environment variables in the Railway dashboard (use `make keygen` to generate):
   - `DATABASE_URL` (Railway provides this as `${{Postgres.DATABASE_URL}}`)
   - `JWT_SECRET_KEY`
   - `BVN_ENCRYPTION_KEY`
   - `NOMBA_CLIENT_ID`, `NOMBA_CLIENT_SECRET`, `NOMBA_ACCOUNT_ID` (live credentials, not sandbox)
   - `NOMBA_WEBHOOK_SECRET`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_BOT_USERNAME`
   - `WEBHOOK_URL` (e.g. `https://<app>.up.railway.app/telegram/webhook`)
   - `AUTO_PROVISION_DVA_ON_SIGNUP=true` (optional — inline virtual-account creation at signup)
5. Deploy. Railway will run `uvicorn app.main:app --host 0.0.0.0 --port $PORT` automatically (or override the start command).
6. After first deploy, apply the schema: `railway run psql $DATABASE_URL < schema.sql`
7. Register the webhook URL + signing key in the Nomba dashboard, pointing at `https://<app>.up.railway.app/webhooks/nomba`.

## Production checklist

Before flipping the deploy from staging to prod:

- [ ] Replace all hardcoded secrets in `app/core/config.py` with empty defaults + startup assertion in `production` env.
- [ ] Set `ENVIRONMENT=production` (turns on the secret-required startup check).
- [ ] Set `JWT_SECRET_KEY` to a fresh 64-char value (not the dev one).
- [ ] Set `BVN_ENCRYPTION_KEY` to a fresh Fernet key.
- [ ] Set `NOMBA_CLIENT_ID`/`NOMBA_CLIENT_SECRET`/`NOMBA_ACCOUNT_ID` to live (not sandbox) credentials.
- [ ] Set `NOMBA_WEBHOOK_SECRET` to a fresh random value, matching whatever is entered in the Nomba dashboard.
- [ ] Register the production webhook URL + signing key in the Nomba dashboard.
- [ ] Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` from @BotFather.
- [ ] Set `WEBHOOK_URL` to your production Telegram webhook URL.
- [ ] `git log -- pyproject.toml` and confirm no secrets were ever committed.
- [ ] Run `pytest --cov=app` — coverage gate at 70% per `pyproject.toml`.

## License

MIT
# autopay
# autopay
