"""
regulatory/europa.py
Client for data.europa.eu Hub Search API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

DEFAULT_API_BASE = "https://data.europa.eu/api/hub/search/search"


@dataclass(frozen=True)
class DatasetSummary:
    title: str
    description: str
    dataset_id: str


def search_regulatory_datasets(
    *,
    query: str = "Digital Services OR Data Protection",
    published_after: date = date(2026, 4, 30),
    limit: int = 5,
    timeout_seconds: float = 15.0,
) -> list[DatasetSummary]:
    """
    Retrieve regulatory datasets published after a contract effective date.

    The API response shape has changed over time, so parsing is intentionally
    defensive and accepts common nested result containers.
    """
    base_url = os.getenv("EUROPA_API_BASE", DEFAULT_API_BASE)
    page_size = max(limit * 10, limit)
    params = {
        "q": query,
        "filter": "dataset",
        "pageSize": page_size,
    }

    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.get(base_url, params=params)
        response.raise_for_status()
        payload = response.json()

    return parse_dataset_response(payload, limit=limit, published_after=published_after)


def parse_dataset_response(
    payload: dict[str, Any],
    *,
    limit: int = 5,
    published_after: date | None = None,
) -> list[DatasetSummary]:
    records = _find_records(payload)
    summaries: list[DatasetSummary] = []

    for record in records:
        if published_after and not _is_after_published_date(record, published_after):
            continue

        title = _localized_text(_first_present(record, "title", "title_en", "name", "prefLabel"))
        description = _localized_text(
            _first_present(record, "description", "description_en", "notes", "abstract")
        )
        dataset_id = str(
            _first_present(record, "id", "identifier", "dataset_id", "uri") or ""
        ).strip()

        if not title and isinstance(record.get("dataset"), dict):
            nested = record["dataset"]
            title = _localized_text(_first_present(nested, "title", "name"))
            description = _localized_text(_first_present(nested, "description", "notes"))
            dataset_id = str(_first_present(nested, "id", "identifier", "uri") or "").strip()

        if title or dataset_id:
            summaries.append(
                DatasetSummary(
                    title=title or "Untitled dataset",
                    description=description,
                    dataset_id=dataset_id,
                )
            )

        if len(summaries) >= limit:
            break

    return summaries


def _find_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for path in (
        ("result", "items"),
        ("result", "results"),
        ("result", "datasets"),
        ("results",),
        ("items",),
        ("datasets",),
    ):
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    if isinstance(payload.get("result"), list):
        return [item for item in payload["result"] if isinstance(item, dict)]

    return []


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", []):
            return value
    return None


def _is_after_published_date(record: dict[str, Any], published_after: date) -> bool:
    published_on = _published_date(record)
    return published_on is None or published_on > published_after


def _published_date(record: dict[str, Any]) -> date | None:
    raw = _first_present(
        record,
        "issued",
        "releaseDate",
        "release_date",
        "created",
        "createdDate",
        "publicationDate",
        "publication_date",
        "modified",
    )
    if raw is None and isinstance(record.get("dataset"), dict):
        raw = _first_present(
            record["dataset"],
            "issued",
            "releaseDate",
            "release_date",
            "created",
            "createdDate",
            "publicationDate",
            "publication_date",
            "modified",
        )
    return _parse_date(raw)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            parsed = _parse_date(item)
            if parsed:
                return parsed
        return None
    if isinstance(value, dict):
        return _parse_date(_first_present(value, "@value", "value", "en", "de"))
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _localized_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(_localized_text(item) for item in value if item).strip()
    if isinstance(value, dict):
        for key in ("en", "eng", "de", "value", "@value"):
            if key in value:
                return _localized_text(value[key])
        for item in value.values():
            text = _localized_text(item)
            if text:
                return text
    return str(value).strip()
