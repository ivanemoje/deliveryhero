"""
pipeline.py — CLI entry point.
Walks --input-dir for .docx/.pdf files and/or accepts --gdoc-ids for Google Docs.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

SUPPORTED = {".docx", ".pdf"}


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


def run(input_dir: Path | None, gdoc_ids: list[str]) -> None:
    load_dotenv()

    sources: list[str] = []

    # Process local directory
    if input_dir and input_dir.exists():
        sources += [str(f) for f in input_dir.iterdir() if f.suffix.lower() in SUPPORTED]

    # Process Google Doc IDs
    sources += [f"gdoc://{doc_id}" for doc_id in gdoc_ids]

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
