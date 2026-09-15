"""Shared-material propagation service (issue #673).

Per-skill outcomes against real git repos under a monkeypatched HOME:
synced (new patch tag + flipped drift), skipped (already in sync, repo
missing), failed (unreadable shared source, dirty tree), tag increments
(v1.2.3 → v1.2.4, no tags → v0.1.0, non-semver tags ignored), the
sources filter selecting skills, and batch isolation (one failure does
not strand the other skills).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_propagate import (
    next_version_tag,
    propagate_shared_materials,
)

_WS = "propagate-ws"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _make_skill_repo(repo: Path, files: dict[str, str], tags: tuple[str, ...] = ()) -> None:
    # The trio save_version's post-write contract check enforces (#542);
    # a missing root contract.yaml is a warning only.
    trio = {
        "SKILL.md": "# Skill\n",
        "references/output-contract.md": "# contract\n",
        "scripts/validate_output.py": "raise SystemExit(0)\n",
    }
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for rel, content in {**trio, **files}.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    for tag in tags:
        _git(repo, "tag", tag)


@pytest.fixture
def ws_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    base = home / ".agents" / "skills"
    (base / _WS).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return base


def _seed_shared(
    base: Path,
    materials: list[dict],
    files: dict[str, str],
) -> Path:
    shared = base / _WS / "_shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "map.json").write_text(
        json.dumps({"version": 1, "materials": materials}), encoding="utf-8"
    )
    for rel, content in files.items():
        target = shared / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return shared


def test_next_version_tag_rules() -> None:
    assert next_version_tag(()) == "v0.1.0"
    assert next_version_tag(("v1.2.3",)) == "v1.2.4"
    # Highest parseable wins regardless of input order; non-semver ignored.
    assert next_version_tag(("draft", "v1.10.0", "v1.2.9")) == "v1.10.1"
    assert next_version_tag(("not-a-version",)) == "v0.1.0"


def test_propagate_syncs_flips_drift_and_bumps_patch_tag(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["write-script"]}],
        {"references/style.md": "# v2\n"},
    )
    repo = ws_dir / _WS / "write-script"
    _make_skill_repo(repo, {"references/style.md": "# v1\n"}, tags=("v1.2.3",))

    result = propagate_shared_materials(_WS)
    (entry,) = result.results
    assert entry.status == "synced"
    assert entry.tag == "v1.2.4"
    assert entry.synced_files == ("references/style.md",)
    # The repo HEAD now carries the shared copy, committed + tagged.
    assert _git(repo, "show", "HEAD:references/style.md") == "# v2"
    assert "v1.2.4" in _git(repo, "tag", "--list")
    assert "Sync shared materials" in _git(repo, "log", "-1", "--pretty=%s")


def test_propagate_no_tags_starts_at_initial_version(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["fresh-skill"]}],
        {"references/style.md": "# v1\n"},
    )
    _make_skill_repo(ws_dir / _WS / "fresh-skill", {"SKILL.md": "# s\n"})

    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "synced"
    assert entry.tag == "v0.1.0"


def test_propagate_skips_already_synced_and_missing_repos(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["synced-skill", "ghost-skill"]}],
        {"references/style.md": "# same\n"},
    )
    _make_skill_repo(
        ws_dir / _WS / "synced-skill", {"references/style.md": "# same\n"}, tags=("v1.0.0",)
    )
    # ghost-skill: no repo directory.

    result = propagate_shared_materials(_WS)
    by_skill = {r.skill: r for r in result.results}
    assert by_skill["synced-skill"].status == "skipped"
    assert by_skill["synced-skill"].detail == "already in sync"
    assert by_skill["ghost-skill"].status == "skipped"
    assert by_skill["ghost-skill"].detail == "skill repo not found"
    # No new tag was created for the already-synced skill.
    assert _git(ws_dir / _WS / "synced-skill", "tag", "--list") == "v1.0.0"


def test_propagate_isolates_failures_per_skill(ws_dir) -> None:
    """A dirty tree fails one skill; the other still propagates."""
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["dirty-skill", "clean-skill"]}],
        {"references/style.md": "# v2\n"},
    )
    dirty = ws_dir / _WS / "dirty-skill"
    _make_skill_repo(dirty, {"references/style.md": "# v1\n"}, tags=("v1.0.0",))
    (dirty / "uncommitted.md").write_text("dirty\n", encoding="utf-8")
    _make_skill_repo(
        ws_dir / _WS / "clean-skill", {"references/style.md": "# v1\n"}, tags=("v1.0.0",)
    )

    result = propagate_shared_materials(_WS)
    by_skill = {r.skill: r for r in result.results}
    assert by_skill["dirty-skill"].status == "failed"
    assert "uncommitted" in (by_skill["dirty-skill"].detail or "")
    assert by_skill["clean-skill"].status == "synced"
    assert by_skill["clean-skill"].tag == "v1.0.1"


def test_propagate_reports_unreadable_shared_source(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/gone.md", "skills": ["some-skill"]}],
        {"references/other.md": "# present\n"},
    )
    _make_skill_repo(ws_dir / _WS / "some-skill", {"SKILL.md": "# s\n"})

    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "failed"
    assert "unreadable" in (entry.detail or "")


def test_propagate_sources_filter_selects_skills(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [
            {"source": "references/a.md", "skills": ["skill-a"]},
            {"source": "references/b.md", "skills": ["skill-b"]},
        ],
        {"references/a.md": "# a2\n", "references/b.md": "# b2\n"},
    )
    _make_skill_repo(ws_dir / _WS / "skill-a", {"references/a.md": "# a1\n"})
    _make_skill_repo(ws_dir / _WS / "skill-b", {"references/b.md": "# b1\n"})

    result = propagate_shared_materials(_WS, ["references/a.md"])
    assert [r.skill for r in result.results] == ["skill-a"]
    assert result.results[0].status == "synced"
    # skill-b untouched: still on the old copy, no new tag.
    assert _git(ws_dir / _WS / "skill-b", "show", "HEAD:references/b.md") == "# b1"


def test_propagate_whole_mapped_set_even_when_filtered(ws_dir) -> None:
    """save_version syncs the skill's WHOLE mapped set: requesting one
    source also lands the skill's other mapped materials."""
    _seed_shared(
        ws_dir,
        [
            {"source": "references/a.md", "skills": ["multi-skill"]},
            {"source": "references/b.md", "skills": ["multi-skill"]},
        ],
        {"references/a.md": "# a2\n", "references/b.md": "# b2\n"},
    )
    repo = ws_dir / _WS / "multi-skill"
    _make_skill_repo(repo, {"references/a.md": "# a1\n", "references/b.md": "# b1\n"})

    (entry,) = propagate_shared_materials(_WS, ["references/a.md"]).results
    assert entry.status == "synced"
    assert set(entry.synced_files) == {"references/a.md", "references/b.md"}
    assert _git(repo, "show", "HEAD:references/b.md") == "# b2"


def test_propagate_unknown_source_is_422_and_writes_nothing(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/a.md", "skills": ["skill-a"]}],
        {"references/a.md": "# a2\n"},
    )
    repo = ws_dir / _WS / "skill-a"
    _make_skill_repo(repo, {"references/a.md": "# a1\n"})

    with pytest.raises(SkillEditValidationError):
        propagate_shared_materials(_WS, ["references/nope.md"])
    assert _git(repo, "show", "HEAD:references/a.md") == "# a1"


def test_propagate_without_shared_dir_is_404(ws_dir) -> None:
    with pytest.raises(NotFoundError):
        propagate_shared_materials(_WS)
