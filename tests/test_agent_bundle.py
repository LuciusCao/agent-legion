from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from server.app.agent_bundle import AgentBundleError, build_agent_bundle, extract_agent_result
from server.app.agent_completion import _unpack_result


def test_agent_bundle_contains_manifest_and_skill_snapshot(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Test", encoding="utf-8")
    archive = tmp_path / "bundle.tar.gz"

    build_agent_bundle(archive, skill_dir=skill, manifest={"agent_id": "generator-v1"})

    with tarfile.open(archive, "r:gz") as tar:
        assert {member.name for member in tar.getmembers()} >= {
            "manifest.json",
            "skill/SKILL.md",
        }


def test_agent_result_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "result.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"escape"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(AgentBundleError, match="unsafe path"):
        extract_agent_result(archive, tmp_path / "job")


def _write_result_archive(archive: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))


def test_result_unpack_promotes_only_expected_outputs(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    archive = tmp_path / "result.tar.gz"
    _write_result_archive(
        archive,
        {
            "expected.json": b"{}",
            "evil.txt": b"clobber",
            "runs/generate/worker/events.jsonl": b"{}",
        },
    )

    _unpack_result(archive, job_dir, ("expected.json",))

    # Only the declared output is promoted; worker run logs (events.jsonl) and
    # undeclared files never land in the job dir, and staging leaves nothing.
    assert (job_dir / "expected.json").is_file()
    assert [path.name for path in job_dir.iterdir()] == ["expected.json"]
