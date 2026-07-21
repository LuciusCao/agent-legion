from __future__ import annotations

import hashlib
import json
import stat
import tarfile
import time
import urllib.error
from pathlib import Path

from scripts.remote import worker
from server.app.executors.remote_bundle import build_bundle
from server.app.workflows.pi_protocol import render_command_spec

INPUT_HASH = hashlib.sha256(b"{}").hexdigest()

MANIFEST = {
    "job_id": "job-1",
    "node_key": "node_a",
    "capability": "extract_keywords",
    "inputs": ["input.json"],
    "expected_outputs": ["output.json"],
    "additional_prompt": "",
    "tools": ["read", "write", "bash"],
    "skill": "wf/extract",
    "skill_version": "abc",
    "run_token": "tok123",
    "bundle_mode": "refs",
    "artifact_upload_url": "/api/artifacts",
    "input_artifacts": {"input.json": f"sha256:{INPUT_HASH}"},
    "pi": {
        "binary": "pi",
        "provider": "deepseek",
        "model": "your-model-b",
        "thinking": "low",
        "timeout_seconds": 600,
        "environment": {"PI_TELEMETRY": "0"},
    },
}


def _claim(execution_id: str, manifest: dict) -> dict:
    """Claim shaped like a new-server response: manifest plus its command spec."""
    return {
        "execution_id": execution_id,
        "manifest": manifest,
        "command_spec": render_command_spec(manifest),
    }


FAKE_PI = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
Path("output.json").write_text("{}", encoding="utf-8")
print(json.dumps({"type": "done"}))
"""

FAKE_PI_ENV_PROBE = """#!/usr/bin/env python3
import json, os
from pathlib import Path
Path("output.json").write_text("{}", encoding="utf-8")
Path("saw_token.txt").write_text(
    str("REMOTE_WORKER_TOKEN" in os.environ), encoding="utf-8"
)
print(json.dumps({"type": "done"}))
"""


def _write_fake_pi(tmp_path: Path) -> str:
    fake = tmp_path / "fake_pi"
    fake.write_text(FAKE_PI, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(fake)


def _make_bundle(tmp_path: Path, manifest: dict) -> Path:
    skill_src = tmp_path / "skill_src"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text("# s", encoding="utf-8")
    bundle = tmp_path / "e1.tar.gz"
    build_bundle(bundle, skill_dir=skill_src, manifest=manifest)
    return bundle


class StubClient:
    def __init__(
        self,
        bundle: Path,
        artifacts: dict[str, bytes] | None = None,
        heartbeat_ok: bool = True,
    ):
        self._bundle = bundle
        self._artifacts = artifacts or {INPUT_HASH: b"{}"}
        self.uploads: dict[str, bytes] = {}
        self._heartbeat_ok = heartbeat_ok
        self.heartbeats = 0

    def download_bundle(self, claim: dict, dest: Path) -> None:
        dest.write_bytes(self._bundle.read_bytes())

    def download_artifact(self, hash: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._artifacts[hash])

    def upload_artifact(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self.uploads[digest] = data
        return digest

    def heartbeat(self, execution_id: str) -> bool:
        self.heartbeats += 1
        return self._heartbeat_ok


class FlakyHeartbeatClient(StubClient):
    """Simulates transient server errors (e.g. 5xx) on every heartbeat."""

    def heartbeat(self, execution_id: str) -> bool:
        self.heartbeats += 1
        raise urllib.error.URLError("unexpected heartbeat status: 502")


def test_run_execution_happy_path(tmp_path):
    manifest = {**MANIFEST, "pi": {**MANIFEST["pi"], "binary": _write_fake_pi(tmp_path)}}
    bundle = _make_bundle(tmp_path, manifest)
    claim = _claim("e1", manifest)
    client = StubClient(bundle)

    metadata, archive = worker.run_execution(client, claim, tmp_path / "work")

    assert metadata["status"] == "completed"
    assert metadata["exit_code"] == 0
    assert metadata["output_artifacts"]["output.json"].startswith("sha256:")
    assert archive is not None and archive.is_file()
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "output.json" in names
    assert f"runs/node_a/{manifest['run_token']}/events.jsonl" in names
    assert f"runs/node_a/{manifest['run_token']}/run.json" in names
    run_json = None
    with tarfile.open(archive, "r:gz") as tar:
        run_json = json.loads(
            tar.extractfile(f"runs/node_a/{manifest['run_token']}/run.json").read()
        )
    assert run_json["node_key"] == "node_a"
    assert run_json["run_id"] == manifest["run_token"]
    assert run_json["exit_code"] == 0
    assert run_json["model"] == {
        "provider": "deepseek",
        "model": "your-model-b",
        "thinking": "low",
    }
    assert run_json["inputs"] == ["input.json"]
    assert run_json["outputs"] == ["output.json"]
    assert run_json["skill_version"] == "abc"


def test_run_execution_strips_worker_token_from_pi_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMOTE_WORKER_TOKEN", "super-secret-token")
    fake = tmp_path / "fake_pi_env"
    fake.write_text(FAKE_PI_ENV_PROBE, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    manifest = {**MANIFEST, "pi": {**MANIFEST["pi"], "binary": str(fake)}}
    bundle = _make_bundle(tmp_path, manifest)
    claim = _claim("e1", manifest)

    metadata, archive = worker.run_execution(StubClient(bundle), claim, tmp_path / "work")

    assert metadata["status"] == "completed"
    job_dir = tmp_path / "work" / "e1" / "job"
    assert (job_dir / "saw_token.txt").read_text(encoding="utf-8") == "False"


def test_run_execution_missing_output_fails(tmp_path):
    fake = tmp_path / "fake_pi_fail"
    fake.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    manifest = {**MANIFEST, "pi": {**MANIFEST["pi"], "binary": str(fake)}}
    bundle = _make_bundle(tmp_path, manifest)
    claim = _claim("e1", manifest)

    metadata, archive = worker.run_execution(StubClient(bundle), claim, tmp_path / "work")

    assert metadata["status"] == "failed"
    assert "Missing outputs" in metadata["error_message"]
    assert archive is not None  # run_dir is still uploaded for debugging


def test_run_execution_aborts_when_claim_lost(tmp_path):
    sleepy = tmp_path / "fake_pi_sleep"
    sleepy.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n", encoding="utf-8")
    sleepy.chmod(sleepy.stat().st_mode | stat.S_IXUSR)
    manifest = {**MANIFEST, "pi": {**MANIFEST["pi"], "binary": str(sleepy), "timeout_seconds": 600}}
    bundle = _make_bundle(tmp_path, manifest)
    claim = _claim("e1", manifest)
    client = StubClient(bundle, heartbeat_ok=False)
    # shrink the heartbeat interval so the test is fast
    original = worker.HEARTBEAT_INTERVAL_SECONDS
    worker.HEARTBEAT_INTERVAL_SECONDS = 0.1
    try:
        start = time.monotonic()
        metadata, archive = worker.run_execution(client, claim, tmp_path / "work")
        elapsed = time.monotonic() - start
    finally:
        worker.HEARTBEAT_INTERVAL_SECONDS = original
    assert elapsed < 30
    assert metadata["status"] == "cancelled"
    assert archive is None  # nothing to report; the server already requeued/failed it


def test_run_execution_survives_transient_heartbeat_errors(tmp_path):
    manifest = {**MANIFEST, "pi": {**MANIFEST["pi"], "binary": _write_fake_pi(tmp_path)}}
    bundle = _make_bundle(tmp_path, manifest)
    claim = _claim("e1", manifest)
    client = FlakyHeartbeatClient(bundle)
    # shrink the heartbeat interval so the test is fast
    original = worker.HEARTBEAT_INTERVAL_SECONDS
    worker.HEARTBEAT_INTERVAL_SECONDS = 0.1
    try:
        metadata, archive = worker.run_execution(client, claim, tmp_path / "work")
    finally:
        worker.HEARTBEAT_INTERVAL_SECONDS = original
    assert metadata["status"] == "completed"
    assert archive is not None and archive.is_file()
    assert client.heartbeats > 0


def test_run_execution_reports_shard_output(tmp_path):
    fake = tmp_path / "fake_pi_shard"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "Path('output.json').write_text('{}', encoding='utf-8')\n"
        "Path('shard_output.json').write_text('{\"r\": 1}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    manifest = {
        **MANIFEST,
        "pi": {**MANIFEST["pi"], "binary": str(fake)},
        "shard_index": 1,
        "shard_input": {"q": 2},
    }
    bundle = _make_bundle(tmp_path, manifest)
    metadata, _archive = worker.run_execution(
        StubClient(bundle),
        _claim("e1", manifest),
        tmp_path / "work",
    )
    prompt = (
        tmp_path / "work" / "e1" / "job" / "runs" / "node_a" / "tok123" / "prompt.md"
    ).read_text(encoding="utf-8")
    assert "Shard index: 1" in prompt
    assert '"q": 2' in prompt
    assert metadata["shard_output"] == '{"r": 1}'
