"""#1163: v2 metadata scans bound total decompressed work, not member count."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.agent_broker import result_metadata_reader as reader
from shared.code_contract import RESULT_METADATA_FORMAT_V2, RESULT_METADATA_MEMBER
from tests.helpers.agent_worker_api import claim as _claim
from tests.helpers.agent_worker_api import make_app as _make_app
from tests.helpers.agent_worker_api import register as _register
from tests.helpers.agent_worker_api import seed_request as _seed_request

_MIB = 1024 * 1024
_parse_only = pytest.mark.no_db


def _archive(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _metadata() -> bytes:
    return json.dumps(
        {"status": "completed", "exit_code": 0, "command": [], "output_artifacts": {}}
    ).encode()


@pytest.fixture
def decompressed_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    total = [0]
    original = tarfile._Stream.read

    def counting_read(stream: tarfile._Stream, size: int | None = None) -> bytes:
        payload = original(stream, size)
        total[0] += len(payload)
        return payload

    monkeypatch.setattr(tarfile._Stream, "read", counting_read)
    return total


@_parse_only
def test_large_valid_archive_with_trailing_result_member_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decompressed_reads: list[int]
) -> None:
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", 16 * _MIB)
    archive = tmp_path / "trailing.tar.gz"
    archive.write_bytes(
        _archive(
            [(f"output-{index}.bin", bytes([index]) * (2 * _MIB)) for index in range(4)]
            + [(RESULT_METADATA_MEMBER, _metadata())]
        )
    )

    assert json.loads(reader.read_archived_result_metadata(archive))["status"] == "completed"
    assert decompressed_reads[0] > 8 * _MIB
    assert decompressed_reads[0] <= 16 * _MIB


@_parse_only
@pytest.mark.parametrize("prefix_sizes", [[8 * _MIB], [512 * 1024] * 8])
def test_high_ratio_archive_rejects_at_cumulative_scan_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decompressed_reads: list[int],
    prefix_sizes: list[int],
) -> None:
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", 2 * _MIB)
    archive = tmp_path / "bomb.tar.gz"
    archive.write_bytes(
        _archive(
            [(f"padding-{index}.bin", b"0" * size) for index, size in enumerate(prefix_sizes)]
            + [(RESULT_METADATA_MEMBER, _metadata())]
        )
    )

    assert archive.stat().st_size < 32 * 1024
    with pytest.raises(ValueError, match="scan exceeds decompressed-byte limit"):
        reader.read_archived_result_metadata(archive)
    assert decompressed_reads[0] <= 2 * _MIB + tarfile.RECORDSIZE + 1


@_parse_only
def test_member_cap_remains_independent_from_scan_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", 4 * _MIB)
    archive = tmp_path / "oversized-result.tar.gz"
    archive.write_bytes(_archive([(RESULT_METADATA_MEMBER, b"x" * (_MIB + 1))]))

    with pytest.raises(ValueError, match="metadata member is too large"):
        reader.read_archived_result_metadata(archive)


@pytest.mark.postgres
def test_scan_cap_is_fail_closed_400_and_claim_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", _MIB)
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-scan-cap", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        headers = {
            "X-Agent-Worker-Token": token,
            "X-Agent-Lease-Id": claimed["lease_id"],
            "X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2,
        }
        response = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers=headers,
            content=_archive(
                [("padding.bin", b"0" * (4 * _MIB)), (RESULT_METADATA_MEMBER, _metadata())]
            ),
        )
        assert response.status_code == 400, response.text
        assert "scan exceeds decompressed-byte limit" in response.json()["detail"]
        bundle_dir = Path(app.state.agent_broker.bundle_dir)
        assert not list(bundle_dir.glob(".result-*"))
        assert not list(bundle_dir.glob("*.result.tar.gz"))

        retry = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers=headers,
            content=_archive([(RESULT_METADATA_MEMBER, _metadata())]),
        )
        assert retry.status_code == 204, retry.text
