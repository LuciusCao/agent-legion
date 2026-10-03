"""Contract tests for SeaweedFS collection reclaim in worktree teardown (#824).

``scripts/seaweedfs_collection.py`` is exercised directly with stubbed
fetch/delete callables and against an in-process fake master HTTP server;
the ``clean-worktree.sh`` wiring is exercised end to end by copying the
script into a synthetic repo layout, stubbing git/psql/uv on PATH and
boto3/dotenv/storage via probe modules — no real worktree, database,
bucket or SeaweedFS instance is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.seaweedfs_collection import (
    CollectionGuardError,
    collection_volume_ids,
    reclaim_collection,
    resolve_master_url,
)

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]


class FakeMaster:
    """In-memory SeaweedFS master: ``/vol/status`` + ``/col/delete``."""

    def __init__(self, volumes: dict[int, str], *, delete_status: int = 204) -> None:
        self.volumes = dict(volumes)  # volume id -> collection
        self.delete_status = delete_status
        self.deleted: list[str] = []

    def vol_status(self) -> dict:
        vols = [{"Id": vid, "Collection": col} for vid, col in sorted(self.volumes.items())]
        return {"Volumes": {"DataCenters": {"dc1": {"rack1": {"node:8080": vols}}}}}

    def delete(self, collection: str) -> tuple[int, str]:
        self.deleted.append(collection)
        if self.delete_status != 204:
            return self.delete_status, '{"error":"boom"}'
        if collection not in self.volumes.values():
            return 400, f'{{"error":"collection {collection} does not exist"}}'
        self.volumes = {v: c for v, c in self.volumes.items() if c != collection}
        return 204, ""


@pytest.fixture
def master_server() -> Iterator[tuple[FakeMaster, str]]:
    master = FakeMaster(
        {1: "", 2: "", 8: "agent-legion-wt-a", 9: "agent-legion-wt-a", 26: "agent-legion"}
    )

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if urlsplit(self.path).path == "/vol/status":
                self._reply(200, json.dumps(master.vol_status()))
            else:
                self._reply(404, "")

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            parts = urlsplit(self.path)
            if parts.path != "/col/delete":
                self._reply(404, "")
                return
            code, body = master.delete(parse_qs(parts.query)["collection"][0])
            self._reply(code, body)

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield master, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# --- module: master URL resolution & guard --------------------------------


@pytest.mark.parametrize(
    ("endpoint", "env", "expected"),
    [
        ("http://127.0.0.1:8333", {}, "http://127.0.0.1:9333"),
        (
            "http://seaweedfs:8333",
            {"AGENT_LEGION_LOCAL_S3_BACKEND": "seaweedfs"},
            "http://seaweedfs:9333",
        ),
        ("http://[::1]:8333", {}, "http://[::1]:9333"),
        ("http://127.0.0.1:8333", {"AGENT_LEGION_LOCAL_S3_BACKEND": "rustfs"}, None),
        ("http://127.0.0.1:9000", {}, None),  # rustfs port convention
        (None, {}, None),  # AWS default endpoint
        ("http://x:1", {"AGENT_LEGION_SEAWEEDFS_MASTER_URL": "http://m:9333/"}, "http://m:9333"),
    ],
)
def test_resolve_master_url(
    endpoint: str | None, env: dict[str, str], expected: str | None
) -> None:
    assert resolve_master_url(endpoint, env) == expected


@pytest.mark.parametrize(
    "collection",
    [
        "",
        "agent-legion",
        "agent-legion-",
        "agent-legion-develop",
        "agent-legion-prod",
        "other-bucket",
    ],
)
def test_guard_rejects_shared_and_non_derived_collections(collection: str) -> None:
    calls: list[str] = []
    with pytest.raises(CollectionGuardError):
        reclaim_collection(
            "http://m", collection, fetch=lambda url: calls.append(url), delete=calls.append
        )
    assert calls == []  # guard fires before any master call


def test_reclaim_deletes_leftover_volumes_and_verifies() -> None:
    master = FakeMaster({1: "", 8: "agent-legion-wt-a", 9: "agent-legion-wt-a", 26: "agent-legion"})

    def delete(url: str) -> None:
        master.delete(parse_qs(urlsplit(url).query)["collection"][0])

    result = reclaim_collection(
        "http://m", "agent-legion-wt-a", fetch=lambda url: master.vol_status(), delete=delete
    )

    assert result.reclaimed == (8, 9) and result.leftover == () and result.ok
    assert master.deleted == ["agent-legion-wt-a"]
    # Untagged (filer meta log) and prod volumes are untouched.
    assert master.volumes == {1: "", 26: "agent-legion"}


def test_reclaim_is_noop_when_collection_already_gone() -> None:
    deletes: list[str] = []
    result = reclaim_collection(
        "http://m",
        "agent-legion-wt-a",
        fetch=lambda url: FakeMaster({1: ""}).vol_status(),
        delete=deletes.append,
    )
    assert result.ok and result.reclaimed == ()
    assert deletes == []


def test_reclaim_reports_leftover_when_delete_does_not_take() -> None:
    master = FakeMaster({8: "agent-legion-wt-a"})
    result = reclaim_collection(
        "http://m",
        "agent-legion-wt-a",
        fetch=lambda url: master.vol_status(),
        delete=lambda url: None,
    )
    assert not result.ok and result.leftover == (8,)


def test_collection_volume_ids_tolerates_empty_topology() -> None:
    assert collection_volume_ids("http://m", "agent-legion-wt-a", lambda url: {"Volumes": {}}) == ()


def test_reclaim_over_http(master_server: tuple[FakeMaster, str]) -> None:
    master, url = master_server
    result = reclaim_collection(url, "agent-legion-wt-a")
    assert result.reclaimed == (8, 9) and result.ok
    assert master.volumes == {1: "", 2: "", 26: "agent-legion"}


def test_reclaim_over_http_surfaces_master_errors(master_server: tuple[FakeMaster, str]) -> None:
    master, url = master_server
    master.delete_status = 500
    with pytest.raises(OSError):  # urllib HTTPError is an OSError subclass
        reclaim_collection(url, "agent-legion-wt-a")


# --- clean-worktree.sh wiring ----------------------------------------------

_GIT_STUB = """#!/usr/bin/env bash
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  echo "worktree __MAIN__"
  echo "bare"
  exit 0
fi
exit 0
"""

# uv run --frozen python - -> real interpreter with probe modules first.
_UV_STUB = """#!/usr/bin/env bash
export PYTHONPATH="$PROBE_DIR:$PYTHONPATH"
exec "$REAL_PYTHON" -
"""

_BOTOCORE_EXCEPTIONS = """
class ClientError(Exception):
    def __init__(self, response):
        super().__init__(response)
        self.response = response


class EndpointConnectionError(Exception):
    pass
"""

# Bucket existence driven by STUB_BUCKET_EXISTS; deletions logged.
_BOTO3_PROBE = """
import os

from botocore.exceptions import ClientError


class _Paginator:
    def paginate(self, Bucket):
        yield {"Contents": [{"Key": "k", "Size": 3}]}


class _Client:
    def head_bucket(self, Bucket):
        if os.environ.get("STUB_BUCKET_EXISTS") != "1":
            raise ClientError({"Error": {"Code": "404"}})

    def get_paginator(self, op):
        return _Paginator()

    def delete_objects(self, Bucket, Delete):
        print(f"stub delete_objects {Bucket}")

    def delete_bucket(self, Bucket):
        print(f"stub delete_bucket {Bucket}")


def client(*args, **kwargs):
    return _Client()
"""

_STORAGE_PROBE = """
import os
from types import SimpleNamespace


def load_s3_settings():
    return SimpleNamespace(
        bucket="agent-legion-x",
        endpoint_url=os.environ.get("STUB_S3_ENDPOINT", "http://127.0.0.1:8333"),
        region="us-east-1",
        access_key="",
        secret_key="",
    )
"""


def _exe(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_clean(tmp_path: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    main = tmp_path / "main"
    scripts_dir = main / ".worktrees" / "other" / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in (
        "clean-worktree.sh",
        "drop-worktree-db.sh",
        "seaweedfs_collection.py",
        "__init__.py",
    ):
        shutil.copy(ROOT / "scripts" / name, scripts_dir / name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "git", _GIT_STUB.replace("__MAIN__", str(main)))
    _exe(bin_dir / "psql", "#!/usr/bin/env bash\nexit 0\n")
    _exe(bin_dir / "uv", _UV_STUB)
    probe = tmp_path / "probe"
    (probe / "botocore").mkdir(parents=True)
    (probe / "server/app/storage").mkdir(parents=True)
    (probe / "dotenv.py").write_text("def load_dotenv(*a, **k):\n    return True\n")
    (probe / "boto3.py").write_text(_BOTO3_PROBE)
    (probe / "botocore/__init__.py").write_text("")
    (probe / "botocore/exceptions.py").write_text(_BOTOCORE_EXCEPTIONS)
    for pkg in ("server", "server/app"):
        (probe / pkg / "__init__.py").write_text("")
    (probe / "server/app/storage/__init__.py").write_text(_STORAGE_PROBE)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
        "PROBE_DIR": str(probe),
        "REAL_PYTHON": sys.executable,
    }
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(scripts_dir / "clean-worktree.sh"), "wt-a", "--yes"],
        capture_output=True,
        text=True,
        env=env,
        cwd=main,
        timeout=60,
    )


@pytest.mark.parametrize("bucket_exists", ["1", "0"])
def test_clean_worktree_reclaims_leftover_collection(
    tmp_path: Path, master_server: tuple[FakeMaster, str], bucket_exists: str
) -> None:
    master, url = master_server
    result = _run_clean(
        tmp_path,
        {"AGENT_LEGION_SEAWEEDFS_MASTER_URL": url, "STUB_BUCKET_EXISTS": bucket_exists},
    )

    assert result.returncode == 0, result.stderr
    assert "已回收 SeaweedFS collection agent-legion-wt-a 的残留卷: [8, 9]" in result.stdout
    assert master.deleted == ["agent-legion-wt-a"]
    assert master.volumes == {1: "", 2: "", 26: "agent-legion"}
    if bucket_exists == "1":
        # Collection reclaim runs only after the bucket itself is gone.
        assert result.stdout.index("stub delete_bucket") < result.stdout.index("已回收")


def test_clean_worktree_skips_reclaim_when_master_unreachable(tmp_path: Path) -> None:
    result = _run_clean(tmp_path, {"AGENT_LEGION_SEAWEEDFS_MASTER_URL": "http://127.0.0.1:1"})

    assert result.returncode == 0, result.stderr
    assert "不可达" in result.stdout and "跳过 collection 卷回收" in result.stdout
    assert "收尾清理结束" in result.stdout


def test_clean_worktree_skips_reclaim_for_non_seaweedfs_endpoint(tmp_path: Path) -> None:
    result = _run_clean(tmp_path, {"STUB_S3_ENDPOINT": "http://127.0.0.1:9000"})

    assert result.returncode == 0, result.stderr
    assert "S3 bucket 不存在（跳过）" in result.stdout
    assert "collection" not in result.stdout
