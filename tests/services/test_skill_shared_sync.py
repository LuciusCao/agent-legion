"""Shared-material sync planning + the save_version integration (#633).

Service-level: the map.json schema, the no-_shared no-op, the mapped
material landing in the committed tree at the new tag, per-material skill
selection, and every pre-write rejection (conflicting hand-supplied path,
malformed map, missing source). The sync rides the EXISTING save pipeline,
so rollback and contract behavior come from test_skill_editing.py.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from server.app.services.skill_editing import (
    SkillEditingService,
    SkillEditValidationError,
    SkillFileWrite,
)
from server.app.services.skill_shared_sync import (
    load_shared_map,
    plan_shared_sync,
    validate_materials,
)

pytestmark = pytest.mark.no_db

_KEY = "wf/review"
_OTHER_KEY = "wf/summary"
_BASE = Path("skills") / "wf"

_MAP = {
    "version": 1,
    "materials": [
        {"source": "references/prompt-style.md", "skills": ["review", "summary"]},
        {"source": "scripts/normalize.py", "skills": ["summary"]},
    ],
}


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


def _make_repo(repo: Path, tag: str = "v1.0.0") -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "SKILL.md").write_text("# Review\n", encoding="utf-8")
    (repo / "references").mkdir()
    (repo / "references" / "output-contract.md").write_text("# contract\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "validate_output.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    _git(repo, "tag", tag)


def _write_map(base_dir: Path, materials: object) -> Path:
    shared = base_dir / "wf" / "_shared"
    (shared / "references").mkdir(parents=True)
    (shared / "scripts").mkdir()
    (shared / "map.json").write_text(json.dumps({"version": 1, "materials": materials}))
    return shared


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    base = tmp_path / "skills"
    _make_repo(base / "wf" / "review")
    _make_repo(base / "wf" / "summary")
    return base


@pytest.fixture
def service(base_dir: Path, tmp_path: Path) -> SkillEditingService:
    return SkillEditingService(base_dir=base_dir, runs_dir=tmp_path / "runs")


def test_no_shared_dir_is_a_noop(service: SkillEditingService, base_dir: Path) -> None:
    result = service.save_version(_KEY, [SkillFileWrite("SKILL.md", "# v2\n")], "v2.0.0", "m")
    assert result["synced_files"] == []
    assert result["files"] == ["SKILL.md"]


def test_mapped_material_lands_in_the_commit_and_response(
    service: SkillEditingService, base_dir: Path
) -> None:
    shared = _write_map(base_dir, _MAP["materials"])
    (shared / "references" / "prompt-style.md").write_text("# house style\n")
    result = service.save_version(_KEY, [SkillFileWrite("SKILL.md", "# v2\n")], "v2.0.0", "m")
    assert result["synced_files"] == ["references/prompt-style.md"]
    assert "references/prompt-style.md" in result["files"]
    # The file is IN the tagged commit, not just the working tree.
    at_tag = _git(base_dir / "wf" / "review", "show", "v2.0.0:references/prompt-style.md")
    assert at_tag == "# house style"


def test_sync_selects_only_materials_mapped_to_this_skill(
    service: SkillEditingService, base_dir: Path
) -> None:
    shared = _write_map(base_dir, _MAP["materials"])
    (shared / "references" / "prompt-style.md").write_text("# style\n")
    (shared / "scripts" / "normalize.py").write_text("def norm():\n    pass\n")
    # review is mapped to prompt-style only; summary to both.
    review = service.save_version(_KEY, [SkillFileWrite("SKILL.md", "# v2\n")], "v2.0.0", "m")
    assert review["synced_files"] == ["references/prompt-style.md"]
    summary = service.save_version(
        _OTHER_KEY, [SkillFileWrite("SKILL.md", "# s2\n")], "v2.0.0", "m"
    )
    assert summary["synced_files"] == [
        "references/prompt-style.md",
        "scripts/normalize.py",
    ]


def test_hand_supplied_mapped_path_is_rejected(
    service: SkillEditingService, base_dir: Path
) -> None:
    shared = _write_map(base_dir, _MAP["materials"])
    (shared / "references" / "prompt-style.md").write_text("# shared\n")
    with pytest.raises(SkillEditValidationError) as excinfo:
        service.save_version(
            _KEY,
            [
                SkillFileWrite("SKILL.md", "# v2\n"),
                SkillFileWrite("references/prompt-style.md", "# stale hand copy\n"),
            ],
            "v2.0.0",
            "m",
        )
    assert excinfo.value.errors == [
        {
            "path": "references/prompt-style.md",
            "error": "path is a mapped shared material (the shared copy wins); "
            "remove it from the save payload",
        }
    ]
    # Nothing was written: HEAD and tree unchanged.
    repo = base_dir / "wf" / "review"
    assert _git(repo, "tag", "--list") == "v1.0.0"
    assert _git(repo, "status", "--porcelain") == ""


def test_malformed_map_fails_the_save(service: SkillEditingService, base_dir: Path) -> None:
    shared = base_dir / "wf" / "_shared"
    shared.mkdir(parents=True)
    (shared / "map.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SkillEditValidationError, match="Invalid shared materials map"):
        service.save_version(_KEY, [SkillFileWrite("SKILL.md", "# v2\n")], "v2.0.0", "m")


def test_missing_source_file_fails_the_save(service: SkillEditingService, base_dir: Path) -> None:
    _write_map(base_dir, _MAP["materials"])  # prompt-style.md never written
    with pytest.raises(SkillEditValidationError) as excinfo:
        service.save_version(_KEY, [SkillFileWrite("SKILL.md", "# v2\n")], "v2.0.0", "m")
    assert excinfo.value.errors[0]["path"] == "references/prompt-style.md"
    assert "unreadable" in excinfo.value.errors[0]["error"]


def test_shared_dir_without_map_is_an_error(base_dir: Path) -> None:
    shared = base_dir / "wf" / "_shared"
    shared.mkdir(parents=True)
    with pytest.raises(SkillEditValidationError) as excinfo:
        load_shared_map(shared)
    assert excinfo.value.errors[0]["error"] == "map.json is missing"


@pytest.mark.parametrize(
    "materials",
    [
        "not-a-list",
        [{"source": "references/ok.md", "skills": []}],
        [{"source": "docs/style.md", "skills": ["review"]}],  # outside material dirs
        [{"source": "../escape.md", "skills": ["review"]}],
        [{"source": "/abs/style.md", "skills": ["review"]}],
        [{"source": "references/.git/hooks/x.md", "skills": ["review"]}],
        [{"source": "references/a.md", "skills": ["review", "Bad Name"]}],
        [
            {"source": "references/a.md", "skills": ["review"]},
            {"source": "references/a.md", "skills": ["s"]},
        ],
    ],
)
def test_validate_materials_rejects_bad_shapes(materials: object) -> None:
    from server.app.services.skill_repo_edit import SkillEditValidationError as Err

    with pytest.raises(Err):
        validate_materials(materials)


def test_validate_materials_accepts_the_contract_shape() -> None:
    materials = validate_materials(_MAP["materials"])
    assert [m.source for m in materials] == [
        "references/prompt-style.md",
        "scripts/normalize.py",
    ]


def test_plan_shared_sync_noop_without_shared(tmp_path: Path) -> None:
    base = tmp_path / "skills"
    _make_repo(base / "wf" / "review")
    plan = plan_shared_sync(base, _KEY, [("SKILL.md", "x")])
    assert plan.files == ()
