"""
regulatory/sync.py — standalone Europa regulatory enrichment job.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from src.db import active_contract_versions_for_regulatory_sync, save_regulatory_datasets
from src.regulatory import search_regulatory_datasets


def sync_regulatory_datasets() -> int:
    query = os.getenv("EUROPA_QUERY", "Digital Services OR Data Protection")
    limit = int(os.getenv("EUROPA_LIMIT", "5"))
    total_saved = 0

    contracts = active_contract_versions_for_regulatory_sync()
    print(f"Syncing regulatory datasets for {len(contracts)} active contract version(s).")

    for contract in contracts:
        contract_id = str(contract["contract_id"])
        effective_date = contract["effective_date"]
        datasets = search_regulatory_datasets(
            query=query,
            published_after=effective_date,
            limit=limit,
        )
        saved = save_regulatory_datasets(
            contract_id,
            datasets,
            query=query,
            published_after=effective_date,
        )
        total_saved += saved
        print(f"[REGULATORY] contract_id={contract_id} saved={saved}")

    print(f"Regulatory sync complete. saved={total_saved}")
    return total_saved


if __name__ == "__main__":
    load_dotenv()
    sync_regulatory_datasets()
