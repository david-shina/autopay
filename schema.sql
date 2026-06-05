-- =====================================================================
-- AutoPay AI — PostgreSQL schema
--
-- This file is the human-readable specification. Runtime migrations are
-- managed by Alembic (migrations/versions/). The Postgres container
-- mounts this file on first boot via /docker-entrypoint-initdb.d.
--
-- Tables: users, kyc_records, virtual_accounts, bills, transactions,
--         audit_logs, refresh_tokens, telegram_link_codes
-- =====================================================================

-- Future-proofing: enum types are kept inline as CHECK constraints for
-- now, but can be promoted to proper ENUMs in a later migration if
-- portability becomes important.

-- ── Reusable extension(s) ───────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =====================================================================
-- users
-- =====================================================================
-- NOTE: bvn is NOT stored here. It lives encrypted in kyc_records.
CREATE TABLE IF NOT EXISTS users (
    id                   BIGSERIAL    PRIMARY KEY,
    first_name           TEXT         NOT NULL,
    last_name            TEXT         NOT NULL,
    email                TEXT         NOT NULL UNIQUE,
    phone_number         TEXT         NOT NULL UNIQUE,
    hashed_password      TEXT         NOT NULL,
    telegram_chat_id     TEXT         UNIQUE,
    is_telegram_linked   BOOLEAN      NOT NULL DEFAULT FALSE,
    balance              NUMERIC(14,2) NOT NULL DEFAULT 0,
    currency             CHAR(3)      NOT NULL DEFAULT 'NGN',
    address              TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email            ON users(LOWER(email));
CREATE INDEX IF NOT EXISTS idx_users_telegram_chat_id ON users(telegram_chat_id);

-- =====================================================================
-- kyc_records  (BVN is encrypted at rest; last4 + hash for lookups)
-- =====================================================================
CREATE TABLE IF NOT EXISTS kyc_records (
    id                BIGSERIAL    PRIMARY KEY,
    user_id           BIGINT       NOT NULL UNIQUE
                                   REFERENCES users(id) ON DELETE CASCADE,
    bvn_ciphertext    BYTEA        NOT NULL,
    bvn_last4         CHAR(4)      NOT NULL,
    bvn_hash          CHAR(64)     NOT NULL UNIQUE,
    bvn_validated     BOOLEAN      NOT NULL DEFAULT FALSE,
    validated_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kyc_records_user_id  ON kyc_records(user_id);
CREATE INDEX IF NOT EXISTS idx_kyc_records_bvn_hash ON kyc_records(bvn_hash);

-- =====================================================================
-- virtual_accounts
-- =====================================================================
CREATE TABLE IF NOT EXISTS virtual_accounts (
    id                          BIGSERIAL    PRIMARY KEY,
    user_id                     BIGINT       NOT NULL UNIQUE
                                            REFERENCES users(id) ON DELETE CASCADE,
    provider                    TEXT         NOT NULL DEFAULT 'paystack',
    provider_account_reference  TEXT         NOT NULL UNIQUE,
    account_number              TEXT         UNIQUE,
    account_name                TEXT,
    bank_name                   TEXT,
    currency                    CHAR(3)      NOT NULL DEFAULT 'NGN',
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_virtual_accounts_account_number
    ON virtual_accounts(account_number);

-- =====================================================================
-- bills
-- =====================================================================
CREATE TABLE IF NOT EXISTS bills (
    id                    BIGSERIAL       PRIMARY KEY,
    user_id               BIGINT          NOT NULL
                                         REFERENCES users(id) ON DELETE CASCADE,
    vendor_name           TEXT            NOT NULL,
    account_number        TEXT,
    bank_code             TEXT,
    bank_name             TEXT,
    amount                NUMERIC(14,2)   NOT NULL,
    currency              CHAR(3)         NOT NULL DEFAULT 'NGN',
    due_date              TIMESTAMPTZ     NOT NULL,
    status                TEXT            NOT NULL DEFAULT 'pending'
                                         CHECK (status IN
                                            ('pending','scheduled','processing',
                                             'paid','failed','cancelled')),
    is_recurring          BOOLEAN         NOT NULL DEFAULT FALSE,
    recurrence_interval   TEXT,
    next_recurrence_date  TIMESTAMPTZ,
    retry_count           INTEGER         NOT NULL DEFAULT 0,
    max_retries           INTEGER         NOT NULL DEFAULT 3,
    created_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bills_user_id        ON bills(user_id);
CREATE INDEX IF NOT EXISTS idx_bills_status_due
    ON bills(due_date) WHERE status = 'scheduled';

-- =====================================================================
-- transactions
-- =====================================================================
CREATE TABLE IF NOT EXISTS transactions (
    id                  BIGSERIAL       PRIMARY KEY,
    user_id             BIGINT          NOT NULL
                                       REFERENCES users(id) ON DELETE RESTRICT,
    bill_id             BIGINT          REFERENCES bills(id) ON DELETE SET NULL,
    type                TEXT            NOT NULL
                                       CHECK (type IN ('credit','debit')),
    amount              NUMERIC(14,2)   NOT NULL,
    fee                 NUMERIC(14,2)   NOT NULL DEFAULT 0,
    currency            CHAR(3)         NOT NULL DEFAULT 'NGN',
    status              TEXT            NOT NULL DEFAULT 'pending'
                                       CHECK (status IN
                                          ('pending','processing','success',
                                           'failed','reversed')),
    provider            TEXT            NOT NULL DEFAULT 'paystack',
    provider_reference  TEXT            UNIQUE,
    retry_count         INTEGER         NOT NULL DEFAULT 0,
    failure_reason      TEXT,
    narration           TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_id
    ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_status
    ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_transactions_user_status
    ON transactions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_transactions_created_at
    ON transactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_provider_reference
    ON transactions(provider_reference);

-- =====================================================================
-- audit_logs  — every state-changing event appends a row
-- =====================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id            BIGSERIAL    PRIMARY KEY,
    user_id       BIGINT       REFERENCES users(id) ON DELETE SET NULL,
    actor         TEXT         NOT NULL,
    event_type    TEXT         NOT NULL,
    entity_type   TEXT,
    entity_id     BIGINT,
    before_state  JSONB,
    after_state   JSONB,
    metadata      JSONB,
    ip_address    INET,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id    ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);

-- =====================================================================
-- refresh_tokens
-- =====================================================================
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id           BIGSERIAL    PRIMARY KEY,
    user_id      BIGINT       NOT NULL
                             REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT         NOT NULL UNIQUE,
    expires_at   TIMESTAMPTZ  NOT NULL,
    revoked      BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);

-- =====================================================================
-- telegram_link_codes
-- =====================================================================
CREATE TABLE IF NOT EXISTS telegram_link_codes (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     BIGINT       NOT NULL
                            REFERENCES users(id) ON DELETE CASCADE,
    code        TEXT         NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ  NOT NULL,
    is_used     BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_telegram_link_codes_user_id ON telegram_link_codes(user_id);

-- =====================================================================
-- webhook_events
-- =====================================================================
-- Paystack retries events on network blip. We dedup on (provider,
-- event_id) so the second delivery is a 200 no-op. event_id is the
-- Paystack `event.id` when present; we fall back to a SHA-256 of the
-- raw body if the event omits the field (older Paystack payloads).
CREATE TABLE IF NOT EXISTS webhook_events (
    id           BIGSERIAL    PRIMARY KEY,
    provider     TEXT         NOT NULL,
    event_id     TEXT         NOT NULL,
    event_type   TEXT         NOT NULL,
    received_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_webhook_events_provider_event_id
        UNIQUE (provider, event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_received_at
    ON webhook_events(received_at);

-- =====================================================================
-- ── Showcase queries (documentation only) ──────────────────────────
-- =====================================================================
--
-- (1) Running balance per user, using a CTE + window function:
--
--   WITH user_tx AS (
--     SELECT
--       t.id, t.user_id, u.email, t.type, t.amount, t.fee,
--       t.status, t.created_at,
--       CASE
--         WHEN t.type = 'credit' AND t.status = 'success' THEN  t.amount
--         WHEN t.type = 'debit'  AND t.status = 'success' THEN -(t.amount + t.fee)
--         ELSE 0
--       END AS signed_amount
--     FROM transactions t
--     JOIN users u ON u.id = t.user_id
--   )
--   SELECT id, user_id, email, type, amount, fee, status, created_at,
--          SUM(signed_amount) OVER (
--            PARTITION BY user_id
--            ORDER BY created_at, id
--            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
--          ) AS running_balance
--   FROM user_tx
--   ORDER BY user_id, created_at;
--
-- (2) Monthly spend per user, with a ranking window function:
--
--   WITH monthly AS (
--     SELECT user_id,
--            date_trunc('month', created_at) AS month,
--            SUM(amount + fee) AS total_spend
--     FROM transactions
--     WHERE type = 'debit' AND status = 'success'
--     GROUP BY user_id, date_trunc('month', created_at)
--   )
--   SELECT user_id, month, total_spend,
--          RANK() OVER (PARTITION BY user_id ORDER BY total_spend DESC) AS spend_rank
--   FROM monthly;
