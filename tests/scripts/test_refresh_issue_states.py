"""Tests for the issue-state cache refresh script (gh is faked)."""

from __future__ import annotations

import json
import os
import stat
import textwrap
from pathlib import Path

import pytest

from scripts.refresh_issue_states import (
    collect_issue_references,
    fetch_issue_states,
    write_manifest,
)

pytestmark = pytest.mark.no_db


@pytest.fixture
def registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "config" / "architecture").mkdir(parents=True)
    (root / "config" / "architecture" / "architecture-exemptions.yaml").write_text(
        textwrap.dedent(
            """\
            exemptions:
            - check: architecture.file_budget
              path: server/app/example.py
              reason: Oversized module needs staged split.
              owner: agent-legion
              remove_when: issues/open/github.com/LuciusCao/agent-legion/issues/195
              ceiling: 100
            - check: architecture.file_budget
              path: server/app/other.py
              reason: Same split batch, second file.
              owner: agent-legion
              remove_when: issues/open/github.com/LuciusCao/agent-legion/issues/195
              ceiling: 80
            - check: architecture.file_budget
              path: server/app/third.py
              reason: Different repository anchor.
              owner: agent-legion
              remove_when: issues/closed/github.com/example/other-repo/issues/7
              ceiling: 90
            - check: architecture.route_response_model
              path: server/app/routes/example.py:handler
              reason: Streaming response has no JSON model.
              owner: workspace-executor
              remove_when: docs/architecture/tracked-plan.md
            """
        ),
        encoding="utf-8",
    )
    return root


def test_collect_issue_references_groups_by_repository(registry_root: Path) -> None:
    references = collect_issue_references(
        registry_root / "config" / "architecture" / "architecture-exemptions.yaml"
    )
    assert references == {
        "LuciusCao/agent-legion": {195},
        "example/other-repo": {7},
    }


def test_collect_issue_references_empty_registry(tmp_path: Path) -> None:
    registry = tmp_path / "architecture-exemptions.yaml"
    registry.write_text("exemptions: []\n", encoding="utf-8")
    assert collect_issue_references(registry) == {}


def test_fetch_issue_states_one_call_per_repo(
    registry_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    responses: dict[str, list[dict[str, object]]] = {
        "LuciusCao/agent-legion": [
            {"number": 195, "state": "CLOSED"},
            {"number": 276, "state": "OPEN"},  # not referenced; must not land
        ],
        "example/other-repo": [{"number": 7, "state": "OPEN"}],
    }

    def fake_run(argv: list[str], **_kwargs: object) -> object:
        calls.append(argv)
        repo = argv[argv.index("--repo") + 1]

        class Result:
            returncode = 0
            stdout = json.dumps(responses[repo])
            stderr = ""

        return Result()

    monkeypatch.setattr("scripts.refresh_issue_states.subprocess.run", fake_run)
    references = collect_issue_references(
        registry_root / "config" / "architecture" / "architecture-exemptions.yaml"
    )
    states = fetch_issue_states(references)

    assert states == {
        "github.com/LuciusCao/agent-legion/issues/195": "closed",
        "github.com/example/other-repo/issues/7": "open",
    }
    assert len(calls) == 2
    assert {c[c.index("--repo") + 1] for c in calls} == set(responses)


def test_fetch_issue_states_gh_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **_kwargs: object) -> object:
        class Result:
            returncode = 1
            stdout = ""
            stderr = "gh: auth required"

        return Result()

    monkeypatch.setattr("scripts.refresh_issue_states.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="gh issue list failed"):
        fetch_issue_states({"example/repo": {1}})


def test_write_manifest_is_deterministic_and_loadable(registry_root: Path) -> None:
    manifest = write_manifest(
        registry_root,
        {"github.com/example/other-repo/issues/7": "open"},
    )
    assert manifest == registry_root / "config" / "architecture" / "issue-states.json"
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["issues"] == {"github.com/example/other-repo/issues/7": "open"}
    assert "updated_at" in raw


def test_write_manifest_replaces_existing_file(registry_root: Path) -> None:
    write_manifest(registry_root, {"github.com/example/other-repo/issues/7": "open"})
    write_manifest(registry_root, {})
    raw = json.loads(
        (registry_root / "config" / "architecture" / "issue-states.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["issues"] == {}


def test_main_writes_manifest_and_exits_zero(
    registry_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end main() against a faked gh binary on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            case "$*" in
              *LuciusCao/agent-legion*)
                echo '[{"number":195,"state":"closed"},{"number":276,"state":"open"}]' ;;
              *)
                echo '[{"number":7,"state":"open"}]' ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    from scripts.refresh_issue_states import main

    assert main(["--root", str(registry_root)]) == 0
    manifest = registry_root / "config" / "architecture" / "issue-states.json"
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    assert raw["issues"] == {
        "github.com/LuciusCao/agent-legion/issues/195": "closed",
        "github.com/example/other-repo/issues/7": "open",
    }
    # The closed-anchor warning is WARNING-level; the INFO summary line is
    # covered by the manifest contents and exit code above.
    assert "closed: github.com/LuciusCao/agent-legion/issues/195" in caplog.text


def test_main_gh_failure_exits_non_zero(
    registry_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho 'gh: auth required' >&2\nexit 3\n", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    from scripts.refresh_issue_states import main

    assert main(["--root", str(registry_root)]) == 1
    assert not (registry_root / "config" / "architecture" / "issue-states.json").exists()
    assert "gh issue list failed" in caplog.text
