"""Unit tests for the Agent Worker execution preparation (worker/execution_prepare.py)."""

from __future__ import annotations

import threading
from pathlib import Path

from server.app.agent_broker.agent_bundle import build_agent_bundle
from worker.execution_prepare import prepare_execution


def _make_bundle(tmp_path: Path, manifest: dict) -> Path:
    skill_src = tmp_path / "skill_src"
    skill_src.mkdir(exist_ok=True)
    (skill_src / "SKILL.md").write_text("# s", encoding="utf-8")
    bundle = tmp_path / "bundle.tar.gz"
    build_agent_bundle(bundle, skill_dir=skill_src, manifest=manifest)
    return bundle


class FakeClient:
    def __init__(self, bundle: Path) -> None:
        self._bundle = bundle

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())


def test_prepare_execution_substitutes_prompt_placeholders(tmp_path: Path) -> None:
    manifest = {
        "command_spec": {
            "command": ["pi", "@{prompt_file}"],
            "prompt": "Working directory: {job_dir}\nSkill directory: {skill_dir}\n",
        },
        "input_artifacts": {},
        "expected_outputs": ["output.json"],
        "pi": {"timeout_seconds": 60},
    }
    bundle = _make_bundle(tmp_path, manifest)
    claim = {
        "execution_id": "exec-1",
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }
    execution_dir = tmp_path / "exec-1"

    prepare_execution(FakeClient(bundle), claim, execution_dir, threading.Semaphore(1))

    prompt = (execution_dir / "job" / "runs" / "node_a" / "worker" / "prompt.md").read_text(
        encoding="utf-8"
    )
    assert "{job_dir}" not in prompt
    assert "{skill_dir}" not in prompt
    assert f"Working directory: {execution_dir}/job" in prompt
    assert f"Skill directory: {execution_dir}/bundle/skill" in prompt
