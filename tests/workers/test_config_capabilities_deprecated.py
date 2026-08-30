"""capabilities 键退役（issue #284）：deprecated no-op。

claim 准入不再按 capability 匹配；worker yaml 的 `capabilities:` 键保留为
兼容通道——任意内容（含 "*"）都接受、非空时打 deprecated warning、不再
上报 Host。
"""

from __future__ import annotations

import logging

import pytest

from worker.config_store import validate_config


def _base_config(**overrides):
    config = {
        "host_url": "http://host:8000",
        "worker_id": "w1",
        "max_concurrency": 1,
    }
    config.update(overrides)
    return config


@pytest.mark.no_db
def test_capabilities_wildcard_and_arbitrary_values_accepted(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="worker.config_validation"):
        config = validate_config(_base_config(capabilities=["*", "anything", ""]))

    assert config["capabilities"] == ["*", "anything"]
    assert any("deprecated" in record.getMessage() for record in caplog.records)


@pytest.mark.no_db
def test_absent_or_empty_capabilities_stays_silent(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="worker.config_validation"):
        assert validate_config(_base_config())["capabilities"] == []
        assert validate_config(_base_config(capabilities=[]))["capabilities"] == []

    assert not caplog.records


@pytest.mark.no_db
def test_capabilities_non_list_still_rejected() -> None:
    with pytest.raises(ValueError, match="capabilities"):
        validate_config(_base_config(capabilities="generate"))
