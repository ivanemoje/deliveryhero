import os
from unittest.mock import patch

import pytest


@pytest.fixture(scope="session", autouse=True)
def test_env():
    """Default environment variables for all tests."""
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"
    os.environ["AWS_ACCESS_KEY_ID"] = "minioadmin"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "minioadmin"
    os.environ["MINIO_ENDPOINT"] = "localhost:9000"
    os.environ["MINIO_BUCKET"] = "legal-documents"
    os.environ["MINIO_USE_SSL"] = "false"


@pytest.fixture()
def mock_s3(tmp_path):
    """Spin up an in-process mock S3 using moto with regional constraints."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        region = "eu-west-1"
        s3 = boto3.client("s3", region_name=region, endpoint_url=None)

        s3.create_bucket(
            Bucket="legal-documents", CreateBucketConfiguration={"LocationConstraint": region}
        )

        with patch("src.storage.minio_client._client", return_value=s3):
            yield s3, tmp_path
