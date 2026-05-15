"""
Storage unit tests.
Run with: pytest tests/test_storage.py -v
"""


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
