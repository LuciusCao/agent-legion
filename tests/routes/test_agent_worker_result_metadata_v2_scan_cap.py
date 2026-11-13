"""#1163: v2 metadata scans bound total decompressed work, not member count."""

from __future__ import annotations

import io
import json
import os
import tarfile
import zlib
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
def tarfile_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    total = [0]
    original = tarfile._Stream.read

    def counting_read(stream: tarfile._Stream, size: int | None = None) -> bytes:
        payload = original(stream, size)
        total[0] += len(payload)
        return payload

    monkeypatch.setattr(tarfile._Stream, "read", counting_read)
    return total


@pytest.fixture
def decompressed_outputs(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    total = [0]
    original = zlib.decompressobj

    class CountingDecompressor:
        def __init__(self, wbits: int) -> None:
            self._decompressor = original(wbits)

        @property
        def unconsumed_tail(self) -> bytes:
            return self._decompressor.unconsumed_tail

        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            payload = self._decompressor.decompress(data, max_length)
            total[0] += len(payload)
            return payload

    monkeypatch.setattr(zlib, "decompressobj", CountingDecompressor)
    return total


@_parse_only
def test_large_valid_archive_with_trailing_result_member_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tarfile_reads: list[int]
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
    assert tarfile_reads[0] > 8 * _MIB
    assert tarfile_reads[0] <= 16 * _MIB


@_parse_only
@pytest.mark.parametrize("prefix_sizes", [[8 * _MIB], [512 * 1024] * 8])
def test_high_ratio_archive_rejects_at_cumulative_scan_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tarfile_reads: list[int],
    decompressed_outputs: list[int],
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
    assert tarfile_reads[0] <= 2 * _MIB + tarfile.RECORDSIZE + 1
    assert decompressed_outputs[0] == 2 * _MIB + 1


def _extended_header_archive(kind: str, header_size: int) -> bytes:
    buffer = io.BytesIO()
    archive_format = tarfile.PAX_FORMAT if kind == "pax" else tarfile.GNU_FORMAT
    with tarfile.open(fileobj=buffer, mode="w:gz", format=archive_format) as tar:
        info = tarfile.TarInfo("padding.bin")
        if kind == "pax":
            info.pax_headers = {"comment": "0" * header_size}
        else:
            info.name = "a" * header_size
        tar.addfile(info)
        metadata = _metadata()
        result = tarfile.TarInfo(RESULT_METADATA_MEMBER)
        result.size = len(metadata)
        tar.addfile(result, io.BytesIO(metadata))
    return buffer.getvalue()


@_parse_only
@pytest.mark.parametrize("kind", ["pax", "gnu"])
def test_extended_header_bomb_rejected_before_first_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tarfile_reads: list[int],
    decompressed_outputs: list[int],
    kind: str,
) -> None:
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", _MIB)
    archive = tmp_path / f"{kind}-header-bomb.tar.gz"
    archive.write_bytes(_extended_header_archive(kind, 4 * _MIB))

    assert archive.stat().st_size < 32 * 1024
    with pytest.raises(ValueError, match="scan exceeds decompressed-byte limit"):
        reader.read_archived_result_metadata(archive)
    assert tarfile_reads[0] <= _MIB + tarfile.RECORDSIZE + 1
    assert decompressed_outputs[0] == _MIB + 1


@_parse_only
@pytest.mark.parametrize("kind", ["pax", "gnu"])
def test_valid_extended_headers_with_trailing_result_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", _MIB)
    archive = tmp_path / f"valid-{kind}-header.tar.gz"
    archive.write_bytes(_extended_header_archive(kind, 128))

    assert json.loads(reader.read_archived_result_metadata(archive))["status"] == "completed"


@_parse_only
def test_exact_decompressed_cap_boundary_preserves_valid_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decompressed_outputs: list[int]
) -> None:
    archive = tmp_path / "exact-cap.tar.gz"
    archive.write_bytes(
        _archive([("padding.bin", b"0" * (128 * 1024)), (RESULT_METADATA_MEMBER, _metadata())])
    )
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", _MIB)
    assert json.loads(reader.read_archived_result_metadata(archive))["status"] == "completed"
    observed = decompressed_outputs[0]
    assert observed > 128 * 1024

    decompressed_outputs[:] = [0]
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", observed)
    assert json.loads(reader.read_archived_result_metadata(archive))["status"] == "completed"
    assert decompressed_outputs[0] == observed

    decompressed_outputs[:] = [0]
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", observed - 1)
    with pytest.raises(ValueError, match="scan exceeds decompressed-byte limit"):
        reader.read_archived_result_metadata(archive)
    assert decompressed_outputs[0] == observed


@_parse_only
def test_underlying_fd_closes_on_success_and_scan_cap_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader, "_MAX_RESULT_ARCHIVE_SCAN_BYTES", _MIB)
    closed: list[bool] = []
    original = tarfile._Stream.close

    def recording_close(stream: tarfile._Stream) -> None:
        fd = stream.fileobj.fd
        original(stream)
        with pytest.raises(OSError):
            os.fstat(fd)
        closed.append(stream.closed)

    monkeypatch.setattr(tarfile._Stream, "close", recording_close)
    valid = tmp_path / "valid.tar.gz"
    valid.write_bytes(_archive([(RESULT_METADATA_MEMBER, _metadata())]))
    assert json.loads(reader.read_archived_result_metadata(valid))["status"] == "completed"

    oversized = tmp_path / "oversized.tar.gz"
    oversized.write_bytes(
        _archive([("padding.bin", b"0" * (2 * _MIB)), (RESULT_METADATA_MEMBER, _metadata())])
    )
    with pytest.raises(ValueError, match="scan exceeds decompressed-byte limit"):
        reader.read_archived_result_metadata(oversized)
    assert closed == [True, True]


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
