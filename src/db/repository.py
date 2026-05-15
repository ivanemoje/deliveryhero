"""
db/repository.py
Single responsibility: persist and query ContractMetadata.
"""

from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from src.extractor import ContractMetadata


def _engine():
    user = os.environ.get("POSTGRES_USER", "postgres")
    pw = os.environ.get("POSTGRES_PASSWORD", "postgres")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "legal_db")

    url = f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"
    return create_engine(url, poolclass=NullPool)


def save_contract(meta: ContractMetadata, file_key: str) -> str:
    engine = _engine()
    with engine.begin() as conn:
        client_id = _upsert_party(conn, meta.client_name, meta.client_location)
        provider_id = _upsert_party(conn, meta.provider_name, meta.provider_location)

        tags = _derive_jurisdiction_tags(meta)
        clauses_snapshot = _build_clauses_snapshot(meta)

        # Handle the payment schedule logic
        insert_sql = text("""
        INSERT INTO contracts (
            client_party_id, provider_party_id, status,
            effective_date, expiration_date,
            total_contract_value, currency,
            payment_schedule,
            governing_law, venue,
            jurisdiction_tags,
            clauses_snapshot,
            source_file_key
        ) VALUES (
            :client_id, :provider_id, 'ACTIVE',
            :effective_date, :expiration_date,
            :total_contract_value, :currency,
            CASE
                WHEN :total_contract_value IS NOT NULL AND :currency IS NOT NULL
                THEN ARRAY[ROW(:total_contract_value, :currency)::money_amount_t]
                ELSE NULL
            END,
            :governing_law, :venue,
            :jurisdiction_tags,
            cast(:clauses_snapshot as jsonb),
            :file_key
        )
        RETURNING contract_id
    """)

        params = {
            "client_id": client_id,
            "provider_id": provider_id,
            "effective_date": meta.effective_date,
            "expiration_date": meta.expiration_date,
            "total_contract_value": meta.total_contract_value,
            "currency": meta.currency,
            "governing_law": meta.governing_law,
            "venue": meta.venue,
            "jurisdiction_tags": tags,
            "clauses_snapshot": json.dumps(clauses_snapshot),
            "file_key": file_key,
        }

        row = conn.execute(insert_sql, params)
        contract_id = str(row.fetchone()[0])

        _insert_normalized_clauses(conn, contract_id, meta)

    return contract_id


def _upsert_party(conn, name: str | None, location: str | None) -> str:
    if not name:
        name, location = "UNKNOWN", "UNKNOWN"

    country = (location or "UNKNOWN").strip().rstrip(".")

    upsert_sql = text("""
        INSERT INTO parties (legal_name, address, roles)
        VALUES (:name, ROW(NULL, NULL, :country, NULL)::address_t, ARRAY[]::TEXT[])
        ON CONFLICT (legal_name, ((address).country)) DO NOTHING
        RETURNING party_id
    """)

    row = conn.execute(upsert_sql, {"name": name, "country": country}).fetchone()

    if row:
        return str(row[0])

    # Fallback select if DO NOTHING was triggered
    select_sql = text("""
        SELECT party_id FROM parties
        WHERE legal_name = :name AND (address).country = :country
    """)
    row = conn.execute(select_sql, {"name": name, "country": country}).fetchone()
    return str(row[0])


def _insert_normalized_clauses(conn, contract_id, meta):
    if meta.force_majeure_notice_days is not None:
        fm_meta = json.dumps({"trigger_events": ["act_of_god", "war"], "consecutive": True})
        conn.execute(
            text("""
                INSERT INTO contract_clauses (contract_id, clause_type, notice_period_days, metadata)
                VALUES (:cid, 'FORCE_MAJEURE', :days, cast(:meta as jsonb))
            """),
            {"cid": contract_id, "days": meta.force_majeure_notice_days, "meta": fm_meta},
        )
    if meta.non_renewal_notice_months is not None:
        nr_meta = json.dumps({"method": "written_notice", "auto_renew_excluded": True})
        conn.execute(
            text("""
                INSERT INTO contract_clauses (contract_id, clause_type, notice_period_months, metadata)
                VALUES (:cid, 'NON_RENEWAL_NOTICE', :months, cast(:meta as jsonb))
            """),
            {"cid": contract_id, "months": meta.non_renewal_notice_months, "meta": nr_meta},
        )


def _derive_jurisdiction_tags(meta: ContractMetadata) -> list[str]:
    raw = " ".join(
        filter(None, [meta.governing_law, meta.client_location, meta.provider_location, meta.venue])
    )
    mapping = {"germany": "Germany", "new york": "USA", "usa": "USA", "uk": "UK", "eu": "EU"}
    found = {canonical for key, canonical in mapping.items() if key in raw.lower()}
    return sorted(found)


def _build_clauses_snapshot(meta: ContractMetadata) -> list[dict]:
    clauses = []
    if meta.force_majeure_notice_days is not None:
        clauses.append({"type": "FORCE_MAJEURE", "notice_days": meta.force_majeure_notice_days})
    if meta.non_renewal_notice_months is not None:
        clauses.append(
            {"type": "NON_RENEWAL_NOTICE", "notice_months": meta.non_renewal_notice_months}
        )
    return clauses


def file_already_processed(file_key: str) -> bool:
    engine = _engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM contracts WHERE source_file_key = :key LIMIT 1"), {"key": file_key}
        ).fetchone()
    return row is not None


def expiring_soon(days: int = 90) -> list[dict]:
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT c.contract_id, p_c.legal_name AS client, c.expiration_date
                FROM contracts c
                JOIN parties p_c ON p_c.party_id = c.client_party_id
                WHERE c.status = 'ACTIVE'
                  AND c.expiration_date BETWEEN CURRENT_DATE AND CURRENT_DATE + (:days * INTERVAL '1 day')
            """),
            {"days": days},
        )
        return [dict(r._mapping) for r in rows]
