"""
Smoke tests for the extractor — no Docker, no network, no DB.
Run with: pytest tests/test_extractor.py -v
"""

from pathlib import Path

import pytest
from google.auth.exceptions import DefaultCredentialsError

from src.extractor.extract import extract_contract_metadata

DUMMY1 = Path("sample_contracts/Dummy_1_PROFESSIONAL_SERVICE_AGREEMENT.docx")
DUMMY2 = Path("sample_contracts/Dummy_2_PROFESSIONAL_SERVICE_AGREEMENT.docx")


@pytest.mark.skipif(not DUMMY1.exists(), reason="sample contract not present")
def test_dummy1_parties():
    meta = extract_contract_metadata(DUMMY1)
    assert meta.client_name == "mnb"
    assert meta.client_location == "Germany"
    assert meta.provider_name == "lfg"


@pytest.mark.skipif(not DUMMY1.exists(), reason="sample contract not present")
def test_dummy1_financial():
    meta = extract_contract_metadata(DUMMY1)
    assert meta.total_contract_value == 150_000.0
    assert meta.currency == "EUR"


@pytest.mark.skipif(not DUMMY1.exists(), reason="sample contract not present")
def test_dummy1_dates():
    meta = extract_contract_metadata(DUMMY1)
    assert meta.effective_date == "2025-07-24"
    assert meta.expiration_date == "2026-07-23"


@pytest.mark.skipif(not DUMMY1.exists(), reason="sample contract not present")
def test_dummy1_obligations():
    meta = extract_contract_metadata(DUMMY1)
    assert meta.force_majeure_notice_days == 14
    assert meta.non_renewal_notice_months == 3


@pytest.mark.skipif(not DUMMY2.exists(), reason="sample contract not present")
def test_dummy2_financial():
    meta = extract_contract_metadata(DUMMY2)
    assert meta.total_contract_value == 300_000.0
    assert meta.currency == "USD"


@pytest.mark.skipif(not DUMMY2.exists(), reason="sample contract not present")
def test_dummy2_expiration_one_year():
    meta = extract_contract_metadata(DUMMY2)
    assert meta.expiration_date == "2026-12-24"


def test_unsupported_format_raises():
    with pytest.raises(ValueError, match="Unsupported format"):
        extract_contract_metadata(Path("some_file.csv"))


def test_gdoc_scheme_raises_without_credentials():
    """gdoc:// must raise — either missing dep (ImportError) or auth failure (OSError/DefaultCredentialsError)."""
    # Specifically catching the types we expect to be raised by Google libraries
    with pytest.raises((ImportError, OSError, DefaultCredentialsError)):
        extract_contract_metadata("gdoc://some-fake-doc-id")
