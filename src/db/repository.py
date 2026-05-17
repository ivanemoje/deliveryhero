"""
db/repository.py
Single responsibility: persist and query ContractMetadata.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from src.extractor import ContractMetadata
from src.regulatory import DatasetSummary


def _engine():
    user = os.environ.get("POSTGRES_USER", "postgres")
    pw = os.environ.get("POSTGRES_PASSWORD", "postgres")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "legal_db")

    url = f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"
    return create_engine(url, poolclass=NullPool)


def save_contract(meta: ContractMetadata, file_key: str) -> str:
    """Persist one extracted contract and return the contract version id."""
    engine = _engine()
    with engine.begin() as conn:
        source_document_id = _upsert_source_document(conn, meta, file_key)
        client_id = _upsert_party(conn, meta.client_name, meta.client_location)
        provider_id = _upsert_party(conn, meta.provider_name, meta.provider_location)
        master_contract_id = _upsert_contract_master(conn, client_id, provider_id)

        version_number = _next_version_number(conn, master_contract_id)
        predecessor_version_id = _latest_active_version(conn, master_contract_id)
        if predecessor_version_id:
            _supersede_version(conn, predecessor_version_id)

        contract_id = _insert_contract_version(
            conn,
            meta=meta,
            master_contract_id=master_contract_id,
            source_document_id=source_document_id,
            predecessor_version_id=predecessor_version_id,
            version_number=version_number,
        )

        _insert_party_roles(conn, contract_id, client_id, provider_id)
        _insert_renewal_terms(conn, contract_id, meta)
        _insert_payment_schedule(conn, contract_id, meta)
        _insert_normalized_clauses(conn, contract_id, meta)
        _insert_extraction_audit(conn, contract_id, source_document_id, meta)

    return contract_id


def _upsert_source_document(conn, meta: ContractMetadata, file_key: str) -> str:
    row = conn.execute(
        text("""
            INSERT INTO source_documents (source_file_key, source_name, file_format)
            VALUES (:file_key, :source_name, :file_format)
            ON CONFLICT (source_file_key) DO UPDATE SET
                source_name = EXCLUDED.source_name,
                file_format = EXCLUDED.file_format
            RETURNING source_document_id
        """),
        {
            "file_key": file_key,
            "source_name": meta.source_file,
            "file_format": meta.file_format,
        },
    ).fetchone()
    return str(row[0])


def _upsert_party(conn, name: str | None, location: str | None) -> str:
    legal_name = (name or "UNKNOWN").strip() or "UNKNOWN"
    country = _country_from_location(location)

    row = conn.execute(
        text("""
            INSERT INTO parties (legal_name, country)
            VALUES (:name, :country)
            ON CONFLICT (legal_name, country) DO UPDATE SET
                updated_at = NOW()
            RETURNING party_id
        """),
        {"name": legal_name, "country": country},
    ).fetchone()
    return str(row[0])


def _country_from_location(location: str | None) -> str:
    if not location:
        return "UNKNOWN"
    parts = [part.strip().rstrip(".") for part in location.split(",") if part.strip()]
    return parts[-1] if parts else "UNKNOWN"


def _upsert_contract_master(conn, client_id: str, provider_id: str) -> str:
    row = conn.execute(
        text("""
            INSERT INTO contract_masters (client_party_id, provider_party_id)
            VALUES (:client_id, :provider_id)
            ON CONFLICT (client_party_id, provider_party_id) DO UPDATE SET
                updated_at = NOW()
            RETURNING master_contract_id
        """),
        {"client_id": client_id, "provider_id": provider_id},
    ).fetchone()
    return str(row[0])


def _next_version_number(conn, master_contract_id: str) -> int:
    row = conn.execute(
        text("""
            SELECT COALESCE(MAX(version_number), 0) + 1
            FROM contract_versions
            WHERE master_contract_id = :master_contract_id
        """),
        {"master_contract_id": master_contract_id},
    ).fetchone()
    return int(row[0])


def _latest_active_version(conn, master_contract_id: str) -> str | None:
    row = conn.execute(
        text("""
            SELECT contract_version_id
            FROM contract_versions
            WHERE master_contract_id = :master_contract_id
              AND status = 'ACTIVE'
            ORDER BY version_number DESC
            LIMIT 1
        """),
        {"master_contract_id": master_contract_id},
    ).fetchone()
    return str(row[0]) if row else None


def _supersede_version(conn, contract_version_id: str) -> None:
    conn.execute(
        text("""
            UPDATE contract_versions
            SET status = 'SUPERSEDED', superseded_at = NOW()
            WHERE contract_version_id = :contract_version_id
        """),
        {"contract_version_id": contract_version_id},
    )


def _insert_contract_version(
    conn,
    *,
    meta: ContractMetadata,
    master_contract_id: str,
    source_document_id: str,
    predecessor_version_id: str | None,
    version_number: int,
) -> str:
    row = conn.execute(
        text("""
            INSERT INTO contract_versions (
                master_contract_id,
                source_document_id,
                predecessor_version_id,
                version_number,
                status,
                effective_date,
                expiration_date,
                total_contract_value,
                currency,
                governing_law,
                venue,
                jurisdiction_tags,
                clauses_snapshot
            ) VALUES (
                :master_contract_id,
                :source_document_id,
                :predecessor_version_id,
                :version_number,
                'ACTIVE',
                :effective_date,
                :expiration_date,
                :total_contract_value,
                :currency,
                :governing_law,
                :venue,
                :jurisdiction_tags,
                cast(:clauses_snapshot as jsonb)
            )
            RETURNING contract_version_id
        """),
        {
            "master_contract_id": master_contract_id,
            "source_document_id": source_document_id,
            "predecessor_version_id": predecessor_version_id,
            "version_number": version_number,
            "effective_date": meta.effective_date,
            "expiration_date": meta.expiration_date,
            "total_contract_value": meta.total_contract_value,
            "currency": meta.currency,
            "governing_law": meta.governing_law,
            "venue": meta.venue,
            "jurisdiction_tags": _derive_jurisdiction_tags(meta),
            "clauses_snapshot": json.dumps(_build_clauses_snapshot(meta)),
        },
    ).fetchone()
    return str(row[0])


def _insert_party_roles(conn, contract_id: str, client_id: str, provider_id: str) -> None:
    conn.execute(
        text("""
            INSERT INTO contract_party_roles (contract_version_id, party_id, role)
            VALUES
                (:contract_id, :client_id, 'CLIENT'),
                (:contract_id, :provider_id, 'SERVICE_PROVIDER')
            ON CONFLICT DO NOTHING
        """),
        {"contract_id": contract_id, "client_id": client_id, "provider_id": provider_id},
    )


def _insert_renewal_terms(conn, contract_id: str, meta: ContractMetadata) -> None:
    notice_days = _months_to_days(meta.non_renewal_notice_months)
    notice_deadline = _notice_deadline(meta.expiration_date, notice_days)

    conn.execute(
        text("""
            INSERT INTO renewal_terms (
                contract_version_id,
                auto_renews,
                renewal_period_months,
                non_renewal_notice_days,
                notice_deadline_date,
                metadata
            ) VALUES (
                :contract_id,
                FALSE,
                NULL,
                :notice_days,
                :notice_deadline,
                cast(:metadata as jsonb)
            )
        """),
        {
            "contract_id": contract_id,
            "notice_days": notice_days,
            "notice_deadline": notice_deadline,
            "metadata": json.dumps({"source": "non_renewal_notice_months"}),
        },
    )


def _insert_payment_schedule(conn, contract_id: str, meta: ContractMetadata) -> None:
    if meta.total_contract_value is None or meta.currency is None:
        return
    conn.execute(
        text("""
            INSERT INTO payment_schedule (
                contract_version_id,
                amount,
                currency,
                cadence,
                description
            ) VALUES (
                :contract_id,
                :amount,
                :currency,
                'contract_total',
                'Extracted total contract value'
            )
        """),
        {
            "contract_id": contract_id,
            "amount": meta.total_contract_value,
            "currency": meta.currency,
        },
    )


def _insert_normalized_clauses(conn, contract_id: str, meta: ContractMetadata) -> None:
    if meta.force_majeure_notice_days is not None:
        fm_meta = json.dumps({"trigger_events": ["act_of_god", "war"], "consecutive": True})
        conn.execute(
            text("""
                INSERT INTO contract_clauses (
                    contract_version_id,
                    clause_type,
                    notice_period_days,
                    performance_delay_threshold_days,
                    immediate_termination_allowed,
                    confidence_score,
                    extraction_method,
                    metadata
                ) VALUES (
                    :contract_id,
                    'FORCE_MAJEURE',
                    :days,
                    :days,
                    TRUE,
                    0.80,
                    'regex',
                    cast(:metadata as jsonb)
                )
            """),
            {
                "contract_id": contract_id,
                "days": meta.force_majeure_notice_days,
                "metadata": fm_meta,
            },
        )
    if meta.non_renewal_notice_months is not None:
        nr_meta = json.dumps({"method": "written_notice", "auto_renew_excluded": True})
        conn.execute(
            text("""
                INSERT INTO contract_clauses (
                    contract_version_id,
                    clause_type,
                    notice_period_months,
                    notice_period_days,
                    confidence_score,
                    extraction_method,
                    metadata
                ) VALUES (
                    :contract_id,
                    'NON_RENEWAL_NOTICE',
                    :months,
                    :days,
                    0.80,
                    'regex',
                    cast(:metadata as jsonb)
                )
            """),
            {
                "contract_id": contract_id,
                "months": meta.non_renewal_notice_months,
                "days": _months_to_days(meta.non_renewal_notice_months),
                "metadata": nr_meta,
            },
        )


def _insert_extraction_audit(
    conn, contract_id: str, source_document_id: str, meta: ContractMetadata
) -> None:
    rows = []
    for field_name, value in vars(meta).items():
        if value is None:
            continue
        rows.append(
            {
                "contract_id": contract_id,
                "source_document_id": source_document_id,
                "field_name": field_name,
                "extracted_value": str(value),
            }
        )
    if not rows:
        return
    conn.execute(
        text("""
            INSERT INTO extraction_audit (
                contract_version_id,
                source_document_id,
                field_name,
                extracted_value,
                confidence_score,
                extraction_method
            ) VALUES (
                :contract_id,
                :source_document_id,
                :field_name,
                :extracted_value,
                0.80,
                'regex'
            )
        """),
        rows,
    )


def _months_to_days(months: int | None) -> int | None:
    return months * 30 if months is not None else None


def _notice_deadline(expiration_date: str | None, notice_days: int | None) -> date | None:
    if not expiration_date or notice_days is None:
        return None
    return datetime.strptime(expiration_date, "%Y-%m-%d").date() - timedelta(days=notice_days)


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
        clauses.append(
            {
                "type": "FORCE_MAJEURE",
                "notice_days": meta.force_majeure_notice_days,
                "performance_delay_threshold_days": meta.force_majeure_notice_days,
                "immediate_termination_allowed": True,
            }
        )
    if meta.non_renewal_notice_months is not None:
        clauses.append(
            {
                "type": "NON_RENEWAL_NOTICE",
                "notice_months": meta.non_renewal_notice_months,
                "notice_days": _months_to_days(meta.non_renewal_notice_months),
            }
        )
    return clauses


def file_already_processed(file_key: str) -> bool:
    engine = _engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM source_documents WHERE source_file_key = :key LIMIT 1"),
            {"key": file_key},
        ).fetchone()
    return row is not None


def expiring_soon(days: int = 90) -> list[dict]:
    """Compatibility query for active contracts expiring within a date window."""
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    cv.contract_version_id AS contract_id,
                    p_c.legal_name AS client,
                    cv.expiration_date
                FROM contract_versions cv
                JOIN contract_masters cm ON cm.master_contract_id = cv.master_contract_id
                JOIN parties p_c ON p_c.party_id = cm.client_party_id
                WHERE cv.status = 'ACTIVE'
                  AND cv.expiration_date BETWEEN CURRENT_DATE AND CURRENT_DATE + (:days * INTERVAL '1 day')
            """),
            {"days": days},
        )
        return [dict(r._mapping) for r in rows]


def expiration_risk(days: int = 90) -> list[dict]:
    """Contracts expiring soon where the non-renewal notice deadline has already passed."""
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    cv.contract_version_id AS contract_id,
                    p_c.legal_name AS client,
                    p_p.legal_name AS service_provider,
                    cv.expiration_date,
                    rt.notice_deadline_date,
                    rt.non_renewal_notice_days
                FROM contract_versions cv
                JOIN contract_masters cm ON cm.master_contract_id = cv.master_contract_id
                JOIN parties p_c ON p_c.party_id = cm.client_party_id
                JOIN parties p_p ON p_p.party_id = cm.provider_party_id
                JOIN renewal_terms rt ON rt.contract_version_id = cv.contract_version_id
                WHERE cv.status = 'ACTIVE'
                  AND cv.expiration_date BETWEEN CURRENT_DATE AND CURRENT_DATE + (:days * INTERVAL '1 day')
                  AND rt.notice_deadline_date < CURRENT_DATE
            """),
            {"days": days},
        )
        return [dict(r._mapping) for r in rows]


def financial_exposure_by_provider_location() -> list[dict]:
    """Total active contract value grouped by service provider country."""
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    p_p.country AS provider_location,
                    cv.currency,
                    SUM(cv.total_contract_value) AS total_contract_value,
                    COUNT(*) AS active_contract_count
                FROM contract_versions cv
                JOIN contract_masters cm ON cm.master_contract_id = cv.master_contract_id
                JOIN parties p_p ON p_p.party_id = cm.provider_party_id
                WHERE cv.status = 'ACTIVE'
                  AND cv.total_contract_value IS NOT NULL
                GROUP BY p_p.country, cv.currency
                ORDER BY p_p.country, cv.currency
            """)
        )
        return [dict(r._mapping) for r in rows]


def force_majeure_immediate_termination(delay_days: int = 14) -> list[dict]:
    """Contracts where Force Majeure allows immediate termination after a delay threshold."""
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    cv.contract_version_id AS contract_id,
                    p_c.legal_name AS client,
                    p_p.legal_name AS service_provider,
                    cc.performance_delay_threshold_days,
                    cc.immediate_termination_allowed
                FROM contract_versions cv
                JOIN contract_masters cm ON cm.master_contract_id = cv.master_contract_id
                JOIN parties p_c ON p_c.party_id = cm.client_party_id
                JOIN parties p_p ON p_p.party_id = cm.provider_party_id
                JOIN contract_clauses cc ON cc.contract_version_id = cv.contract_version_id
                WHERE cv.status = 'ACTIVE'
                  AND cc.clause_type = 'FORCE_MAJEURE'
                  AND cc.immediate_termination_allowed IS TRUE
                  AND cc.performance_delay_threshold_days <= :delay_days
            """),
            {"delay_days": delay_days},
        )
        return [dict(r._mapping) for r in rows]


def active_contract_versions_for_regulatory_sync() -> list[dict]:
    """Active contract versions with effective dates for regulatory enrichment."""
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    contract_version_id AS contract_id,
                    effective_date
                FROM contract_versions
                WHERE status = 'ACTIVE'
                  AND effective_date IS NOT NULL
                ORDER BY effective_date DESC, contract_version_id
            """)
        )
        return [dict(r._mapping) for r in rows]


def save_regulatory_datasets(
    contract_id: str,
    datasets: list[DatasetSummary],
    *,
    query: str,
    published_after: date,
) -> int:
    """Persist regulatory API results for a contract version."""
    if not datasets:
        return 0

    rows = [
        {
            "contract_id": contract_id,
            "dataset_id": dataset.dataset_id or dataset.title,
            "title": dataset.title,
            "description": dataset.description,
            "query": query,
            "published_after": published_after,
            "metadata": json.dumps({}),
        }
        for dataset in datasets
    ]

    engine = _engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO regulatory_datasets (
                    contract_version_id,
                    dataset_id,
                    title,
                    description,
                    query,
                    published_after,
                    metadata
                ) VALUES (
                    :contract_id,
                    :dataset_id,
                    :title,
                    :description,
                    :query,
                    :published_after,
                    cast(:metadata as jsonb)
                )
                ON CONFLICT (contract_version_id, dataset_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    query = EXCLUDED.query,
                    published_after = EXCLUDED.published_after,
                    retrieved_at = NOW(),
                    metadata = EXCLUDED.metadata
            """),
            rows,
        )

    return result.rowcount or 0
