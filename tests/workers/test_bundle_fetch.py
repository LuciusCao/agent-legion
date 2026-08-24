"""Worker bundle 物化：claim bundle 描述符 → 本地缓存目录树（#156）。"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from shared.material_bundle import bundle_address
from shared.material_cache import MaterializeError
from worker.material_fetch import materialize_claim_material

pytestmark = pytest.mark.no_db

PAYLOAD_A = b"worker-bundle-a" * 20
PAYLOAD_B = b"worker-bundle-b" * 30
HASH_A = hashlib.sha256(PAYLOAD_A).hexdigest()
HASH_B = hashlib.sha256(PAYLOAD_B).hexdigest()
PAYLOADS = {"https://s3.test/get/a": PAYLOAD_A, "https://s3.test/get/b": PAYLOAD_B}


@pytest.fixture
def downloads(monkeypatch):
    def _open(url: str):
        return io.BytesIO(PAYLOADS[url])

    # bundle_fetch 从 material_fetch 导入了 _open_download（独立绑定），两边都 patch。
    monkeypatch.setattr("worker.bundle_fetch._open_download", _open)
    monkeypatch.setattr("worker.material_fetch._open_download", _open)
    return PAYLOADS


def _manifest(**overrides) -> dict:
    material = {
        "material_id": "bundle-1",
        "kind": "bundle",
        "filename": "folder",
        "size_bytes": len(PAYLOAD_A) + len(PAYLOAD_B),
        "entries": [
            {
                "material_id": "mat-a",
                "path": "a.txt",
                "size_bytes": len(PAYLOAD_A),
                "content_hash": HASH_A,
                "download_url": "https://s3.test/get/a",
            },
            {
                "material_id": "mat-b",
                "path": "sub/b.txt",
                "size_bytes": len(PAYLOAD_B),
                "content_hash": HASH_B,
                "download_url": "https://s3.test/get/b",
            },
        ],
    }
    material.update(overrides)
    return {"runtime_context": {"material": material}}


def test_materialize_claim_bundle_builds_tree(downloads, tmp_path: Path) -> None:
    block = materialize_claim_material(_manifest(), tmp_path / "exec")

    assert block is not None
    assert block["kind"] == "bundle"
    assert block["material_id"] == "bundle-1"
    tree = Path(block["path"])
    assert tree.is_dir()
    assert (tree / "a.txt").read_bytes() == PAYLOAD_A
    assert (tree / "sub" / "b.txt").read_bytes() == PAYLOAD_B
    assert [entry["path"] for entry in block["entries"]] == ["a.txt", "sub/b.txt"]
    # 与 Host 同一地址规则：缓存目录位置一致。
    expected = bundle_address([(HASH_A, "a.txt"), (HASH_B, "sub/b.txt")])
    assert block["content_hash"] == expected
    assert tree == tmp_path / "materials_cache" / expected[:2] / expected


def test_materialize_claim_bundle_verifies_member_hash(downloads, tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["runtime_context"]["material"]["entries"][0]["content_hash"] = "0" * 64

    with pytest.raises(MaterializeError, match="sha256"):
        materialize_claim_material(manifest, tmp_path / "exec")


def test_materialize_claim_bundle_rejects_incomplete_descriptor(tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="incomplete"):
        materialize_claim_material(_manifest(entries=[]), tmp_path / "exec")
    manifest = _manifest()
    del manifest["runtime_context"]["material"]["entries"][0]["download_url"]
    with pytest.raises(MaterializeError, match="incomplete"):
        materialize_claim_material(manifest, tmp_path / "exec")


def test_materialize_claim_material_dispatches_by_kind(downloads, tmp_path: Path) -> None:
    # kind 缺席时走单文件路径（既有行为不变）。
    block = materialize_claim_material(
        {
            "runtime_context": {
                "material": {
                    "material_id": "mat-1",
                    "download_url": "https://s3.test/get/a",
                    "content_hash": HASH_A,
                    "size_bytes": len(PAYLOAD_A),
                }
            }
        },
        tmp_path / "exec",
    )
    assert block is not None
    assert "kind" not in block
    assert Path(block["path"]).is_file()
