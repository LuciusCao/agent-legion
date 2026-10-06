"""`capabilities` 配置键已移除（issue #452；#284 起即 no-op）。

存量 worker.yaml 残留该键时 Worker 仍须正常启动：读取时剥离、每进程告警一次、
下次落盘即清除；控制面写入该键则按未知配置项拒绝。直接 `executor.py --config`
加载路径（runtime.controls.load_config）共用同一剥离与告警（#1023）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from worker.config_store import WorkerConfigStore, validate_config
from worker.runtime import controls as runtime_controls

pytestmark = pytest.mark.no_db

_LOGGER = "worker.runtime.controls"


@pytest.fixture(autouse=True)
def _reset_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_controls, "_warned_removed", set())


def _base_config(**overrides):
    config = {
        "host_url": "http://host:8000",
        "worker_id": "w1",
        "max_concurrency": 1,
    }
    config.update(overrides)
    return config


@pytest.mark.parametrize("legacy", [["*", "review"], [], "generate", None])
def test_legacy_capabilities_key_is_stripped_not_rejected(legacy, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        config = validate_config(_base_config(capabilities=legacy))

    assert "capabilities" not in config
    assert any("#452" in record.getMessage() for record in caplog.records)


def test_removed_key_warns_once_per_process(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        validate_config(_base_config(capabilities=["review"]))
        validate_config(_base_config(capabilities=["review"]))
        validate_config(_base_config())

    assert len(caplog.records) == 1


def test_config_without_legacy_key_stays_silent(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert "capabilities" not in validate_config(_base_config())

    assert not caplog.records


def test_state_file_with_legacy_key_loads_and_next_write_drops_it(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state = state_dir / "worker.yaml"
    state.write_text(yaml.safe_dump(_base_config(capabilities=["review"])), encoding="utf-8")
    store = WorkerConfigStore(state_dir)

    assert store.read()["worker_id"] == "w1"
    store.update_public({"max_concurrency": 2})

    persisted = yaml.safe_load(state.read_text(encoding="utf-8"))
    assert persisted["max_concurrency"] == 2
    assert "capabilities" not in persisted


def test_bootstrap_with_legacy_key_still_imports(tmp_path: Path) -> None:
    bootstrap = tmp_path / "worker.yaml"
    bootstrap.write_text(yaml.safe_dump(_base_config(capabilities=["*"])), encoding="utf-8")

    store = WorkerConfigStore(tmp_path / "state", bootstrap)

    assert store.bootstrap_error is None
    assert store.read()["worker_id"] == "w1"
    assert "capabilities" not in yaml.safe_load(store.path.read_text(encoding="utf-8"))


def test_control_plane_update_rejects_removed_key(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_base_config()))

    with pytest.raises(ValueError, match="capabilities"):
        store.update_public({"capabilities": ["review"]})


def _write_yaml(path: Path, config: dict) -> Path:
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_direct_executor_config_load_strips_legacy_key_and_warns_once(
    tmp_path: Path, caplog
) -> None:
    """#1023：直接 `executor.py --config` 读取路径不经 validate_config——
    executor 每个 pass 热读 load_config，旧键须同样剥离且整进程只告警一次。"""
    path = _write_yaml(tmp_path / "worker.yaml", _base_config(capabilities=["review"]))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        first = runtime_controls.load_config(path)
        second = runtime_controls.load_config(path)
        assert runtime_controls.load_claim_controls(path) == (1, False, None)

    assert "capabilities" not in first and "capabilities" not in second
    assert first["worker_id"] == "w1"
    assert len(caplog.records) == 1
    assert "#452" in caplog.records[0].getMessage()


def test_direct_and_store_paths_share_one_warning(tmp_path: Path, caplog) -> None:
    """两条读取路径复用同一 _REMOVED_KEYS 与去重集合：同进程先后命中不重复告警。"""
    path = _write_yaml(tmp_path / "worker.yaml", _base_config(capabilities=["*"]))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        runtime_controls.load_config(path)
        validate_config(_base_config(capabilities=["*"]))

    assert len(caplog.records) == 1


def test_direct_executor_config_without_legacy_key_stays_silent(tmp_path: Path, caplog) -> None:
    path = _write_yaml(tmp_path / "worker.yaml", _base_config())

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert runtime_controls.load_config(path) == _base_config()

    assert not caplog.records
