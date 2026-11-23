from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.execution.ownership import OWNER_FILENAME, write_owner_marker
from worker.host.client import Client
from worker.host.errors import HostResponseError
from worker.host.transfer_lease import (
    apply_lease_ttl,
    restore_uploads_with_lease_clamp,
    sync_transfer_heartbeat,
    update_heartbeat_interval,
)
from worker.transfer_controls import TransferControls, load_transfer_controls
from worker.transfer_lease_controls import transfer_timeout_cap, validate_transfer_timeout
from worker.upload.constants import PENDING_FILENAME

pytestmark = pytest.mark.no_db


def _claim(execution_id: str, ttl: object = None) -> dict:
    claim = {
        "execution_id": execution_id,
        "node_key": "node-a",
        "lease_id": f"lease-{execution_id}",
    }
    if ttl is not None:
        claim["lease_ttl_seconds"] = ttl
    return claim


def _answer(claims: list[dict]):
    return 200, json.dumps({"claims": claims}).encode()


def _pending_upload(work_root: Path, execution_id: str, ttl: object = None) -> None:
    execution_dir = work_root / execution_id
    execution_dir.mkdir(parents=True)
    (execution_dir / PENDING_FILENAME).write_text("{}", encoding="utf-8")
    claim: dict[str, object] = {"execution_id": execution_id, "lease_id": f"lease-{execution_id}"}
    if ttl is not None:
        claim["lease_ttl_seconds"] = ttl
    write_owner_marker(execution_dir, claim)


class _RestoreRecorder:
    def __init__(self, client: Client, restored: int = 1) -> None:
        self.client = client
        self.restored = restored
        self.timeouts: list[float] = []

    def restore(self, _work_root: Path) -> int:
        self.timeouts.append(self.client.transfer_timeout)
        return self.restored


def test_claim_ttl_clamps_the_timeout_used_by_the_real_report_call(tmp_path: Path) -> None:
    client = Client("http://unused", transfer_timeout=180, heartbeat_interval=15)
    client.request = lambda *args, **kwargs: _answer([_claim("exec-1", 90)])  # type: ignore[method-assign]

    client.claim_batch("worker-1", limit=1, agent_limit=1, code_limit=1)

    assert client.configured_transfer_timeout == 180
    assert client.transfer_timeout == 165
    archive = tmp_path / "result.tar.gz"
    archive.write_bytes(b"archive")
    seen: list[float] = []

    def report_request(*args, **kwargs):
        seen.append(kwargs["timeout"])
        return 204, b""

    client.request = report_request  # type: ignore[method-assign]
    assert client.report("exec-1", "lease-exec-1", archive) == (204, b"")
    assert seen == [165]


def test_short_ttl_and_batch_minimum_keep_the_cross_clamp() -> None:
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    client.request = lambda *args, **kwargs: _answer(  # type: ignore[method-assign]
        [_claim("exec-1", 90), _claim("exec-2", 30)]
    )

    client.claim_batch("worker-1", limit=2, agent_limit=2, code_limit=0)

    assert client.transfer_timeout == 45
    assert client.configured_transfer_timeout == 120
    assert client.transfer_lease_ttl == 30


def test_old_host_without_ttl_keeps_legacy_timeout() -> None:
    client = Client("http://unused", transfer_timeout=120)
    client.request = lambda *args, **kwargs: _answer([_claim("exec-1")])  # type: ignore[method-assign]

    client.claim_batch("worker-1", limit=1, agent_limit=1, code_limit=1)

    assert client.transfer_timeout == 120
    assert client.transfer_lease_ttl is None


def test_invalid_ttl_rejects_the_whole_batch_without_changing_timeout() -> None:
    client = Client("http://unused", transfer_timeout=123)
    client.request = lambda *args, **kwargs: _answer(  # type: ignore[method-assign]
        [_claim("exec-1", 30), _claim("exec-2", "invalid")]
    )

    with pytest.raises(HostResponseError, match="invalid lease_ttl_seconds"):
        client.claim_batch("worker-1", limit=2, agent_limit=2, code_limit=0)

    assert client.transfer_timeout == 123
    assert client.transfer_lease_ttl is None


@pytest.mark.parametrize(
    ("ttl", "heartbeat", "expected"),
    [(90, 15, 165), (30, 15, 45), (1, 15, 1), (45, 30, 60)],
)
def test_transfer_timeout_cap_boundaries(ttl: float, heartbeat: float, expected: float) -> None:
    assert transfer_timeout_cap(ttl, heartbeat) == expected


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "bad"])
def test_invalid_transfer_numbers_are_diagnostic(value: object) -> None:
    with pytest.raises(ValueError):
        validate_transfer_timeout(value)
    with pytest.raises(ValueError):
        transfer_timeout_cap(value, 15)


def test_short_ttl_clamp_is_logged_once(capsys: pytest.CaptureFixture[str]) -> None:
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    client.request = lambda *args, **kwargs: _answer([_claim("exec-1", 1)])  # type: ignore[method-assign]

    client.claim_batch("worker-1", limit=1, agent_limit=1, code_limit=1)
    client.claim_batch("worker-1", limit=1, agent_limit=1, code_limit=1)

    output = capsys.readouterr().out
    assert "leaves no safe transfer window" in output
    assert output.count("clamping transfer_timeout_seconds") == 1


def test_relay_heartbeat_hot_update_reclamps_current_transfer_timeout(tmp_path: Path) -> None:
    config = tmp_path / "worker.yaml"
    config.write_text("transfer_timeout_seconds: 120\nheartbeat_interval_seconds: 15\n")
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    apply_lease_ttl(client, 30)
    assert client.transfer_timeout == 45
    initial = load_transfer_controls(config)
    assert initial.heartbeat_interval_seconds == 15

    config.write_text("transfer_timeout_seconds: 120\nheartbeat_interval_seconds: 45\n")
    updated = load_transfer_controls(config)
    assert updated.heartbeat_interval_seconds == 45
    sync_transfer_heartbeat(client, updated, relay=False)
    assert client.heartbeat_interval == 15
    assert client.transfer_timeout == 45
    sync_transfer_heartbeat(client, updated, relay=True)

    assert client.heartbeat_interval == 45
    assert client.transfer_timeout == 15
    update_heartbeat_interval(client, 15)
    assert client.transfer_timeout == 15  # 在途值只能收紧，不能随周期回落放宽


def test_larger_ttl_does_not_relax_in_flight_claims(capsys: pytest.CaptureFixture[str]) -> None:
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    apply_lease_ttl(client, 30)
    assert client.transfer_timeout == 45

    apply_lease_ttl(client, 90)

    assert client.transfer_timeout == 45
    output = capsys.readouterr().out
    assert "constrained to 45s" in output
    assert "current claim cap 165s" in output


def test_restore_applies_minimum_pending_ttl_before_uploads_restart(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _pending_upload(work_root, "exec-long", 90)
    _pending_upload(work_root, "exec-short", 30)
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    uploads = _RestoreRecorder(client, restored=2)

    assert restore_uploads_with_lease_clamp(client, uploads, work_root) == 2

    assert uploads.timeouts == [45]
    assert client.transfer_lease_ttl == 30


@pytest.mark.parametrize(("relay", "expected"), [(False, 45), (True, 15)])
def test_restore_clamp_uses_bare_or_relay_heartbeat(
    tmp_path: Path, relay: bool, expected: float
) -> None:
    work_root = tmp_path / "work"
    _pending_upload(work_root, "exec-1", 30)
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    sync_transfer_heartbeat(client, TransferControls(heartbeat_interval_seconds=45), relay=relay)
    uploads = _RestoreRecorder(client)

    assert restore_uploads_with_lease_clamp(client, uploads, work_root) == 1

    assert uploads.timeouts == [expected]


def test_restore_without_ttl_keeps_legacy_mixed_fleet_timeout(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _pending_upload(work_root, "exec-legacy")
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    uploads = _RestoreRecorder(client)

    assert restore_uploads_with_lease_clamp(client, uploads, work_root) == 1

    assert uploads.timeouts == [120]
    assert client.transfer_lease_ttl is None


def test_restore_without_owner_keeps_legacy_timeout(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    execution_dir = work_root / "exec-legacy"
    execution_dir.mkdir(parents=True)
    (execution_dir / PENDING_FILENAME).write_text("{}", encoding="utf-8")
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    uploads = _RestoreRecorder(client)

    assert restore_uploads_with_lease_clamp(client, uploads, work_root) == 1

    assert uploads.timeouts == [120]
    assert client.transfer_lease_ttl is None


@pytest.mark.parametrize("contents", ["not json", "[]"])
def test_restore_invalid_owner_fails_closed_before_uploads_restart(
    tmp_path: Path, contents: str
) -> None:
    work_root = tmp_path / "work"
    _pending_upload(work_root, "exec-1", 30)
    (work_root / "exec-1" / OWNER_FILENAME).write_text(contents, encoding="utf-8")
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    uploads = _RestoreRecorder(client)

    with pytest.raises(ValueError, match="invalid restored owner marker"):
        restore_uploads_with_lease_clamp(client, uploads, work_root)

    assert uploads.timeouts == []


def test_restore_owner_read_error_fails_closed_before_uploads_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "work"
    _pending_upload(work_root, "exec-1", 30)
    owner = work_root / "exec-1" / OWNER_FILENAME
    real_read_text = Path.read_text

    def failing_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == owner:
            raise PermissionError(13, "Permission denied", str(owner))
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    uploads = _RestoreRecorder(client)

    with pytest.raises(ValueError, match="invalid restored owner marker"):
        restore_uploads_with_lease_clamp(client, uploads, work_root)

    assert uploads.timeouts == []


def test_restore_dangling_owner_symlink_fails_closed_before_uploads_restart(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "work"
    _pending_upload(work_root, "exec-1", 30)
    owner = work_root / "exec-1" / OWNER_FILENAME
    owner.unlink()
    owner.symlink_to(tmp_path / "missing-owner-target")
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    uploads = _RestoreRecorder(client)

    with pytest.raises(ValueError, match="invalid restored owner marker"):
        restore_uploads_with_lease_clamp(client, uploads, work_root)

    assert uploads.timeouts == []


def test_restore_owner_file_not_found_fails_closed_before_uploads_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "work"
    _pending_upload(work_root, "exec-1", 30)
    owner = work_root / "exec-1" / OWNER_FILENAME
    real_read_text = Path.read_text

    def failing_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == owner:
            raise FileNotFoundError(2, "No such file or directory", str(owner))
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)
    client = Client("http://unused", transfer_timeout=120, heartbeat_interval=15)
    uploads = _RestoreRecorder(client)

    with pytest.raises(ValueError, match="invalid restored owner marker"):
        restore_uploads_with_lease_clamp(client, uploads, work_root)

    assert uploads.timeouts == []
