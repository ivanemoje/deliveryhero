"""
Storage unit tests using moto to mock the S3/MinIO API.
No Docker needed.
Run with: pytest tests/test_storage.py -v
"""

import os
from unittest.mock import patch

import pytest

# Set env vars before importing storage module
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin")
os.environ.setdefault("MINIO_BUCKET", "legal-documents")
os.environ.setdefault("MINIO_USE_SSL", "false")


@pytest.fixture()
def mock_s3(tmp_path):
    """Spin up an in-process mock S3 using moto."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        # Create the bucket the storage module expects
        s3 = boto3.client(
            "s3",
            endpoint_url=None,
            region_name="eu-west-1",
            aws_access_key_id="minioadmin",
            aws_secret_access_key="minioadmin",
        )
        s3.create_bucket(Bucket="legal-documents")

        # Patch _client() in storage to return this mock client
        with patch("src.storage.minio_client._client", return_value=s3):
            yield s3, tmp_path


def test_upload_and_exists(mock_s3):
    s3, tmp = mock_s3
    f = tmp / "contract.docx"
    f.write_bytes(b"dummy contract content")

    from src.storage import object_exists, upload_file

    key = upload_file(f, "contracts/contract.docx")
    assert key == "contracts/contract.docx"
    assert object_exists("contracts/contract.docx")


def test_download_round_trip(mock_s3):
    s3, tmp = mock_s3
    src = tmp / "upload.docx"
    src.write_bytes(b"round trip content")

    from src.storage import download_file, upload_file

    upload_file(src, "contracts/upload.docx")

    dest = tmp / "download.docx"
    result = download_file("contracts/upload.docx", dest)
    assert result.read_bytes() == b"round trip content"


def test_object_not_exists(mock_s3):
    from src.storage import object_exists

    assert not object_exists("contracts/nonexistent.docx")
