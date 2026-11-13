from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.host.client import Client
from worker.host.errors import HostResponseError
from worker.transfer_lease_controls import transfer_timeout_cap, validate_transfer_timeout

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
