"""Worker 物化接线：claim material 块 → 本地缓存 → runtime/沙箱（MATERIAL-ACCESS-001）。

覆盖 worker/material_fetch.py 的下载物化、worker/code_runner.py 的
runtime 注入与静态 allow-read、以及 cleanup/stale_sweep 对缓存目录的豁免。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from shared.code_sandbox import build_sandbox_argv
from shared.material_cache import MATERIALS_CACHE_DIRNAME, MaterializeError
from worker import binary_resolution, material_fetch
from worker.cleanup import clean_work_root
from worker.code_runner import execute_code
from worker.material_fetch import materialize_claim_material
from worker.stale_sweep import sweep_stale_executions
from worker.status import ExecutionStatusReporter

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]

PAYLOAD = b"worker-material" * 60
HASH = hashlib.sha256(PAYLOAD).hexdigest()

CODE_READS_MATERIAL = (
    "import json\n"
    "from pathlib import Path\n"
    "\n"
    "def run(job, job_dir, runtime):\n"
    "    block = runtime.get('materials') or {}\n"
    "    data = Path(block['path']).read_bytes()\n"
    "    Path(job_dir, 'output.json').write_text(\n"
    "        json.dumps({'material_id': block['material_id'], 'size': len(data)}),\n"
    "        encoding='utf-8',\n"
    "    )\n"
)


def _material_block(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "material_id": "mat-w1",
        "filename": "notes.txt",
        "content_type": "text/plain",
        "size_bytes": len(PAYLOAD),
        "content_hash": HASH,
        "download_url": "https://s3.test/download/fake?sig=x",
    }
    block.update(overrides)
    return block


def _fake_download(monkeypatch: pytest.MonkeyPatch, payload: bytes = PAYLOAD) -> list[str]:
    """替换 worker.material_fetch._open_download 为内存流；返回请求的 URL 列表。"""
    urls: list[str] = []

    def _open(url: str, *, if_match: str = "") -> io.BytesIO:
        urls.append(url)
        return io.BytesIO(payload)

    monkeypatch.setattr(material_fetch, "_open_download", _open)
    return urls


def _manifest_with_material(material: dict[str, Any] | None) -> dict[str, Any]:
    return {"runtime_context": {"material": material}}


def test_materialize_downloads_into_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls = _fake_download(monkeypatch)
    execution_dir = tmp_path / "work" / "exec-1"
    execution_dir.mkdir(parents=True)

    block = materialize_claim_material(_manifest_with_material(_material_block()), execution_dir)

    assert block is not None
    assert urls == ["https://s3.test/download/fake?sig=x"]
    path = Path(block["path"])
    cache_root = tmp_path / "work" / MATERIALS_CACHE_DIRNAME
    assert path == cache_root / HASH[:2] / HASH
    assert path.read_bytes() == PAYLOAD

    # 命中：同一 execution 再次物化不再下载。
    again = materialize_claim_material(_manifest_with_material(_material_block()), execution_dir)
    assert again is not None and again["path"] == str(path)
    assert len(urls) == 1


def test_materialize_none_without_descriptor(tmp_path: Path) -> None:
    assert materialize_claim_material({"runtime_context": {}}, tmp_path) is None
    assert materialize_claim_material({}, tmp_path) is None


def test_materialize_rejects_incomplete_descriptor(tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="incomplete"):
        materialize_claim_material(_manifest_with_material({"material_id": "mat-w1"}), tmp_path)


def test_materialize_verifies_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 与声明同长度、内容不同：size 通过，sha256 失败。
    _fake_download(monkeypatch, payload=b"x" * len(PAYLOAD))
    with pytest.raises(MaterializeError, match="sha256"):
        materialize_claim_material(_manifest_with_material(_material_block()), tmp_path / "exec-1")


def test_materialize_sends_if_match_with_object_etag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1279：claim 描述符带 object_etag 时，presigned GET 带 If-Match 头核验；
    描述符无该字段（旧 Server）→ 不带条件头（新旧兼容矩阵的新 Worker 半格）。"""
    seen: list[dict[str, str]] = []

    def _open(url: str, *, if_match: str = "") -> io.BytesIO:
        seen.append({"url": url, "if_match": if_match})
        return io.BytesIO(PAYLOAD)

    monkeypatch.setattr(material_fetch, "_open_download", _open)
    materialize_claim_material(
        _manifest_with_material(_material_block(object_etag="etag-1")), tmp_path / "exec-1"
    )
    assert seen[0]["if_match"] == "etag-1"

    # 旧 Server 的描述符无 object_etag：空值 → 无条件 GET。
    # （独立 work 根：不与上面的 exec-1 共享缓存。）
    materialize_claim_material(
        _manifest_with_material(_material_block()), tmp_path / "w2" / "exec-2"
    )
    assert seen[1]["if_match"] == ""


def test_open_download_sends_if_match_header_and_maps_412(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1279（Worker 链故障注入）：_open_download 真实头装配——If-Match 带
    引号发出；412（complete 后同尺寸覆盖）映射为 MaterializeError。"""
    sent: list[dict] = []

    class _Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.raw = io.BytesIO(PAYLOAD)

        def close(self) -> None:
            pass

    def _get(url: str, **kwargs: Any) -> _Response:
        sent.append(kwargs)
        if_match = (kwargs.get("headers") or {}).get("If-Match", "")
        return _Response(412 if if_match and if_match != '"etag-1"' else 200)

    monkeypatch.setattr(material_fetch.requests, "get", _get)

    stream = material_fetch._open_download("https://s3.test/x", if_match="etag-1")
    assert sent[0]["headers"] == {"If-Match": '"etag-1"'}
    assert stream.read() == PAYLOAD

    with pytest.raises(MaterializeError, match="ETag mismatch"):
        material_fetch._open_download("https://s3.test/x", if_match="stale")
    # 无 etag（旧 Server / 存量行）：不带条件头。
    material_fetch._open_download("https://s3.test/x")
    assert sent[-1]["headers"] == {}


def test_hashless_address_follows_generation_with_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1279 codex r6：无 hash 材料的缓存地址带代次（代次即身份）——
    代次推进（repair）后不再命中旧地址、重新下载；旧 Server 描述符无
    upload_generation 字段 → 回落裸 material_id（既有形态）。"""
    urls = _fake_download(monkeypatch)
    block_no_hash = _material_block(content_hash="", upload_generation=0)

    first = materialize_claim_material(
        _manifest_with_material(block_no_hash), tmp_path / "work" / "exec-1"
    )
    assert first is not None
    assert Path(first["path"]).name == "mat-w1-v2g0"
    assert len(urls) == 1

    # 代次推进（修复后）：新地址、重新下载。
    second = materialize_claim_material(
        _manifest_with_material(_material_block(content_hash="", upload_generation=1)),
        tmp_path / "work" / "exec-2",
    )
    assert second is not None
    assert Path(second["path"]).name == "mat-w1-v2g1"
    assert len(urls) == 2

    # 旧 Server（描述符无 upload_generation）→ 裸 material_id 地址。
    legacy = _material_block(content_hash="")
    legacy.pop("upload_generation", None)
    third = materialize_claim_material(_manifest_with_material(legacy), tmp_path / "w2" / "exec-3")
    assert third is not None
    assert Path(third["path"]).name == "mat-w1"


def test_legacy_unbound_hashless_descriptor_is_size_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1312 方案 B：新 Server 对存量无 hash 行下发 object_etag=''（身份
    未建立）——Worker 维持 base 的 size-only 语义：无条件 GET 照常物化，
    不拒绝（存量行身份建立拆至 #1316）。"""
    seen: list[str] = []

    def _open(url: str, *, if_match: str = "") -> io.BytesIO:
        seen.append(if_match)
        return io.BytesIO(PAYLOAD)

    monkeypatch.setattr(material_fetch, "_open_download", _open)
    legacy = _material_block(content_hash="", object_etag="", upload_generation=0)
    block = materialize_claim_material(_manifest_with_material(legacy), tmp_path / "exec-1")

    assert block is not None and Path(block["path"]).read_bytes() == PAYLOAD
    assert seen == [""]


def test_sandbox_argv_grants_static_cache_root(tmp_path: Path) -> None:
    cache_root = tmp_path / MATERIALS_CACHE_DIRNAME
    argv = build_sandbox_argv(
        "/usr/bin/velites",
        tmp_path / "job",
        [tmp_path / "bundle"],
        tmp_path / "job" / ".code_result.json",
        sandbox_network=False,
        materials_cache_root=cache_root,
    )
    allow_reads = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--allow-read"]
    # 目录不存在时不放行；存在时静态放行缓存根（MATERIAL-ACCESS-001）。
    assert str(cache_root) not in allow_reads
    cache_root.mkdir()
    argv = build_sandbox_argv(
        "/usr/bin/velites",
        tmp_path / "job",
        [tmp_path / "bundle"],
        tmp_path / "job" / ".code_result.json",
        sandbox_network=False,
        materials_cache_root=cache_root,
    )
    allow_reads = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--allow-read"]
    assert str(cache_root) in allow_reads


def _code_bundle(tmp_path: Path, code_text: str) -> Path:
    bundle = tmp_path / "code-bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(
            REPO_ROOT / "workspace_libs",
            arcname="workspace_libs",
            filter=lambda member: None if "__pycache__" in member.name else member,
        )
        data = code_text.encode("utf-8")
        info = tarfile.TarInfo("node_code.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return bundle


class _FakeClient:
    def __init__(self, bundle: Path) -> None:
        self._bundle = bundle

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())


def _claim(code_text: str, material: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_id": "exec-mat-1",
        "lease_id": "lease-1",
        "node_key": "node_a",
        "kind": "code",
        "bundle_url": "/bundle/x.tar.gz",
        "manifest": {
            "kind": "code",
            "execution_id": "exec-mat-1",
            "workspace_id": "ws-1",
            "job_id": "job-1",
            "workflow_key": "wf",
            "node_key": "node_a",
            "capability": "intake_items",
            "code_hash": hashlib.sha256(code_text.encode("utf-8")).hexdigest(),
            "custom_code": False,
            "config_schema": {},
            "config": {},
            "inputs": [],
            "expected_outputs": ["output.json"],
            "timeout_seconds": 30,
            "sandbox_network": False,
            "log_path": "data/logs/jobs/job-1-node_a.log",
            "runtime_context": {
                "job": {"id": "job-1", "workflow_key": "wf"},
                "workspace": {"id": "ws-1"},
                "settings_config": {},
                "job_batch": None,
                "skill_versions": {},
                "material": material,
            },
        },
    }


def test_execute_code_materializes_and_exposes_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端（假 velites 直通）：节点经 runtime["materials"] 读到物化文件。"""
    urls = _fake_download(monkeypatch)
    # velites 桩：跳过 wrap 参数直接 exec -- 之后的命令（不真实沙箱）。
    script = tmp_path / "velites"
    script.write_text(
        '#!/usr/bin/env bash\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bundled-bin")
    monkeypatch.setattr(
        shutil, "which", lambda binary: str(script) if binary == "velites" else None
    )
    client = _FakeClient(_code_bundle(tmp_path, CODE_READS_MATERIAL))
    execution_dir = tmp_path / "work" / "exec-mat-1"

    task = execute_code(
        client,
        _claim(CODE_READS_MATERIAL, _material_block()),
        execution_dir,
        {"node_key": "node_a"},
        threading.Semaphore(2),
        threading.Event(),
        1,
        threading.Event(),
        SimpleNamespace(proc_ref={}),  # type: ignore[arg-type]
        ExecutionStatusReporter(None),
    )

    assert task is not None and task.code_result is not None
    assert task.code_result["status"] == "completed"
    assert urls == ["https://s3.test/download/fake?sig=x"]
    # 沙箱 argv 静态放行 work_root 下的缓存根。
    cache_root = tmp_path / "work" / MATERIALS_CACHE_DIRNAME
    allow_reads = [
        task.command[i + 1] for i, tok in enumerate(task.command) if tok == "--allow-read"
    ]
    assert str(cache_root) in allow_reads
    # 节点读到的是物化缓存里的本地文件。
    output = json.loads((execution_dir / "job" / "output.json").read_text(encoding="utf-8"))
    assert output == {"material_id": "mat-w1", "size": len(PAYLOAD)}
    assert (cache_root / HASH[:2] / HASH).read_bytes() == PAYLOAD


def test_clean_work_root_preserves_materials_cache(tmp_path: Path) -> None:
    cache_root = tmp_path / MATERIALS_CACHE_DIRNAME
    cached = cache_root / HASH[:2] / HASH
    cached.parent.mkdir(parents=True)
    cached.write_bytes(PAYLOAD)
    stale = tmp_path / "exec-stale"
    stale.mkdir()

    clean_work_root(tmp_path)

    assert cached.is_file()
    assert not stale.exists()


def test_stale_sweep_preserves_materials_cache(tmp_path: Path) -> None:
    cache_root = tmp_path / MATERIALS_CACHE_DIRNAME
    cached = cache_root / HASH[:2] / HASH
    cached.parent.mkdir(parents=True)
    cached.write_bytes(PAYLOAD)
    stale = tmp_path / "exec-stale"
    stale.mkdir()
    old = time.time() - 48 * 3600
    for path in (cached, stale):
        os.utime(path, (old, old))

    sweep_stale_executions(tmp_path)

    assert cache_root.exists(), "缓存目录由容量淘汰管理，不按时间清扫"
    assert not stale.exists()
