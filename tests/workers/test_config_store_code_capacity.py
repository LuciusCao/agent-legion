"""worker/config_store.py 的 max_code_concurrency 配置项测试（批次 2 双池）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from worker.config_store import public_config, validate_config
from worker.supervisor import WorkerConfigStore

pytestmark = pytest.mark.no_db


def _config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "host_url": "http://host.test:8000/",
        "worker_id": "worker-1",
        "runtimes": ["pi"],
        "max_concurrency": 1,
    }
    config.update(overrides)
    return config


def test_max_code_concurrency_defaults_to_zero() -> None:
    assert validate_config(_config())["max_code_concurrency"] == 0
    assert public_config(validate_config(_config()))["max_code_concurrency"] == 0


def test_max_code_concurrency_accepts_pool_size() -> None:
    assert validate_config(_config(max_code_concurrency=4))["max_code_concurrency"] == 4


@pytest.mark.parametrize("bad", [-1, 1025, True, 1.5, "4"])
def test_max_code_concurrency_rejects_invalid_values(bad: Any) -> None:
    with pytest.raises(ValueError, match="code 并发数"):
        validate_config(_config(max_code_concurrency=bad))


def test_update_public_allows_code_capacity_edit(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    updated = store.update_public({"max_code_concurrency": 2})
    assert updated["max_code_concurrency"] == 2
    assert store.read()["max_code_concurrency"] == 2
