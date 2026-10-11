"""worker 侧 node_concurrency_limits 配置项测试（#1158 Worker 节点级并发上限）。

钉住三个面：validate_config/public_config 的校验与默认值（机器资源保护层，
空 = 不限制）、runtime.controls 的热读 loader（启动预检 fail-fast / 热更
保留旧值的同一契约）、WorkerConfigStore.update_public 的控制台写入通道。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shared.concurrency_limits import MAX_DYNAMIC_CONCURRENCY
from worker.config_store import public_config, validate_config
from worker.runtime.controls import load_node_concurrency_limits
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


def test_node_concurrency_limits_defaults_to_empty() -> None:
    # 未配置 = {} = 不限制（与现状完全一致，零回归面）。
    assert validate_config(_config())["node_concurrency_limits"] == {}
    assert public_config(validate_config(_config()))["node_concurrency_limits"] == {}


def test_node_concurrency_limits_accepts_positive_int_map() -> None:
    config = validate_config(_config(node_concurrency_limits={"heavy_transcode": 1, "review": 4}))
    assert config["node_concurrency_limits"] == {"heavy_transcode": 1, "review": 4}


@pytest.mark.parametrize(
    "bad",
    [
        {"node": 0},
        {"node": -1},
        {"node": True},
        {"node": 1.5},
        {"node": "4"},
        {"node": MAX_DYNAMIC_CONCURRENCY + 1},  # #657：越界 = ceiling+1
        {"": 1},  # 空 key
        [("node", 1)],  # 非对象
    ],
)
def test_node_concurrency_limits_rejects_invalid_values(bad: Any) -> None:
    with pytest.raises(ValueError, match="节点"):
        validate_config(_config(node_concurrency_limits=bad))


def test_update_public_allows_node_limits_edit(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    updated = store.update_public({"node_concurrency_limits": {"heavy_transcode": 1}})
    assert updated["node_concurrency_limits"] == {"heavy_transcode": 1}
    assert store.read()["node_concurrency_limits"] == {"heavy_transcode": 1}
    # 显式空 map = 清空（无限制），与「不声明 = 保留」的 Host 侧语义对应。
    cleared = store.update_public({"node_concurrency_limits": {}})
    assert cleared["node_concurrency_limits"] == {}
    assert store.read()["node_concurrency_limits"] == {}


def _write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "worker.yaml"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_load_node_concurrency_limits_defaults_to_empty(tmp_path: Path) -> None:
    assert load_node_concurrency_limits(_write_config(tmp_path, {})) == {}


def test_load_node_concurrency_limits_reads_map(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"node_concurrency_limits": {"heavy_transcode": 2}})
    assert load_node_concurrency_limits(path) == {"heavy_transcode": 2}


def test_load_node_concurrency_limits_invalid_raises(tmp_path: Path) -> None:
    # 与 load_claim_controls 同契约：非法值 ValueError——启动预检 fail-fast、
    # 热更（reload_controls）保留旧值。
    path = _write_config(tmp_path, {"node_concurrency_limits": {"heavy_transcode": 0}})
    with pytest.raises(ValueError, match="节点"):
        load_node_concurrency_limits(path)
