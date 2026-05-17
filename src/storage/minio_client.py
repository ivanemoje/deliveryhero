"""
storage/minio_client.py
Single responsibility: put/get files in MinIO. No parsing, no DB calls.
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def _client():
    return boto3.client(
        "s3",
        endpoint_url=f"http{'s' if os.getenv('MINIO_USE_SSL','false').lower()=='true' else ''}://{os.environ['MINIO_ENDPOINT']}",
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",  # MinIO ignores region but boto3 requires one
    )


def upload_file(local_path: Path, object_key: str) -> str:
    """Upload a file to MinIO. Returns the object key."""
    bucket = os.environ["MINIO_BUCKET"]
    _client().upload_file(str(local_path), bucket, object_key)
    return object_key


def download_file(object_key: str, dest_path: Path) -> Path:
    """Download an object from MinIO to dest_path. Returns dest_path."""
    bucket = os.environ["MINIO_BUCKET"]
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    _client().download_file(bucket, object_key, str(dest_path))
    return dest_path


def object_exists(object_key: str) -> bool:
    bucket = os.environ["MINIO_BUCKET"]
    try:
        _client().head_object(Bucket=bucket, Key=object_key)
        return True
    except ClientError:
        return False
