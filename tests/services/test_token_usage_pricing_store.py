from __future__ import annotations

import pytest

from server.app.services.token_usage_pricing_store import TokenUsagePricingStore


@pytest.fixture
def store(job_db) -> TokenUsagePricingStore:
    store = TokenUsagePricingStore(job_db.path)
    with job_db.connect() as conn:
        conn.execute("delete from global_settings where key='token_usage'")
    return store


def _document() -> dict:
    return {
        "currency": "USD",
        "pricing": [
            {
                "provider": "gateway",
                "model": "model-x",
                "input_per_1m": 1.0,
                "output_per_1m": 2.0,
                "cache_read_per_1m": 0.5,
            }
        ],
    }


def test_get_returns_none_when_unset(store) -> None:
    assert store.get() is None


def test_put_get_roundtrip(store) -> None:
    store.put(_document())
    assert store.get() == _document()


def test_put_overwrites_existing_document(store) -> None:
    store.put(_document())
    updated = _document()
    updated["currency"] = "EUR"
    store.put(updated)
    assert store.get()["currency"] == "EUR"


def test_effective_config_drops_token_usage_when_unset(store) -> None:
    base = {"token_usage": {"currency": "CNY", "pricing": []}, "other": 1}
    config = store.effective_config(base)
    assert "token_usage" not in config
    assert config["other"] == 1


def test_effective_config_uses_database_document(store) -> None:
    store.put(_document())
    base = {"token_usage": {"currency": "CNY", "pricing": []}, "other": 1}
    config = store.effective_config(base)
    assert config["token_usage"] == _document()
    assert config["other"] == 1
    # The base config object must not be mutated.
    assert base["token_usage"]["currency"] == "CNY"
