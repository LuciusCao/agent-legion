from __future__ import annotations

from pathlib import Path

import pytest

from scripts.remote import worker
from server.app.executors.remote_bundle import build_bundle

MANIFEST = {
    "job_id": "j1",
    "node_key": "gen",
    "capability": "cap",
    "inputs": ["input.json"],
    "expected_outputs": [],
    "additional_prompt": "",
    "tools": ["read"],
    "skill": "wf/gen",
    "skill_version": "abc",
    "run_token": "tok123",
    "pi": {"binary": "pi", "timeout_seconds": 60, "environment": {}},
}


def _make_bundle(tmp_path: Path, manifest: dict) -> Path:
    skill_src = tmp_path / "skill_src"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text("# s", encoding="utf-8")
    job_src = tmp_path / "job_src"
    job_src.mkdir()
    (job_src / "input.json").write_text("{}", encoding="utf-8")
    bundle = tmp_path / "e1.tar.gz"
    build_bundle(
        bundle, skill_dir=skill_src, job_dir=job_src, inputs=("input.json",), manifest=manifest
    )
    return bundle


class StubClient:
    def __init__(self, bundle: Path):
        self._bundle = bundle

    def download_bundle(self, claim: dict, dest: Path) -> None:
        dest.write_bytes(self._bundle.read_bytes())

    def heartbeat(self, execution_id: str) -> bool:
        return True


def _spec() -> dict:
    return {
        "version": 1,
        "prompt": (
            "Job ID: j1\n"
            "Working directory: {job_dir}\n"
            "Skill directory: {skill_dir}\n"
            "Session: {session_name}\n"
        ),
        "command": ["echo", "@{prompt_file}", "{session_name}", "{session_dir}"],
        "prompt_instruction": "Execute the attached node instructions.",
    }


def test_run_execution_uses_command_spec(tmp_path: Path):
    bundle = _make_bundle(tmp_path, MANIFEST)
    claim = {"execution_id": "e1", "command_spec": _spec()}

    metadata, archive = worker.run_execution(StubClient(bundle), claim, tmp_path / "work")

    assert metadata["status"] == "completed"
    assert archive is not None
    job_dir = tmp_path / "work" / "e1" / "job"
    run_dir = job_dir / "runs" / "gen" / "tok123"
    prompt_file = run_dir / "prompt.md"
    # The prompt file holds the spec prompt with every placeholder substituted.
    prompt_text = prompt_file.read_text(encoding="utf-8")
    assert f"Working directory: {job_dir}" in prompt_text
    assert "Session: j1:gen:tok123" in prompt_text
    assert "{job_dir}" not in prompt_text
    assert "{session_name}" not in prompt_text
    # Result metadata reports the real argv after substitution.
    expected_argv = ["echo", f"@{prompt_file}", "j1:gen:tok123", str(run_dir / "session")]
    assert metadata["command"] == expected_argv
    # The subprocess received the substituted argv (echo lands in events.jsonl).
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert " ".join(expected_argv[1:]) in events
    assert "{prompt_file}" not in events


def test_run_execution_without_command_spec_fails_claim(tmp_path: Path):
    bundle = _make_bundle(tmp_path, MANIFEST)

    class NoTouchClient(StubClient):
        def download_bundle(self, claim: dict, dest: Path) -> None:
            raise AssertionError("claim must be rejected before any download")

    # Old server (key absent) and null spec both hard-fail: no silent fallback.
    for claim in ({"execution_id": "e1"}, {"execution_id": "e1", "command_spec": None}):
        with pytest.raises(RuntimeError, match="command_spec"):
            worker.run_execution(NoTouchClient(bundle), claim, tmp_path / "work")
