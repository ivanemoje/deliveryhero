-- scripts/init.sql
-- Runs automatically on first postgres container start.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

DO $$ BEGIN
    CREATE TYPE contract_status_t AS ENUM ('ACTIVE', 'EXPIRED', 'TERMINATED', 'DRAFT', 'SUPERSEDED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE party_role_t AS ENUM ('CLIENT', 'SERVICE_PROVIDER');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS parties (
    party_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    legal_name      TEXT        NOT NULL,
    street          TEXT,
    city            TEXT,
    region          TEXT,
    country         TEXT        NOT NULL DEFAULT 'UNKNOWN',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_parties_identity UNIQUE (legal_name, country)
);

CREATE TABLE IF NOT EXISTS source_documents (
    source_document_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_file_key    TEXT        NOT NULL UNIQUE,
    source_name        TEXT        NOT NULL,
    file_format        TEXT        NOT NULL,
    content_sha256     TEXT,
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contract_masters (
    master_contract_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_party_id    UUID        NOT NULL REFERENCES parties(party_id),
    provider_party_id  UUID        NOT NULL REFERENCES parties(party_id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_contract_master_parties UNIQUE (client_party_id, provider_party_id)
);

CREATE TABLE IF NOT EXISTS contract_versions (
    contract_version_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    master_contract_id      UUID        NOT NULL REFERENCES contract_masters(master_contract_id),
    source_document_id      UUID        REFERENCES source_documents(source_document_id),
    predecessor_version_id  UUID        REFERENCES contract_versions(contract_version_id),
    version_number          INT         NOT NULL DEFAULT 1 CHECK (version_number > 0),
    status                  contract_status_t NOT NULL DEFAULT 'ACTIVE',
    effective_date          DATE,
    expiration_date         DATE,
    total_contract_value    NUMERIC(18, 2),
    currency                CHAR(3)     CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    governing_law           TEXT,
    venue                   TEXT,
    jurisdiction_tags       TEXT[]      NOT NULL DEFAULT '{}',
    clauses_snapshot        JSONB       NOT NULL DEFAULT '[]',
    extracted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_at           TIMESTAMPTZ,
    CONSTRAINT uq_contract_versions_master_version UNIQUE (master_contract_id, version_number)
);

-- Compatibility view for callers that still expect the original contracts table.
CREATE OR REPLACE VIEW contracts AS
SELECT
    cv.contract_version_id AS contract_id,
    cv.source_document_id,
    cv.predecessor_version_id AS parent_contract_id,
    cm.client_party_id,
    cm.provider_party_id,
    cv.status::TEXT AS status,
    cv.effective_date,
    cv.expiration_date,
    cv.total_contract_value,
    cv.currency,
    cv.governing_law,
    cv.venue,
    cv.jurisdiction_tags,
    cv.clauses_snapshot,
    sd.source_file_key,
    cv.version_number,
    cv.extracted_at
FROM contract_versions cv
JOIN contract_masters cm ON cm.master_contract_id = cv.master_contract_id
LEFT JOIN source_documents sd ON sd.source_document_id = cv.source_document_id;

CREATE OR REPLACE FUNCTION delete_contracts_view_row()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM contract_versions
    WHERE contract_version_id = OLD.contract_id;

    DELETE FROM source_documents sd
    WHERE sd.source_document_id = OLD.source_document_id
      AND NOT EXISTS (
          SELECT 1
          FROM contract_versions cv
          WHERE cv.source_document_id = sd.source_document_id
      );

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_delete_contracts_view_row ON contracts;
CREATE TRIGGER trg_delete_contracts_view_row
INSTEAD OF DELETE ON contracts
FOR EACH ROW
EXECUTE FUNCTION delete_contracts_view_row();

CREATE TABLE IF NOT EXISTS contract_party_roles (
    contract_version_id UUID         NOT NULL REFERENCES contract_versions(contract_version_id) ON DELETE CASCADE,
    party_id            UUID         NOT NULL REFERENCES parties(party_id),
    role                party_role_t NOT NULL,
    PRIMARY KEY (contract_version_id, party_id, role)
);

CREATE TABLE IF NOT EXISTS renewal_terms (
    renewal_term_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_version_id     UUID        NOT NULL REFERENCES contract_versions(contract_version_id) ON DELETE CASCADE,
    auto_renews             BOOLEAN     NOT NULL DEFAULT FALSE,
    renewal_period_months   INT         CHECK (renewal_period_months IS NULL OR renewal_period_months > 0),
    non_renewal_notice_days INT         CHECK (non_renewal_notice_days IS NULL OR non_renewal_notice_days >= 0),
    notice_deadline_date    DATE,
    renewal_sequence        INT         NOT NULL DEFAULT 1 CHECK (renewal_sequence > 0),
    metadata                JSONB       NOT NULL DEFAULT '{}',
    CONSTRAINT uq_renewal_sequence UNIQUE (contract_version_id, renewal_sequence)
);

CREATE TABLE IF NOT EXISTS payment_schedule (
    payment_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_version_id UUID        NOT NULL REFERENCES contract_versions(contract_version_id) ON DELETE CASCADE,
    amount              NUMERIC(18, 2) NOT NULL,
    currency            CHAR(3)     NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    due_date            DATE,
    cadence             TEXT,
    description         TEXT
);

CREATE TABLE IF NOT EXISTS contract_clauses (
    clause_id                       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_version_id             UUID        NOT NULL REFERENCES contract_versions(contract_version_id) ON DELETE CASCADE,
    clause_type                     TEXT        NOT NULL,
    clause_text                     TEXT,
    notice_period_days              INT         CHECK (notice_period_days IS NULL OR notice_period_days >= 0),
    notice_period_months            INT         CHECK (notice_period_months IS NULL OR notice_period_months >= 0),
    performance_delay_threshold_days INT        CHECK (performance_delay_threshold_days IS NULL OR performance_delay_threshold_days >= 0),
    immediate_termination_allowed   BOOLEAN,
    confidence_score                NUMERIC(5, 4) CHECK (confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1),
    extraction_method               TEXT        NOT NULL DEFAULT 'regex',
    metadata                        JSONB       NOT NULL DEFAULT '{}',
    extracted_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS extraction_audit (
    audit_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_version_id UUID        REFERENCES contract_versions(contract_version_id) ON DELETE CASCADE,
    source_document_id  UUID        REFERENCES source_documents(source_document_id),
    field_name          TEXT        NOT NULL,
    extracted_value     TEXT,
    confidence_score    NUMERIC(5, 4) CHECK (confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1),
    extraction_method   TEXT        NOT NULL DEFAULT 'regex',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS regulatory_datasets (
    regulatory_dataset_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_version_id   UUID        NOT NULL REFERENCES contract_versions(contract_version_id) ON DELETE CASCADE,
    dataset_id            TEXT        NOT NULL,
    title                 TEXT        NOT NULL,
    description           TEXT,
    query                 TEXT        NOT NULL,
    published_after       DATE        NOT NULL,
    retrieved_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata              JSONB       NOT NULL DEFAULT '{}',
    CONSTRAINT uq_regulatory_contract_dataset UNIQUE (contract_version_id, dataset_id)
);

CREATE INDEX IF NOT EXISTS idx_parties_country ON parties(country);
CREATE INDEX IF NOT EXISTS idx_versions_status ON contract_versions(status);
CREATE INDEX IF NOT EXISTS idx_versions_expiration_active
    ON contract_versions(expiration_date)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_versions_master ON contract_versions(master_contract_id);
CREATE INDEX IF NOT EXISTS idx_versions_jurisdictions_gin ON contract_versions USING GIN (jurisdiction_tags);
CREATE INDEX IF NOT EXISTS idx_versions_clauses_gin ON contract_versions USING GIN (clauses_snapshot);
CREATE INDEX IF NOT EXISTS idx_clauses_contract_type ON contract_clauses(contract_version_id, clause_type);
CREATE INDEX IF NOT EXISTS idx_clause_metadata_gin ON contract_clauses USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_renewal_notice_deadline ON renewal_terms(notice_deadline_date);
CREATE INDEX IF NOT EXISTS idx_payment_contract ON payment_schedule(contract_version_id);
CREATE INDEX IF NOT EXISTS idx_regulatory_contract ON regulatory_datasets(contract_version_id);
CREATE INDEX IF NOT EXISTS idx_regulatory_dataset_id ON regulatory_datasets(dataset_id);
