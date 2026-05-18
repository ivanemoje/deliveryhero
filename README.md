# Legal Contract Pipeline

Document intelligence pipeline for extracting, storing, and querying
legal contract metadata.

**Stack:** Python 3.12 · LangGraph · MinIO · PostgreSQL · Docker Compose

---

## Architecture

```
                     ┌──────────────────────────────────────────────────┐
                     │               Pipeline                           │
                     │                                                  │
 .docx/.pdf/gdoc ───▶│  check_duplicate ──(skip)──▶ notify ──▶ END      │
                     │       │(new)                                     │
                     │       ▼                                          │
                     │    ingest ──▶ extract ──▶ validate               │
                     │                              │                   │
                     │               ┌──────────────┤                   │
                     │               ▼(retry)       ▼(ok)               │
                     │            extract        persist ──▶ notify     │
                     │               ▲(n<MAX)       │                   │
                     │               └──────────────┘                   │
                     └──────────────────────────────────────────────────┘
                                │                │
                             MinIO           PostgreSQL
                       (raw documents)  (structured metadata
                                         + clause obligations)
```

| Node | Responsibility |
|---|---|
| `check_duplicate` | Derives the MinIO key; skips if already in PostgreSQL (idempotent) |
| `ingest` | Upload raw file to MinIO under `contracts/<filename>` |
| `extract` | Parse metadata from file (parties, financials, dates, clauses) |
| `validate` | Assert required fields present; retry up to `AGENT_MAX_RETRIES` |
| `persist` | Upsert parties, insert contract + clauses into PostgreSQL |
| `notify` | Log result; alert Slack if Force Majeure notice period exceeds 14 days |

---

## Project Structure

```
deliveryhero/
├── src/
│   ├── agent/
│   │   ├── graph.py        # LangGraph state graph definition
│   │   └── pipeline.py     # CLI entry point
│   ├── extractor/
│   │   └── extract.py      # Format-agnostic metadata parser (.docx, .pdf)
│   ├── storage/
│   │   └── minio_client.py # MinIO upload / download / exists
│   ├── regulatory/
│   │   └── europa.py       # data.europa.eu Hub Search API client
│   └── db/
│       └── repository.py   # PostgreSQL persistence + BI queries
├── tests/
│   ├── test_extractor.py   # Smoke tests — no Docker needed
│   ├── test_regulatory.py  # API response parsing tests — no network
│   ├── test_storage.py     # Storage unit tests (moto mock)
│   └── test_integration.py # End-to-end tests — requires Docker
├── scripts/
│   └── init.sql            # Auto-run schema on first postgres start
├── sample_contracts/       # Drop .docx / .pdf files here to process
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── requirements.txt
├── .env.example
└── pytest.ini
```

---

## Setup

### 1. Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- Python 3.12 (for running tests locally without Docker)

### 2. Clone and configure

```bash
git clone git@github.com:ivanemoje/deliveryhero.git
cd deliveryhero

# Copy the example env — other than GCP, defaults work out of the box 
cp .env.example .env
```

### 3. Add sample contracts

```bash
mkdir -p sample_contracts
cp /path/to/your/contracts/*.docx sample_contracts/
cp /path/to/your/contracts/*.pdf  sample_contracts/
```

### 4. Build and start services

```bash
make build    # builds the pipeline Docker image
make up       # starts postgres + minio (detached)
```

MinIO console is available at **http://localhost:9001**
(credentials: `minioadmin` / `minioadmin`)

### 5. Run the pipeline

```bash
make run
```

This processes every `.docx` and `.pdf` in `./sample_contracts/`, uploads each
file to MinIO, extracts metadata, and persists it to PostgreSQL.

### 5. Process a Google Doc**

This processes every `.docx` and `.pdf` passed as arguments. If the drive folder id is shared, all files inside are processed:

```bash
make run-gdoc GDOC_IDS="1CFaxG_LurzLL8Gp5NGUzs09Pubu60TPp"
```
# or multiple:
```bash
make run-gdoc GDOC_IDS="1CFaxG_LurzLL8Gp5NGUzs09Pubu60TPp 1csKnQJWJ9GPg0d-hfEqaiTyyTQxMr-fY"
```

Requires `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env` pointing to a service account
with Viewer access to the document. On GCP VMs, Application Default Credentials
are used automatically if the env var is blank.

### 6. Run regulatory sync

```bash
make sync-regulatory
```

This fetches Europa datasets for active contract versions and persists them to
`regulatory_datasets`.

---

## Tests

### Smoke tests (no Docker required)

Tests the extractor, storage, and regulatory parser modules in isolation.

```bash
make test
```

Expected output:

```
tests/test_extractor.py::test_dummy1_parties       PASSED
tests/test_extractor.py::test_dummy1_financial     PASSED
tests/test_extractor.py::test_dummy1_dates         PASSED
tests/test_extractor.py::test_dummy1_obligations   PASSED
tests/test_extractor.py::test_dummy2_financial     PASSED
tests/test_extractor.py::test_dummy2_expiration_one_year PASSED
tests/test_extractor.py::test_unsupported_format_raises  PASSED
tests/test_extractor.py::test_gdoc_scheme_raises_without_credentials PASSED
tests/test_regulatory.py::test_parse_nested_europa_response PASSED
tests/test_regulatory.py::test_regulatory_node_persists_results PASSED
tests/test_regulatory.py::test_regulatory_node_can_be_disabled PASSED
tests/test_storage.py::test_upload_and_exists      PASSED
tests/test_storage.py::test_download_round_trip    PASSED
tests/test_storage.py::test_object_not_exists      PASSED
```

> **Note:** `test_extractor.py` tests are skipped automatically if sample
> contracts are not in `sample_contracts/`. The unsupported-format test always runs.

### Integration tests (requires Docker)

Tests the full graph: real MinIO upload + real PostgreSQL insert.

```bash
make test-integration
```

This will:
1. Start `postgres` and `minio` containers if not already running
2. Wait 15 seconds for health checks to pass
3. Run `tests/test_integration.py` against `localhost:5432` and `localhost:9000`

Expected output:

```
tests/test_integration.py::test_full_pipeline_dummy1   PASSED
tests/test_integration.py::test_pipeline_invalid_file  PASSED
```

### Run all tests together

```bash
make up
make test
make test-integration
```

---

## Useful commands

```bash
make logs     # tail pipeline container logs
make down     # stop containers (keeps volumes)
make clean    # stop containers + delete volumes + remove built image
```

---

## Data Model

The PostgreSQL schema is normalized around long-term reporting and lifecycle
management:

| Table | Purpose |
|---|---|
| `parties` | Legal entities with normalized country-level location |
| `source_documents` | Idempotent document ingestion keys and source metadata |
| `contract_masters` | Stable relationship between client and service provider |
| `contract_versions` | Successive contract versions, amendments, renewals, status, dates, values |
| `contract_party_roles` | Role history per version |
| `renewal_terms` | Renewal sequence, notice period, and calculated notice deadline |
| `payment_schedule` | Normalized financial schedule for BI |
| `contract_clauses` | Clause-level facts, thresholds, confidence, and metadata |
| `extraction_audit` | Field-level extraction audit trail |
| `regulatory_datasets` | EU Data Portal results linked to the contract version |

`contracts` is exposed as a compatibility view over `contract_versions` so
legacy queries can still select `contract_id`, parties, status, expiration date,
value, currency, clauses, and source key.

The renewal relationship is modeled as a master contract with many successive
`contract_versions`. Each version can point to its predecessor, and the latest
active version supersedes the previous one when a new version is inserted.

### BI Queries

The repository exposes the required assessment queries:

- `expiration_risk(days=90)`: active contracts expiring within the window where
  the non-renewal notice deadline has already passed.
- `financial_exposure_by_provider_location()`: active contract value grouped by
  provider country and currency.
- `force_majeure_immediate_termination(delay_days=14)`: active contracts where a
  Force Majeure event allows immediate termination at or before the delay
  threshold.

Equivalent SQL lives in `src/db/repository.py` and is PostgreSQL-compatible.

---

## Regulatory API

`src/regulatory/europa.py` connects to the official data.europa.eu Hub Search
API and returns a clean list of `Title`, `Description`, and `ID` values.
Regulatory enrichment runs as a separate job via `make sync-regulatory`; it uses
active contract versions' Effective Dates, retrieves the top datasets, and stores
them in `regulatory_datasets` against each `contract_version_id`.

The parser is defensive because the API response can contain nested result 
containers and localized text dictionaries.

---

## OCR Strategy

Native digital files are parsed with structured extractors first: `python-docx`
for DOCX content and `pdfplumber` for embedded PDF text. If a PDF page has no
extractable text, the extractor falls back to OCR with `pytesseract` at 300 DPI.

OCR output is normalized before field extraction by repairing common line-break
hyphenation, collapsing repeated spaces, and normalizing currency symbols. In a
production version, the audit table would also store confidence scores per field
and route low-confidence extractions to human review.

---

## LLM Strategy

Contracts are treated as private legal documents. All LLM calls run inside a private GCP project deployed in eu (or any relevant region) behind a VPC Service Control perimeter — contract text never leaves the EU/region data boundary. Cloud Logging is disabled for this endpoint; only approved audit metadata is retained.

The Vertex AI prompt instructs the gemini model gemini-1.5-pro to:


- Classify only the clause types defined in an explicit JSON schema — no freeform output.
- Return `null` for any field where evidence is absent, never invent values.
- Include a short verbatim evidence span and a confidence score for every extracted field.
Use deterministic generation settings:
    - Use temperature=0 and response_mime_type="application/json" for schema-locked output.
    - Validate the response against the database schema before persistence.
- Run inside a private GCP project with no prompt logging beyond approved audit
  metadata.

Responses are validated against the database schema before persistence.Example response contract:

```json
{
  "clauses": [
    {
      "type": "FORCE_MAJEURE",
      "notice_period_days": 14,
      "performance_delay_threshold_days": 14,
      "immediate_termination_allowed": true,
      "evidence": "If a Force Majeure event prevents the Provider from performing for a period exceeding fourteen (14) consecutive days...",
      "confidence": 0.92
    },
    {
      "type": "NON_RENEWAL_NOTICE",
      "notice_period_months": 3,
      "notice_deadline_date": "2026-04-24",
      "bgb_625_excluded": true,
      "evidence": "either party provides written notice of non-renewal at least three (3) months prior to the Expiration Date",
      "confidence": 0.97
    },
    {
      "type": "GOVERNING_LAW",
      "jurisdiction": "Federal Republic of Germany",
      "venue": "Berlin",
      "evidence": "governed by and construed in accordance with the laws of the Federal Republic of Germany",
      "confidence": 0.99
    }
  ]
}
```

LLM calls can be cached to reduce further costs in certain cases.

---

## Proactive Alerting

For serverless alerting, the pipeline writes clause facts into PostgreSQL and
then emits a Slack alert when the Force Majeure notice period exceeds the
14-day termination trigger. Another alert can be fired a day before the threshold is reached, giving the team even more time to act. Locally this is handled by `node_notify`; in GCP the
same event can be sent through Pub/Sub to an n8n workflow:

1. Cloud Run pipeline completes extraction and persistence.
2. Pub/Sub publishes `{contract_id, force_majeure_notice_days}`.
3. n8n checks whether `force_majeure_notice_days > 14`.
4. n8n posts a Procurement Slack message with the contract ID, threshold, and
   link to the stored document.

Set `SLACK_WEBHOOK_URL` to enable local webhook delivery from the pipeline.

### Inspect the database directly

```bash
docker compose exec postgres psql -U legal -d contracts -c "
  SELECT c.contract_id, p.legal_name AS client, c.total_contract_value, c.currency
  FROM contracts c
  JOIN parties p ON p.party_id = c.client_party_id;
"
```

### Inspect MinIO

Open http://localhost:9001 → login `minioadmin`/`minioadmin` → browse the
`legal-documents` bucket.

---

## Configuration reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_USER` | `legal` | DB user |
| `POSTGRES_PASSWORD` | `legal` | DB password |
| `POSTGRES_DB` | `contracts` | Database name |
| `POSTGRES_HOST` | `postgres` | Host (use `localhost` for local tests) |
| `PGADMIN_EMAIL` | `admin@admin.com` |
| `PGADMIN_PASSWORD` | `admin` |
| `PGADMIN_HOST_PORT` | `5050` |
| `MINIO_ENDPOINT` | `minio:9000` | MinIO API endpoint |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `MINIO_BUCKET` | `legal-documents` | Target bucket name |
| `MINIO_USE_SSL` | `false` | Use HTTPS for MinIO/S3 endpoint |
| `EUROPA_API_BASE` | `https://data.europa.eu/api/hub/search/search` | EU Data Portal Hub Search API |
| `EUROPA_QUERY` | `Digital Services OR Data Protection` | Regulatory search query |
| `EUROPA_LIMIT` | `5` | Number of regulatory datasets to persist |
| `AGENT_MAX_RETRIES` | `2` | Max extract retries on validation failure |
| `SLACK_WEBHOOK_URL` | empty | Optional Slack incoming webhook for Procurement alerts |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | empty | Optional service account path for Google Docs extraction |

---
