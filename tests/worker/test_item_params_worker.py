"""Worker code nodes read generic item params without a new claim channel (#1565)."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.app.services.run_payload import sdk_batch_row
from shared import code_sandbox
from worker.code_runner import execute_code
from worker.status import ExecutionStatusReporter

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_db

CODE = """\
from workspace_libs.node_sdk import NodeContext, entrypoint

@entrypoint
def run(ctx: NodeContext) -> None:
    candidate = ctx.batch_payload["task_candidates"][0]
    ctx.artifacts.write_json(
        "output.json",
        {"params": candidate["params"], "node_config": dict(ctx.config)},
    )
"""


class _Client:
    def __init__(self, bundle: Path) -> None:
        self.bundle = bundle

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.bundle.read_bytes())


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "code-bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(
            REPO_ROOT / "workspace_libs",
            arcname="workspace_libs",
            filter=lambda member: None if "__pycache__" in member.name else member,
        )
        source = CODE.encode("utf-8")
        info = tarfile.TarInfo("node_code.py")
        info.size = len(source)
        tar.addfile(info, io.BytesIO(source))
    return bundle


def _claim(params: dict[str, Any]) -> dict[str, Any]:
    input_doc = {"type": "bundle", "bundle_id": "bundle-params", "params": params}
    job = {
        "id": "job-params",
        "workflow_key": "ws-params",
        "run_id": "run-params",
        "input_json": json.dumps(input_doc, ensure_ascii=False),
    }
    run = {"id": "run-params", "frozen_pins_json": "{}"}
    return {
        "execution_id": "exec-worker-params",
        "lease_id": "lease-worker-params",
        "node_key": "consume",
        "kind": "code",
        "bundle_url": "/bundle/code.tar.gz",
        "manifest": {
            "kind": "code",
            "execution_id": "exec-worker-params",
            "workspace_id": "ws-params",
            "job_id": "job-params",
            "workflow_key": "ws-params",
            "node_key": "consume",
            "capability": "consume",
            "code_hash": hashlib.sha256(CODE.encode("utf-8")).hexdigest(),
            "custom_code": True,
            "config_schema": {},
            "config": {"safe_business_value": "from-node-config"},
            "inputs": [],
            "expected_outputs": ["output.json"],
            "timeout_seconds": 30,
            "sandbox_network": False,
            "log_path": "data/logs/jobs/job-params-consume.log",
            "runtime_context": {
                "job": job,
                "workspace": {"id": "ws-params"},
                "settings_config": {},
                "job_batch": sdk_batch_row(run, job),
                "skill_versions": {},
            },
        },
    }


def _fake_velites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "velites"
    script.write_text(
        '#!/usr/bin/env bash\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled-bin")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda binary: str(script) if binary == "velites" else None,
    )


def test_worker_node_reads_frozen_bundle_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_velites(tmp_path, monkeypatch)
    params = {"variant": "绿色", "options": {"count": 2, "flags": [True, None]}}
    claim = _claim(params)

    task = execute_code(
        _Client(_bundle(tmp_path)),
        claim,
        tmp_path / "work" / "exec-worker-params",
        {"node_key": "consume"},
        threading.Semaphore(1),
        threading.Event(),
        1,
        threading.Event(),
        SimpleNamespace(proc_ref={}),
        ExecutionStatusReporter(None),
    )

    assert task is not None and task.code_result is not None
    assert task.code_result["status"] == "completed", task.code_result
    output_path = tmp_path / "work" / "exec-worker-params" / "job" / "output.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "params": params,
        "node_config": {"safe_business_value": "from-node-config"},
    }
