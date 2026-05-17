from datetime import date

from src.regulatory.europa import parse_dataset_response, search_regulatory_datasets


def test_parse_nested_europa_response():
    payload = {
        "result": {
            "items": [
                {
                    "id": "dataset-1",
                    "title": {"en": "Digital Services Act datasets"},
                    "description": {"en": "Regulatory data for digital services."},
                },
                {
                    "identifier": "dataset-2",
                    "title": {"de": "Datenschutz"},
                    "description": ["Data protection catalogue"],
                },
            ]
        }
    }

    rows = parse_dataset_response(payload, limit=5)

    assert len(rows) == 2
    assert rows[0].title == "Digital Services Act datasets"
    assert rows[0].description == "Regulatory data for digital services."
    assert rows[0].dataset_id == "dataset-1"
    assert rows[1].title == "Datenschutz"
    assert rows[1].dataset_id == "dataset-2"


def test_parse_filters_by_published_date():
    payload = {
        "result": {
            "items": [
                {
                    "id": "old",
                    "title": {"en": "Old dataset"},
                    "issued": "2025-01-01",
                },
                {
                    "id": "new",
                    "title": {"en": "New dataset"},
                    "issued": {"@value": "2026-05-01T00:00:00Z"},
                },
            ]
        }
    }

    rows = parse_dataset_response(payload, limit=5, published_after=date(2026, 4, 30))

    assert [row.dataset_id for row in rows] == ["new"]


def test_search_uses_supported_dataset_filter(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"items": []}}

    class FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, *, params):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr("src.regulatory.europa.httpx.Client", FakeClient)

    rows = search_regulatory_datasets(
        query="Digital Services OR Data Protection",
        published_after=date(2026, 4, 30),
        limit=5,
    )

    assert rows == []
    assert captured["params"]["q"] == "Digital Services OR Data Protection"
    assert captured["params"]["filter"] == "dataset"
    assert "query" not in captured["params"]
    assert "issued gt" not in str(captured["params"])


def test_regulatory_sync_persists_results(monkeypatch):
    from src.regulatory import DatasetSummary
    from src.regulatory.sync import sync_regulatory_datasets

    captured = {}

    def fake_active_contract_versions_for_regulatory_sync():
        from datetime import date

        return [{"contract_id": "contract-version-1", "effective_date": date(2026, 4, 30)}]

    def fake_search_regulatory_datasets(*, query, published_after, limit):
        captured["query"] = query
        captured["published_after"] = published_after
        captured["limit"] = limit
        return [
            DatasetSummary(
                title="Digital Services",
                description="Regulatory dataset",
                dataset_id="dataset-1",
            )
        ]

    def fake_save_regulatory_datasets(contract_id, datasets, *, query, published_after):
        captured["contract_id"] = contract_id
        captured["datasets"] = datasets
        captured["saved_query"] = query
        captured["saved_published_after"] = published_after
        return len(datasets)

    monkeypatch.setattr(
        "src.regulatory.sync.active_contract_versions_for_regulatory_sync",
        fake_active_contract_versions_for_regulatory_sync,
    )
    monkeypatch.setattr(
        "src.regulatory.sync.search_regulatory_datasets",
        fake_search_regulatory_datasets,
    )
    monkeypatch.setattr(
        "src.regulatory.sync.save_regulatory_datasets",
        fake_save_regulatory_datasets,
    )

    saved_count = sync_regulatory_datasets()

    assert saved_count == 1
    assert captured["contract_id"] == "contract-version-1"
    assert captured["published_after"].isoformat() == "2026-04-30"
    assert captured["limit"] == 5
