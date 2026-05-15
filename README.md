# Legal Contract Pipeline

Agentic document intelligence pipeline for extracting, storing, and querying
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
| `notify` | Log result; warn if Force Majeure threshold ≤ 14 days |

---

## Project Structure

```
legal-pipeline/
├── src/
│   ├── agent/
│   │   ├── graph.py        # LangGraph state graph definition
│   │   └── pipeline.py     # CLI entry point
│   ├── extractor/
│   │   └── extract.py      # Format-agnostic metadata parser (.docx, .pdf)
│   ├── storage/
│   │   └── minio_client.py # MinIO upload / download / exists
│   └── db/
│       └── repository.py   # PostgreSQL persistence + BI queries
├── tests/
│   ├── test_extractor.py   # Smoke tests — no Docker needed
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
- Google Service Account (with read access to the google docs)

### 2. Clone and configure

```bash
git clone git@github.com:ivanemoje/deliveryhero.git
cd deliveryhero

# Copy the example env — defaults work out of the box
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

**Process a Google Doc** — pass the document ID:
```bash
make run-gdoc GDOC_IDS="1CFaxG_LurzLL8Gp5NGUzs09Pubu60TPp"
# or multiple:
make run-gdoc GDOC_IDS="1CFaxG_LurzLL8Gp5NGUzs09Pubu60TPp 2CyiNWxyz"
```
Requires `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env` pointing to a service account
with Viewer access to the document. On GCP VMs, Application Default Credentials
are used automatically if the env var is blank.

---

## Tests

### Smoke tests (no Docker required)

Tests the extractor and storage modules in isolation.

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
tests/test_storage.py::test_upload_and_exists      PASSED
tests/test_storage.py::test_download_round_trip    PASSED
tests/test_storage.py::test_object_not_exists      PASSED
```

**Note:** `test_extractor.py` tests are skipped automatically if sample contracts are not in `sample_contracts/`. The unsupported-format test always runs.

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
| `MINIO_ENDPOINT` | `minio:9000` | MinIO API endpoint |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `MINIO_BUCKET` | `legal-documents` | Target bucket name |
| `AGENT_MAX_RETRIES` | `2` | Max extract retries on validation failure |

---

## Extending the pipeline

**Add a new file format** — add one entry to `_READERS` in `src/extractor/extract.py`:
```python
_READERS[".rtf"] = _text_from_rtf
```



**Add a new graph node** (e.g. Slack alert) — add a function in `src/agent/graph.py`,
register it with `g.add_node`, and wire it into the edges. The rest of the graph
is unaffected.

**Add a new clause type** — extend `_obligations()` in `src/extractor/extract.py`
and add a corresponding INSERT in `src/db/repository.py`.

## TODOs
- drive
- add pgadmin, 
- initialize contracts folder in minio
- check contracts being executed
- update the README
- give container names
- perfect document extraction
- remove bloat (agent)

### comments
- focus on doc extraction // make perfect