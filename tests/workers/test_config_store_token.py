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


def test_legacy_single_token_lands_in_token_dir(tmp_path: Path) -> None:
    """兼容通道：单 token 提交写入 register_tokens/ 目录（issue #35）。"""
    store = _store(tmp_path)
    store.update_public({"claim_enabled": True}, registration_token="abc123.secret-part")

    tokens = store.read_registration_tokens()
    assert len(tokens) == 1
    assert tokens[0]["token_id"] == "abc123"
    assert tokens[0]["token"] == "abc123.secret-part"
    assert store.read()["register_token_dir"] == str(store.state_dir / "register_tokens")


def test_upsert_and_remove_scoped_tokens(tmp_path: Path) -> None:
    """添加/移除 scoped token；文件名来自 token id，移除幂等返回 False。"""
    store = _store(tmp_path)
    store.upsert_registration_token("id-aaa.first-secret")
    store.upsert_registration_token("id-bbb.second-secret")

    tokens = store.read_registration_tokens()
    assert [row["token_id"] for row in tokens] == ["id-aaa", "id-bbb"]

    assert store.remove_registration_token("id-aaa") is True
    assert store.remove_registration_token("id-aaa") is False
    assert [row["token_id"] for row in store.read_registration_tokens()] == ["id-bbb"]
    # 路径穿越风格的 token_id 被拒绝。
    assert store.remove_registration_token("../etc") is False


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
