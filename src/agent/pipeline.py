"""
pipeline.py — CLI entry point.
Walks --input-dir for .docx/.pdf files and/or accepts --gdoc-ids for Google Docs.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

SUPPORTED = {".docx", ".pdf"}
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"
SUPPORTED_DRIVE_MIME_TYPES = {
    GOOGLE_DOC_MIME,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/pdf",
}


def _invoke(file_path: str) -> None:
    from src.agent.graph import pipeline_graph

    pipeline_graph.invoke(
        {
            "file_path": file_path,
            "object_key": None,
            "metadata": None,
            "contract_id": None,
            "error": None,
            "retries": 0,
            "status": "running",
        }
    )


def _expand_gdrive_id(file_id: str) -> list[str]:
    service = _drive_service()
    file_meta = (
        service.files()
        .get(fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True)
        .execute()
    )

    if file_meta.get("mimeType") != GOOGLE_FOLDER_MIME:
        return [file_id]

    mime_query = " or ".join(f"mimeType = '{mime}'" for mime in SUPPORTED_DRIVE_MIME_TYPES)
    query = f"'{file_id}' in parents and trashed = false and ({mime_query})"
    response = (
        service.files()
        .list(
            q=query,
            fields="files(id,name,mimeType)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=100,
        )
        .execute()
    )
    files = sorted(response.get("files", []), key=lambda item: item.get("name", ""))
    if not files:
        print(f"No supported Google Drive files found in folder: {file_id}")
    else:
        print(f"Expanded Google Drive folder {file_id} to {len(files)} supported file(s).")
    return [file["id"] for file in files]


def _drive_service():
    from google.auth import default as google_auth_default
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    if sa_path and os.path.exists(sa_path):
        creds = service_account.Credentials.from_service_account_file(sa_path, scopes=scopes)
    else:
        creds, _ = google_auth_default(scopes=scopes)
    return build("drive", "v3", credentials=creds)


def run(input_dir: Path | None, gdoc_ids: list[str]) -> None:
    load_dotenv()

    sources: list[str] = []

    # Process local directory
    if input_dir and input_dir.exists():
        sources += [str(f) for f in input_dir.iterdir() if f.suffix.lower() in SUPPORTED]

    # Process Google Drive file/folder IDs
    for doc_id in gdoc_ids:
        sources += [f"gdoc://{file_id}" for file_id in _expand_gdrive_id(doc_id)]

    if not sources:
        print("No files or Google Doc IDs to process.")
        return

    print(f"Processing {len(sources)} source(s)\n")
    for src in sources:
        _invoke(src)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legal contract pipeline")
    parser.add_argument("--input-dir", type=Path, default=None)
    parser.add_argument("--gdoc-ids", nargs="*", default=[])
    args = parser.parse_args()
    run(args.input_dir, args.gdoc_ids)
