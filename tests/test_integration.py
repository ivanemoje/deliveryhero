"""
Integration tests — require running Docker services (postgres + minio).
Run with: make test-integration
          or: pytest tests/test_integration.py -v -m integration

These tests exercise the full pipeline graph end-to-end:
  real file → real MinIO upload → real PostgreSQL insert → verify row exists
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

DUMMY1 = Path("sample_contracts/Dummy_1_PROFESSIONAL_SERVICE_AGREEMENT.docx")


def _services_available() -> bool:
    """Return True only if both POSTGRES_HOST and MINIO_ENDPOINT are reachable."""
    import socket

    def reachable(host, port):
        try:
            with socket.create_connection((host, int(port)), timeout=2):
                return True
        except OSError:
            return False

    pg_ok = reachable(
        os.getenv("POSTGRES_HOST", "localhost"),
        os.getenv("POSTGRES_PORT", "5432"),
    )
    minio_host, minio_port = os.getenv("MINIO_ENDPOINT", "localhost:9000").split(":")
    minio_ok = reachable(minio_host, minio_port)
    return pg_ok and minio_ok


@pytest.fixture(scope="module", autouse=True)
def require_services():
    if not _services_available():
        pytest.skip("Docker services not running — skipping integration tests")


@pytest.mark.skipif(not DUMMY1.exists(), reason="sample contract not present")
def test_full_pipeline_dummy1():
    """
    Full end-to-end: ingest → extract → validate → persist.
    Verifies:
    - MinIO object is created
    - PostgreSQL contract row exists with correct values
    """
    from dotenv import load_dotenv

    load_dotenv()

    from sqlalchemy import text

    from src.agent.graph import pipeline_graph
    from src.db.repository import _engine
    from src.storage import object_exists

    # Clean up any record from a prior run so this test owns the full cycle
    file_key = f"contracts/{DUMMY1.name}"
    with _engine().begin() as conn:
        conn.execute(
            text("DELETE FROM contracts WHERE source_file_key = :key"),
            {"key": file_key},
        )

    state = pipeline_graph.invoke(
        {
            "file_path": str(DUMMY1),
            "object_key": None,
            "metadata": None,
            "contract_id": None,
            "error": None,
            "retries": 0,
            "status": "running",
        }
    )

    assert state["status"] == "ok", f"Pipeline failed: {state.get('error')}"
    assert state["contract_id"] is not None

    # Verify MinIO
    assert object_exists(f"contracts/{DUMMY1.name}")

    # Verify PostgreSQL
    with _engine().connect() as conn:
        row = conn.execute(
            text("SELECT total_contract_value, currency FROM contracts WHERE contract_id = :cid"),
            {"cid": state["contract_id"]},
        ).fetchone()

    assert row is not None
    assert float(row[0]) == 150_000.0
    assert row[1] == "EUR"


def test_pipeline_invalid_file(tmp_path):
    """Pipeline should gracefully fail on an unsupported file format."""
    bad_file = tmp_path / "contract.txt"
    bad_file.write_text("not a contract")

    from src.agent.graph import pipeline_graph

    state = pipeline_graph.invoke(
        {
            "file_path": str(bad_file),
            "object_key": None,
            "metadata": None,
            "contract_id": None,
            "error": None,
            "retries": 0,
            "status": "running",
        }
    )

    assert state["status"] == "failed"
    assert state["error"] is not None


def test_pipeline_skips_duplicate(tmp_path):
    """
    Running the pipeline twice on the same file should skip on the second run.
    """
    from sqlalchemy import text

    from src.agent.graph import pipeline_graph
    from src.db.repository import _engine

    if not DUMMY1.exists():
        pytest.skip("sample contract not present")

    file_key = f"contracts/{DUMMY1.name}"

    # Remove any record left by a prior test
    with _engine().begin() as conn:
        conn.execute(
            text("DELETE FROM contracts WHERE source_file_key = :key"),
            {"key": file_key},
        )

    def _run():
        return pipeline_graph.invoke(
            {
                "file_path": str(DUMMY1),
                "object_key": None,
                "metadata": None,
                "contract_id": None,
                "error": None,
                "retries": 0,
                "status": "running",
            }
        )

    first = _run()
    assert first["status"] == "ok"

    second = _run()
    assert second["status"] == "skipped"
    assert second.get("contract_id") is None