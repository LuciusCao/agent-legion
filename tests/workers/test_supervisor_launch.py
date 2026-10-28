from __future__ import annotations

import sys
from pathlib import Path

import pytest

from worker.config_store import WorkerConfigStore
from worker.lease_snapshot import SNAPSHOT_ENV_VAR, SNAPSHOT_FILENAME
from worker.service_env import PROXY_ENV_VARS
from worker.status import ENV_VAR, STATUS_FILENAME
from worker.supervisor_launch import executor_launch, mounted_config_diverged

pytestmark = pytest.mark.no_db


def test_executor_launch_builds_argv_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INHERITED_VALUE", "preserved")
    for name in PROXY_ENV_VARS:
        monkeypatch.setenv(name, "http://shell-leftover:1")
    script = tmp_path / "worker.py"
    config = tmp_path / "state" / "worker.yaml"

    launch = executor_launch(script, config, config.parent, "http://gateway:8080")

    assert launch.argv == (sys.executable, str(script), "--config", str(config))
    assert launch.env["INHERITED_VALUE"] == "preserved"
    assert launch.env[ENV_VAR] == str(config.parent / STATUS_FILENAME)
    assert launch.env[SNAPSHOT_ENV_VAR] == str(config.parent / SNAPSHOT_FILENAME)
    assert {name: launch.env[name] for name in PROXY_ENV_VARS} == dict.fromkeys(
        PROXY_ENV_VARS, "http://gateway:8080"
    )


def test_mounted_config_diverged_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap = tmp_path / "bootstrap.yaml"
    store = WorkerConfigStore(tmp_path / "state", bootstrap)
    assert mounted_config_diverged(store) is False

    bootstrap.write_text("claim_enabled: false\n", encoding="utf-8")
    assert mounted_config_diverged(store) is False
    store.path.write_text("claim_enabled: false\n", encoding="utf-8")
    assert mounted_config_diverged(store) is False
    store.path.write_text("claim_enabled: true\n", encoding="utf-8")
    assert mounted_config_diverged(store) is True

    def fail_read(_self: Path) -> bytes:
        raise OSError("injected read failure")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    assert mounted_config_diverged(store) is False
