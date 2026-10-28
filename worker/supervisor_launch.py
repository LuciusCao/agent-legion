"""Executor launch specification and config preflight for the worker supervisor."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from worker.config_store import WorkerConfigStore
from worker.lease_snapshot import SNAPSHOT_ENV_VAR, SNAPSHOT_FILENAME
from worker.service_env import proxy_env_overrides
from worker.status import ENV_VAR, STATUS_FILENAME


@dataclass(frozen=True)
class ExecutorLaunch:
    argv: tuple[str, ...]
    env: dict[str, str]


def executor_launch(
    worker_script: Path, config_path: Path, state_dir: Path, proxy: object
) -> ExecutorLaunch:
    return ExecutorLaunch(
        argv=(sys.executable, str(worker_script), "--config", str(config_path)),
        env={
            **os.environ,
            **proxy_env_overrides(proxy),
            ENV_VAR: str(state_dir / STATUS_FILENAME),
            SNAPSHOT_ENV_VAR: str(state_dir / SNAPSHOT_FILENAME),
        },
    )


def mounted_config_diverged(store: WorkerConfigStore) -> bool:
    bootstrap = store.bootstrap_path
    if bootstrap is None or not bootstrap.is_file() or not store.path.is_file():
        return False
    try:
        mounted_hash = hashlib.sha256(bootstrap.read_bytes()).digest()
        current_hash = hashlib.sha256(store.path.read_bytes()).digest()
    except OSError:
        return False
    return mounted_hash != current_hash
