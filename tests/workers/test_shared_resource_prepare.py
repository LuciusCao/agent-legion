from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from workflow_nodes import shared_resource_prepare

pytestmark = pytest.mark.no_db


def _file(tmp_path: Path, name: str, payload: bytes, content_type: str) -> dict[str, Any]:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": name,
        "local_path": str(path),
        "content_type": content_type,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _runtime(files: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "node_key": "prepare",
        "shared_resources": {
            "rules": {
                "resource_id": "rules",
                "version": 2,
                "manifest_hash": "a" * 64,
                "path": "cache-path-not-for-agents",
                "files": files,
            }
        },
    }


def _document(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / shared_resource_prepare.OUTPUT_NAME).read_text(encoding="utf-8"))


def test_prepare_writes_agent_json_with_utf8_and_base64(tmp_path: Path) -> None:
    text = "规则：先审题。\n".encode()
    binary = b"\x00\xff\x10\x80"
    files = [
        _file(tmp_path, "docs/rules.txt", text, "text/plain; charset=utf-8"),
        _file(tmp_path, "assets/payload.bin", binary, "application/octet-stream"),
    ]

    shared_resource_prepare.run({"id": "job-1"}, tmp_path / "job", _runtime(files))

    raw = (tmp_path / "job" / shared_resource_prepare.OUTPUT_NAME).read_bytes()
    document = json.loads(raw)
    assert document["format"] == "shared-resources-v1"
    resource = document["resources"][0]
    assert resource["resource_id"] == "rules" and resource["version"] == 2
    by_path = {file["path"]: file for file in resource["files"]}
    text_file = by_path["docs/rules.txt"]
    binary_file = by_path["assets/payload.bin"]
    assert text_file["encoding"] == "utf-8"
    assert text_file["content"].encode() == text
    assert binary_file["encoding"] == "base64"
    assert base64.b64decode(binary_file["content"], validate=True) == binary
    assert binary_file["sha256"] == hashlib.sha256(binary).hexdigest()
    assert "cache-path-not-for-agents" not in raw.decode()
    assert "local_path" not in document["resources"][0]["files"][0]


def test_prepare_rejects_non_utf8_text(tmp_path: Path) -> None:
    file = _file(tmp_path, "broken.txt", b"\xff\xfe", "text/plain")
    with pytest.raises(ValueError, match="not valid UTF-8"):
        shared_resource_prepare.run({}, tmp_path / "job", _runtime([file]))
    assert not (tmp_path / "job" / shared_resource_prepare.OUTPUT_NAME).exists()


def test_prepare_rejects_changed_bytes(tmp_path: Path) -> None:
    file = _file(tmp_path, "rules.txt", b"expected", "text/plain")
    Path(file["local_path"]).write_bytes(b"poison")
    with pytest.raises(ValueError, match="size changed"):
        shared_resource_prepare.run({}, tmp_path / "job", _runtime([file]))
    Path(file["local_path"]).write_bytes(b"poisoned")
    with pytest.raises(ValueError, match="hash changed"):
        shared_resource_prepare.run({}, tmp_path / "job", _runtime([file]))


def test_prepare_single_file_limit_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shared_resource_prepare, "MAX_FILE_BYTES", 4)
    file = _file(tmp_path, "large.txt", b"12345", "text/plain")
    with pytest.raises(ValueError, match="4-byte preparation limit"):
        shared_resource_prepare.run({}, tmp_path / "job", _runtime([file]))
    assert not (tmp_path / "job" / shared_resource_prepare.OUTPUT_NAME).exists()


def test_prepare_total_limit_is_checked_before_reading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shared_resource_prepare, "MAX_FILE_BYTES", 4)
    monkeypatch.setattr(shared_resource_prepare, "MAX_TOTAL_BYTES", 5)
    files = [
        _file(tmp_path, "a.txt", b"1234", "text/plain"),
        _file(tmp_path, "b.txt", b"5678", "text/plain"),
    ]

    def fail_read(_file):
        raise AssertionError("resource bytes were read before the total-size preflight")

    monkeypatch.setattr(shared_resource_prepare, "_read_file", fail_read)
    with pytest.raises(ValueError, match="5-byte preparation limit"):
        shared_resource_prepare.run({}, tmp_path / "job", _runtime(files))
    assert not (tmp_path / "job" / shared_resource_prepare.OUTPUT_NAME).exists()


def test_prepare_encoded_document_limit_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shared_resource_prepare, "MAX_DOCUMENT_BYTES", 32)
    file = _file(tmp_path, "payload.bin", b"1234", "application/octet-stream")
    with pytest.raises(ValueError, match="32-byte document limit"):
        shared_resource_prepare.run({}, tmp_path / "job", _runtime([file]))
    assert not (tmp_path / "job" / shared_resource_prepare.OUTPUT_NAME).exists()


def test_prepare_allows_no_declared_resources(tmp_path: Path) -> None:
    shared_resource_prepare.run({}, tmp_path / "job", {"node_key": "prepare"})
    assert _document(tmp_path / "job") == {"format": "shared-resources-v1", "resources": []}
