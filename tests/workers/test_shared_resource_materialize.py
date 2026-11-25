from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from server.app.workflows.shared_resource_runtime import materialize_resource_pins
from shared.material_cache import MaterializeError, cache_file_path
from shared.resource_manifest import SHARED_RESOURCE_CLAIM_FORMAT, resource_manifest_hash
from tests.fakes.storage import FakeObjectStorage
from worker.resource_fetch import materialize_claim_shared_resources

pytestmark = pytest.mark.no_db


def _manifest(resources: dict[str, list[tuple[str, bytes, str]]]) -> dict[str, Any]:
    pins = []
    for resource_id, source_files in resources.items():
        files = [
            {
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "content_type": content_type,
                "download_url": f"https://objects.test/{resource_id}/{path}",
            }
            for path, payload, content_type in source_files
        ]
        pins.append(
            {
                "resource_id": resource_id,
                "version": 4,
                "manifest_hash": resource_manifest_hash(files),
                "files": files,
            }
        )
    return {
        "node_key": "prepare",
        "shared_resources": list(resources),
        "runtime_context": {
            "job": {
                "id": "job-1",
                "workflow_definition_snapshot_json": json.dumps(
                    {
                        "nodes": {
                            "prepare": {"shared_resources": list(resources)},
                        }
                    }
                ),
            },
            "shared_resources": {
                "format": SHARED_RESOURCE_CLAIM_FORMAT,
                "pins": pins,
            },
        },
    }


def _objects(manifest: dict[str, Any]) -> dict[str, bytes]:
    result = {}
    for pin in manifest["runtime_context"]["shared_resources"]["pins"]:
        for file in pin["files"]:
            result[file["download_url"]] = file["expected_payload"]
    return result


def _with_payloads(manifest: dict[str, Any], resources: dict[str, list[tuple[str, bytes, str]]]):
    for pin in manifest["runtime_context"]["shared_resources"]["pins"]:
        by_path = {path: payload for path, payload, _ in resources[pin["resource_id"]]}
        for file in pin["files"]:
            file["expected_payload"] = by_path[file["path"]]
    return manifest


def _install_downloads(monkeypatch: pytest.MonkeyPatch, objects: dict[str, bytes]):
    opened: list[str] = []

    def open_download(url: str, *, if_match: str = ""):
        opened.append(url)
        if url not in objects:
            raise MaterializeError("shared resource download failed with HTTP 404")
        return io.BytesIO(objects[url])

    monkeypatch.setattr("worker.resource_fetch._open_download", open_download)
    return opened


def test_worker_and_host_materialize_identical_agent_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = {
        "rules": [
            ("docs/rules.txt", "规则：先审题。\n".encode(), "text/plain; charset=utf-8"),
            ("assets/table.bin", b"\x00\xff\x10\x80", "application/octet-stream"),
        ]
    }
    manifest = _with_payloads(_manifest(resources), resources)
    opened = _install_downloads(monkeypatch, _objects(manifest))

    worker = materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")

    files = worker["rules"]["files"]
    assert Path(files[0]["local_path"]).read_bytes() == resources["rules"][0][1]
    assert Path(files[1]["local_path"]).read_bytes() == resources["rules"][1][1]
    assert worker["rules"]["version"] == 4
    assert "download_url" not in json.dumps(worker)
    assert "storage_key" not in json.dumps(worker)
    cached = materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    assert cached == worker
    assert len(opened) == 2

    pin = manifest["runtime_context"]["shared_resources"]["pins"][0]
    host_pin = {key: value for key, value in pin.items() if key != "files"}
    host_pin["files"] = [
        {
            key: value
            for key, value in file.items()
            if key not in {"download_url", "expected_payload"}
        }
        | {"storage_key": f"shared-resources/{file['path']}"}
        for file in pin["files"]
    ]
    storage = FakeObjectStorage(
        objects={
            f"shared-resources/{file['path']}": file["expected_payload"] for file in pin["files"]
        }
    )
    host = materialize_resource_pins([host_pin], tmp_path / "host-cache", storage)
    for worker_file, host_file in zip(
        worker["rules"]["files"], host["rules"]["files"], strict=True
    ):
        assert (
            Path(worker_file["local_path"]).read_bytes()
            == Path(host_file["local_path"]).read_bytes()
        )
        assert worker_file["sha256"] == host_file["sha256"]
        assert worker_file["size_bytes"] == host_file["size_bytes"]


def test_missing_object_and_wrong_hash_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = {"rules": [("rules.txt", b"expected", "text/plain")]}
    manifest = _with_payloads(_manifest(resources), resources)
    objects = _objects(manifest)
    _install_downloads(monkeypatch, {})
    with pytest.raises(MaterializeError, match="404"):
        materialize_claim_shared_resources(manifest, tmp_path / "missing" / "exec")

    objects[next(iter(objects))] = b"corrupt!"
    _install_downloads(monkeypatch, objects)
    with pytest.raises(MaterializeError, match="size|sha256"):
        materialize_claim_shared_resources(manifest, tmp_path / "changed" / "exec")


def test_poisoned_file_and_tree_cache_fail_without_redownload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = {"rules": [("rules.txt", b"expected", "text/plain")]}
    manifest = _with_payloads(_manifest(resources), resources)
    opened = _install_downloads(monkeypatch, _objects(manifest))
    file = manifest["runtime_context"]["shared_resources"]["pins"][0]["files"][0]
    cache_root = tmp_path / "work" / "materials_cache"
    poisoned = cache_file_path(cache_root, file["sha256"])
    poisoned.parent.mkdir(parents=True)
    poisoned.write_bytes(b"poisoned")
    with pytest.raises(MaterializeError, match="hash/size mismatch"):
        materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    assert opened == []

    opened.clear()
    poisoned.write_bytes(file["expected_payload"])
    result = materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    tree_file = Path(result["rules"]["files"][0]["local_path"])
    tree_file.write_bytes(b"poisoned")
    with pytest.raises(MaterializeError, match="hash/size mismatch"):
        materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    assert opened == []


def _erase_declarations(manifest: dict[str, Any]) -> None:
    manifest["shared_resources"] = []
    manifest["runtime_context"]["job"]["workflow_definition_snapshot_json"] = json.dumps(
        {"nodes": {"prepare": {"shared_resources": []}}}
    )


def _inject_into_empty_snapshot(manifest: dict[str, Any]) -> None:
    manifest["runtime_context"]["job"]["workflow_definition_snapshot_json"] = json.dumps(
        {"nodes": {"prepare": {"shared_resources": []}}}
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest["runtime_context"].update(shared_resources=None),
        lambda manifest: manifest["runtime_context"]["shared_resources"].update(format="v0"),
        lambda manifest: manifest["runtime_context"]["shared_resources"].update(pins=[]),
        lambda manifest: manifest["runtime_context"]["shared_resources"]["pins"][0]["files"][0].pop(
            "download_url"
        ),
        lambda manifest: manifest["runtime_context"]["shared_resources"]["pins"][0].update(
            manifest_hash="0" * 64
        ),
        _erase_declarations,
        _inject_into_empty_snapshot,
    ],
)
def test_deleted_or_downgraded_descriptor_never_runs_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation
) -> None:
    resources = {"rules": [("rules.txt", b"expected", "text/plain")]}
    manifest = _with_payloads(_manifest(resources), resources)
    opened = _install_downloads(monkeypatch, _objects(manifest))
    mutation(manifest)
    with pytest.raises((MaterializeError, ValueError), match="shared resource"):
        materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    assert opened == []


def test_all_descriptors_are_validated_before_the_first_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = {
        "first": [("a.txt", b"a", "text/plain")],
        "second": [("b.txt", b"b", "text/plain")],
    }
    manifest = _with_payloads(_manifest(resources), resources)
    opened = _install_downloads(monkeypatch, _objects(manifest))
    manifest["runtime_context"]["shared_resources"]["pins"][1]["files"][0].pop("download_url")
    with pytest.raises(MaterializeError, match="failed verification"):
        materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    assert opened == []


def test_deleted_manifest_declaration_still_uses_the_frozen_job_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = {"rules": [("rules.txt", b"expected", "text/plain")]}
    manifest = _with_payloads(_manifest(resources), resources)
    _install_downloads(monkeypatch, _objects(manifest))
    manifest["shared_resources"] = []
    result = materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    assert Path(result["rules"]["files"][0]["local_path"]).read_bytes() == b"expected"


@pytest.mark.parametrize("snapshot", [None, "", {}, []])
def test_nonempty_manifest_never_substitutes_itself_for_a_missing_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, snapshot
) -> None:
    resources = {"rules": [("rules.txt", b"expected", "text/plain")]}
    manifest = _with_payloads(_manifest(resources), resources)
    opened = _install_downloads(monkeypatch, _objects(manifest))
    manifest["runtime_context"]["job"]["workflow_definition_snapshot_json"] = snapshot
    with pytest.raises(ValueError, match="differs from the job snapshot|snapshot is invalid"):
        materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    assert opened == []


def test_manifest_cannot_inject_the_other_nodes_resource_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = {"rules": [("rules.txt", b"expected", "text/plain")]}
    manifest = _with_payloads(_manifest(resources), resources)
    opened = _install_downloads(monkeypatch, _objects(manifest))
    manifest["runtime_context"]["job"]["workflow_definition_snapshot_json"] = json.dumps(
        {
            "nodes": {
                "prepare": {"shared_resources": ["other-resource"]},
                "other": {"shared_resources": ["rules"]},
            }
        }
    )
    with pytest.raises(ValueError, match="differs from the job snapshot"):
        materialize_claim_shared_resources(manifest, tmp_path / "work" / "exec")
    assert opened == []


def test_legacy_plain_manifest_does_not_require_a_snapshot() -> None:
    from shared.resource_manifest import claim_resource_ids

    assert claim_resource_ids({"node_key": "plain"}, None) == ()
    assert claim_resource_ids({"node_key": "plain", "shared_resources": []}, {}) == ()
