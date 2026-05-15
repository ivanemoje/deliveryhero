-- scripts/init.sql
-- Runs automatically on first postgres container start.

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Composite types ──────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE address_t AS (
        street   TEXT,
        city     TEXT,
        country  TEXT,
        region   TEXT
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE money_amount_t AS (
        amount      NUMERIC(18, 2),
        currency    CHAR(3)
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── parties ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parties (
    party_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    legal_name      TEXT        NOT NULL,
    address         address_t   NOT NULL DEFAULT ROW(NULL, NULL, 'UNKNOWN', NULL),
    roles           TEXT[]      NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Essential for ON CONFLICT (legal_name, ((address).country)) 
-- The double parentheses are mandatory for expressions in index definitions.
CREATE UNIQUE INDEX IF NOT EXISTS idx_party_upsert 
ON parties (legal_name, ((address).country));

-- ── contracts ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contracts (
    contract_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_contract_id      UUID        REFERENCES contracts(contract_id),
    client_party_id         UUID        NOT NULL REFERENCES parties(party_id),
    provider_party_id       UUID        NOT NULL REFERENCES parties(party_id),
    status                  TEXT        NOT NULL DEFAULT 'ACTIVE'
                                        CHECK (status IN ('ACTIVE','EXPIRED','TERMINATED','DRAFT')),
    effective_date          DATE,
    expiration_date         DATE,
    total_contract_value    NUMERIC(18, 2),
    currency                CHAR(3),
    payment_schedule        money_amount_t[],
    governing_law           TEXT,
    venue                   TEXT,
    jurisdiction_tags       TEXT[]      NOT NULL DEFAULT '{}',
    clauses_snapshot        JSONB,
    source_file_key         TEXT,
    version_number          INT         NOT NULL DEFAULT 1,
    extracted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── contract_clauses ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contract_clauses (
    clause_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id             UUID        NOT NULL REFERENCES contracts(contract_id) ON DELETE CASCADE,
    clause_type             TEXT        NOT NULL,
    notice_period_days      INT,
    notice_period_months    INT,
    metadata                JSONB       NOT NULL DEFAULT '{}',
    extracted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts(status);
CREATE INDEX IF NOT EXISTS idx_contracts_expiration ON contracts(expiration_date) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_contracts_source_file ON contracts(source_file_key);
CREATE INDEX IF NOT EXISTS idx_clauses_contract_type ON contract_clauses(contract_id, clause_type);
CREATE INDEX IF NOT EXISTS idx_contracts_clauses_gin ON contracts USING GIN (clauses_snapshot);
CREATE INDEX IF NOT EXISTS idx_contracts_jurisdictions_gin ON contracts USING GIN (jurisdiction_tags);
CREATE INDEX IF NOT EXISTS idx_clause_metadata_gin ON contract_clauses USING GIN (metadata);