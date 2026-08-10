"""Token/yaml write-ordering tests for worker/config_store.py.

yaml is written before the token file: a crash between the two leaves a
visible "referenced token is missing" state (recoverable via re-configure)
instead of an orphan token file no config references. A failed yaml write
must leave no token behind at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from worker.supervisor import WorkerConfigStore, validate_config

pytestmark = pytest.mark.no_db


def _config() -> dict[str, Any]:
    return {
        "host_url": "http://host.test:8000/",
        "worker_id": "worker-1",
        "runtimes": ["pi"],
        "max_concurrency": 1,
    }


def _store(tmp_path: Path) -> WorkerConfigStore:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    return store


def test_yaml_written_before_token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    seen: dict[str, bool] = {}
    original_write = store.write

    def spy_write(config: dict[str, Any]) -> None:
        seen["token_existed_at_yaml_write"] = (store.state_dir / "register_token").exists()
        original_write(config)

    monkeypatch.setattr(store, "write", spy_write)
    store.update_public({"claim_enabled": True}, registration_token="token-x")

    assert seen["token_existed_at_yaml_write"] is False
    token_path = store.state_dir / "register_token"
    assert token_path.read_text(encoding="utf-8").strip() == "token-x"
    assert store.read()["register_token_file"] == str(token_path)


def test_failed_yaml_write_leaves_no_orphan_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)

    def boom(_config: dict[str, Any]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "write", boom)
    with pytest.raises(OSError, match="disk full"):
        store.update_public({"claim_enabled": True}, registration_token="token-x")

    assert not (store.state_dir / "register_token").exists()
