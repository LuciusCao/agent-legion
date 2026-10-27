from __future__ import annotations

from pathlib import Path

import pytest

from scripts.e2e.host_skill_checkout_smoke import prepare_workspace, run_smoke

pytestmark = pytest.mark.no_db


def test_smoke_exercises_latest_first_pin_and_relocked_checkouts(tmp_path: Path) -> None:
    commits = prepare_workspace(tmp_path)

    assert run_smoke(tmp_path) == commits
    assert commits["pinned_commit"] != commits["latest_commit"]
    run_dirs = sorted((tmp_path / "runs").glob("*/host_smoke/skill"))
    assert [path.parent.parent.name for path in run_dirs] == [
        "latest",
        "pinned-first",
        "pinned-relocked",
    ]


def test_smoke_fails_at_real_checkout_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_workspace(tmp_path)
    monkeypatch.setenv("PATH", "")

    with pytest.raises(FileNotFoundError, match="git"):
        run_smoke(tmp_path)

    assert not (tmp_path / "runs" / "latest").exists()


def test_docker_build_loads_and_runs_host_smoke() -> None:
    workflow = (Path(__file__).parents[2] / ".github/workflows/quality-gate.yml").read_text()
    docker_lane = workflow.split("  docker-build:\n", 1)[1].split("\n  quality-gate:\n", 1)[0]
    host_build = docker_lane.split("      - name: Build host image\n", 1)[1].split(
        "      - name: Prepare Host skill checkout smoke\n", 1
    )[0]

    assert "load: true" in host_build
    assert "tags: agent-legion-host:ci" in host_build
    assert (
        'docker run --rm --network none -v "$RUNNER_TEMP/host-skill-checkout-smoke:/smoke" '
        "agent-legion-host:ci python -m scripts.e2e.host_skill_checkout_smoke run "
        "--workspace /smoke"
    ) in docker_lane


def test_only_host_stage_installs_runtime_git() -> None:
    dockerfile = (Path(__file__).parents[2] / "Dockerfile").read_text()
    host_stage = dockerfile.split(" AS host\n", 1)[1].split("\nFROM ", 1)[0]
    worker_stage = dockerfile.split(" AS worker\n", 1)[1]

    assert "apt-get install -y --no-install-recommends g++ ffmpeg git" in host_stage
    assert "apt-get install -y --no-install-recommends g++ ffmpeg git" not in worker_stage
