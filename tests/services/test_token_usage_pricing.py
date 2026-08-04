import pytest

from server.app.services.token_usage_pricing import calculate_cost, load_pricing_config


def _config(pricing_entries: list[dict], currency: str = "CNY") -> dict:
    return {"token_usage": {"currency": currency, "pricing": pricing_entries}}


def _gateway_entry() -> dict:
    return {
        "provider": "gateway",
        "model": "your-model-a",
        "input_per_1m": 3.0,
        "output_per_1m": 15.0,
        "cache_read_per_1m": 0.6,
    }


def test_load_pricing_config_skips_entries_missing_provider_or_model():
    config = _config(
        [
            _gateway_entry(),
            {"model": "no-provider", "input_per_1m": 1.0},
            {"provider": "no-model", "input_per_1m": 1.0},
        ]
    )
    pricing = load_pricing_config(config)
    assert list(pricing) == [("gateway", "your-model-a")]


def test_load_pricing_config_strips_provider_and_model_whitespace():
    entry = {**_gateway_entry(), "provider": "  gateway  ", "model": " your-model-a "}
    pricing = load_pricing_config(_config([entry]))
    assert ("gateway", "your-model-a") in pricing


def test_load_pricing_config_empty_when_section_missing():
    assert load_pricing_config({}) == {}
    assert load_pricing_config({"token_usage": {}}) == {}


def test_calculate_cost_breaks_down_each_component():
    cost = calculate_cost(
        1000000, 500000, 300000, 200000, "gateway", "your-model-a", _config([_gateway_entry()])
    )
    assert cost is not None
    assert cost.input == pytest.approx(1.5)
    assert cost.output == pytest.approx(4.5)
    assert cost.cache_read == pytest.approx(0.12)
    assert cost.total == pytest.approx(1.5 + 4.5 + 0.12)
    assert cost.currency == "CNY"
    assert cost.pricing_missing is False


def test_calculate_cost_strips_provider_and_model_whitespace():
    cost = calculate_cost(0, 0, 0, 0, "  gateway ", " your-model-a ", _config([_gateway_entry()]))
    assert cost is not None
    assert cost.pricing_missing is False


def test_calculate_cost_returns_none_for_unknown_model():
    cost = calculate_cost(100, 50, 30, 20, "unknown", "model", _config([_gateway_entry()]))
    assert cost is None


def test_calculate_cost_zero_usage_is_zero_not_missing():
    cost = calculate_cost(0, 0, 0, 0, "gateway", "your-model-a", _config([_gateway_entry()]))
    assert cost is not None
    assert cost.total == 0.0
    assert cost.pricing_missing is False


def test_calculate_cost_defaults_currency_to_empty_string():
    config = _config([_gateway_entry()])
    del config["token_usage"]["currency"]
    cost = calculate_cost(0, 0, 0, 0, "gateway", "your-model-a", config)
    assert cost is not None
    assert cost.currency == ""
