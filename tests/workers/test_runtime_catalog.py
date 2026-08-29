"""Agent runtime 目录探测与声明推导（worker/runtime/catalog.py，issue #254）。

语义：声明 = 本机探测到的已安装 runtime − disabled_runtimes 反选停用；
探测即默认启用，不再需要手工勾选。这里同时覆盖 config_store 的旧版
opt-in `runtimes` 键一次性迁移。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from worker import binary_resolution, config_store
from worker.config_store import validate_config
from worker.runtime import catalog
from worker.runtime.catalog import (
    RUNTIME_CATALOG,
    SUPPORTED_RUNTIMES,
    detect_installed_runtimes,
    effective_runtimes,
    runtime_status,
)


@pytest.fixture(autouse=True)
def _isolated_bundled_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把自带二进制目录指向不存在的位置，避免开发机 data/bin 污染测试。"""
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bin")


def _only(*installed: str):
    return lambda binary: f"/usr/local/bin/{binary}" if binary in installed else None


def _none(_binary: str) -> None:
    return None


@pytest.mark.no_db
def test_supported_runtimes_match_catalog() -> None:
    assert SUPPORTED_RUNTIMES == ("velites", "pi", "openclaw")
    assert set(RUNTIME_CATALOG) == set(SUPPORTED_RUNTIMES)


@pytest.mark.no_db
def test_detect_installed_runtimes_reports_resolved_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _only("velites", "pi"))
    assert detect_installed_runtimes() == {
        "velites": "/usr/local/bin/velites",
        "pi": "/usr/local/bin/pi",
    }


@pytest.mark.no_db
def test_effective_runtimes_defaults_to_all_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _only("velites", "pi"))
    assert effective_runtimes([]) == ["pi", "velites"]


@pytest.mark.no_db
def test_effective_runtimes_subtracts_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _only("velites", "pi"))
    assert effective_runtimes(["pi"]) == ["velites"]
    # 全部停用 / 什么都没装都是合法的空集合（code-only 或暂不接 agent）。
    assert effective_runtimes(["pi", "velites"]) == []
    monkeypatch.setattr(shutil, "which", _none)
    assert effective_runtimes([]) == []


@pytest.mark.no_db
def test_runtime_status_covers_whole_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _only("velites"))
    rows = {row["runtime"]: row for row in runtime_status(["pi"])}
    assert rows["velites"]["installed"] is True
    assert rows["velites"]["enabled"] is True
    assert rows["velites"]["binary"] == "/usr/local/bin/velites"
    # pi 未安装：installed=False，开关无从谈起（enabled 恒 False）。
    assert rows["pi"]["installed"] is False
    assert rows["pi"]["enabled"] is False
    assert rows["pi"]["install_hint"]


def _base_config(**overrides):
    config = {
        "host_url": "http://host:8000",
        "worker_id": "w1",
        "max_concurrency": 1,
    }
    config.update(overrides)
    return config


@pytest.mark.no_db
def test_validate_config_derives_runtimes_from_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _only("velites", "pi"))
    config = validate_config(_base_config(disabled_runtimes=[]))
    assert config["runtimes"] == ["pi", "velites"]
    assert config["disabled_runtimes"] == []
    config = validate_config(_base_config(disabled_runtimes=["pi"]))
    assert config["runtimes"] == ["velites"]


@pytest.mark.no_db
def test_validate_config_allows_empty_effective_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _none)
    config = validate_config(_base_config(disabled_runtimes=[]))
    assert config["runtimes"] == []


@pytest.mark.no_db
def test_validate_config_rejects_unknown_disabled_runtime() -> None:
    with pytest.raises(ValueError, match="disabled_runtimes"):
        validate_config(_base_config(disabled_runtimes=["shell"]))


@pytest.mark.no_db
def test_legacy_runtimes_key_migrates_to_disabled_complement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """旧版 opt-in 勾选 → 补集停用，升级后 claim 行为保持；落盘只留新键。"""
    monkeypatch.setattr(shutil, "which", _only("velites", "pi"))
    store = config_store.WorkerConfigStore(tmp_path)
    store.write(validate_config(_base_config(runtimes=["velites"])))

    persisted = (tmp_path / "worker.yaml").read_text(encoding="utf-8")
    assert "disabled_runtimes" in persisted
    assert "runtimes:" not in persisted.replace("disabled_runtimes:", "")

    config = store.read(require_identity=False)
    assert config["disabled_runtimes"] == ["openclaw", "pi"]
    assert config["runtimes"] == ["velites"]


@pytest.mark.no_db
def test_disabled_runtimes_takes_precedence_over_legacy_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _only("velites", "pi"))
    config = validate_config(
        _base_config(disabled_runtimes=["velites"], runtimes=["pi", "velites"])
    )
    assert config["disabled_runtimes"] == ["velites"]
    assert config["runtimes"] == ["pi"]


@pytest.mark.no_db
def test_models_allowlist_accepts_supported_runtime_regardless_of_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # models 的 runtime 取值校验对齐支持全集：pi 未安装也不拒绝声明
    # （发现阶段按生效集合取交集，机器装好 pi 后自动生效）。
    monkeypatch.setattr(shutil, "which", _only("velites"))
    config = validate_config(
        _base_config(
            disabled_runtimes=[],
            models=[{"runtime": "pi", "provider": "openai", "model": "gpt-5.2"}],
        )
    )
    assert config["models"] == [{"runtime": "pi", "provider": "openai", "model": "gpt-5.2"}]


@pytest.mark.no_db
def test_detection_uses_catalog_resolve_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """config_store 与 catalog 共用同一个探测入口（patch 一点即全局生效）。"""
    monkeypatch.setattr(catalog, "resolve_binary", _only("velites"))
    config = validate_config(_base_config(disabled_runtimes=[]))
    assert config["runtimes"] == ["velites"]
