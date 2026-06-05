# AutoPay AI

> AI-powered bill automation for Nigerian users. Send a bill photo, PDF, or text to a Telegram bot (or upload via web dashboard) and the platform pays it on your behalf via Paystack — with a LangGraph decision agent deciding **pay-now** / **schedule** / **hold**.

## Architecture

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   Telegram Bot   │    │   Web / REST API │    │  Paystack Webhook│
│  (in-process)    │    │     (FastAPI)    │    │  (charge.success │
└────────┬─────────┘    └─────────┬────────┘    │  transfer.*)     │
         │                        │               └────────┬─────────┘
         │                        │                        │
         └────────────────┬───────┴───────────┬────────────┘
                          ▼                   ▼
                  ┌───────────────────────────────────┐
                  │            FastAPI app           │
                  │  /api/v1/auth /bills /kyc        │
                  │  /api/v1/wallet /telegram        │
                  │  /webhooks/paystack              │
                  │  /telegram/webhook               │
                  └────────────────┬──────────────────┘
                                   │
       ┌───────────────┬───────────┼─────────────┐
       ▼               ▼           ▼             ▼
  ┌─────────┐   ┌─────────────┐ ┌──────────┐ ┌────────┐
  │Postgres │   │  Paystack   │ │ LangGraph│ │ APSched│
  │ 8 tables│   │ DVA / trans │ │  agent   │ │  jobs  │
  │         │   │  / webhooks │ │          │ │        │
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
| `PAYSTACK_SECRET_KEY` | — | yes | Live or test (`sk_test_...`) key. |
| `PAYSTACK_PUBLIC_KEY` | — | yes | Public-facing key. |
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
| `AUTO_PROVISION_DVA_ON_SIGNUP` | `false` | no | If true, signup calls Paystack to create a DVA inline. Keep false until your Paystack business is approved for Dedicated NUBANs. |

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
| POST | `/webhooks/paystack` | Paystack events (`charge.success`, `transfer.success/failed/reversed`, `dedicatedaccount.assign.success`). HMAC-SHA512 verified. Replay-safe. |
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

Send a bill (photo, PDF, or text) at any time to start the upload flow.

## Architecture decision: deferred DVA

Signup does **not** call Paystack to provision a Dedicated NUBAN by default. The Paystack Dedicated NUBAN feature requires business approval; turning it on prematurely causes every signup to fail-audit the DVA step.

Two ways to provision a DVA:

* `POST /api/v1/wallet/provision` (recommended) — call from the dashboard, the bot, or anywhere after signup.
* Set `AUTO_PROVISION_DVA_ON_SIGNUP=true` — signup does it inline. Switch on once your Paystack business is approved.

## Architecture decision: webhook replay defense

Paystack retries the same event on a network blip. We dedup on `(provider, event_id)` via the `webhook_events` table. The second delivery is a `200` no-op with a `webhook.replay` audit row, not a double-credit / double-debit.

`event_id` is the Paystack `event.id` when present; we fall back to a SHA-256 of the raw body when the provider omits the field (older Paystack payloads).

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

Current count: **178 tests passing** (12 auth, 6 wallet, 8 bill, 5 kyc, 9 webhook, 3 db-rollback, 12 telegram-handler, 5 scheduler, 10 agent, 8 audit, 5 crypto, 23 models, 7 payment-math, 17 paystack, 9 security, 4 smoke, 30 date-parser, 4 status/loader, + 1 OpenAPI security scheme test, + 1 WWW-Authenticate header test).

## Project layout

```
app/
  api/             HTTP routers (auth, bills, kyc, wallet, webhooks, health)
  core/            config, database, logging, scheduler, security, http
  handlers/        Telegram bot conversation handlers
  models/          SQLModel ORM (8 tables)
  schemas/         Pydantic DTOs
  services/        business logic (auth, audit, loaders, payout, date_parser,
                   payments/{base,exceptions,paystack}, telegram)
  agents/          LangGraph decision agent
  static/          static assets
  templates/       Jinja templates
migrations/        Alembic
scripts/           entrypoint.sh, seed.py
tests/             pytest suite
.github/workflows/ CI
```

## Webhook testing with ngrok (dev only)

For real Paystack webhook testing, you need a public HTTPS URL pointing at your local `/webhooks/paystack`. `ngrok` is the cheapest way:

```bash
# 1. Install: https://ngrok.com/download  (or `winget install ngrok`)
# 2. Sign up, grab your authtoken from the dashboard, run once:
ngrok config add-authtoken <your-token>

# 3. Start the tunnel
make ngrok
# Copy the https URL it prints (e.g. https://abc123.ngrok-free.app)

# 4. Update .env:
WEBHOOK_URL=https://abc123.ngrok-free.app/telegram/webhook
# (you'll also need to set the Paystack webhook URL in their dashboard
# to https://abc123.ngrok-free.app/webhooks/paystack)
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
   - `PAYSTACK_SECRET_KEY` (live key, not test)
   - `PAYSTACK_PUBLIC_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_BOT_USERNAME`
   - `WEBHOOK_URL` (e.g. `https://<app>.up.railway.app/telegram/webhook`)
   - `AUTO_PROVISION_DVA_ON_SIGNUP=true` (only if your Paystack business is approved for Dedicated NUBANs)
5. Deploy. Railway will run `uvicorn app.main:app --host 0.0.0.0 --port $PORT` automatically (or override the start command).
6. After first deploy, apply the schema: `railway run psql $DATABASE_URL < schema.sql`
7. Update the Paystack dashboard to point at `https://<app>.up.railway.app/webhooks/paystack`.

## Production checklist

Before flipping the deploy from staging to prod:

- [ ] Replace all hardcoded secrets in `app/core/config.py` with empty defaults + startup assertion in `production` env.
- [ ] Set `ENVIRONMENT=production` (turns on the secret-required startup check).
- [ ] Set `JWT_SECRET_KEY` to a fresh 64-char value (not the dev one).
- [ ] Set `BVN_ENCRYPTION_KEY` to a fresh Fernet key.
- [ ] Set `PAYSTACK_SECRET_KEY` to a live key (`sk_live_...`).
- [ ] Set `AUTO_PROVISION_DVA_ON_SIGNUP=true` only after Paystack business is approved for Dedicated NUBANs.
- [ ] Configure the Paystack dashboard webhook URL.
- [ ] Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` from @BotFather.
- [ ] Set `WEBHOOK_URL` to your production Telegram webhook URL.
- [ ] `git log -- pyproject.toml` and confirm no secrets were ever committed.
- [ ] Run `pytest --cov=app` — coverage gate at 70% per `pyproject.toml`.

## License

MIT
