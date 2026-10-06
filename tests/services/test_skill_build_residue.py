"""#1038: Python build residue never wedges skill / _shared authoring.

``__pycache__/`` and ``*.pyc`` (written by a local validator run) are
skipped by the shared editing export, carried over (never deleted) by the
full-state shared write, rejected inside a PUT payload with a "safe to
remove" hint, skipped by the committed-tree editing export, and ignored by
the save's dirty check while UNSTAGED — the commit still holds exactly the
declared files. Other binaries keep failing the export with a 422 that
says what to do.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from server.app.services.job_errors import ConflictError
from server.app.services.skill_commit_snapshot import commit_snapshot
from server.app.services.skill_creation import SkillCreationService
from server.app.services.skill_edit_snapshot import shared_edit_snapshot
from server.app.services.skill_editing import (
    SkillEditingService,
    SkillEditValidationError,
    SkillFileWrite,
)
from server.app.services.skill_shared_put import validate_shared_put_payload
from server.app.services.skill_shared_swap import write_shared_materials

pytestmark = pytest.mark.no_db

_PYC = b"\xcb\r\r\n\x00\x00\x00\x00binary-bytecode"
_MAP = {"version": 1, "materials": [{"source": "scripts/common.py", "skills": ["review"]}]}


def _shared(tmp_path: Path) -> Path:
    root = tmp_path / "ws" / "_shared"
    (root / "scripts" / "__pycache__").mkdir(parents=True)
    (root / "map.json").write_text(json.dumps(_MAP), encoding="utf-8")
    (root / "scripts" / "common.py").write_text("X = 1\n", encoding="utf-8")
    (root / "scripts" / "__pycache__" / "common.cpython-312.pyc").write_bytes(_PYC)
    (root / "scripts" / "stray.pyc").write_bytes(_PYC)
    return root


def test_shared_export_skips_build_residue(tmp_path: Path) -> None:
    files = shared_edit_snapshot(_shared(tmp_path))
    assert [item["path"] for item in files] == ["map.json", "scripts/common.py"]


def test_shared_export_still_rejects_other_binaries_with_guidance(tmp_path: Path) -> None:
    root = _shared(tmp_path)
    (root / "scripts" / "logo.txt").write_bytes(_PYC)
    with pytest.raises(SkillEditValidationError) as caught:
        shared_edit_snapshot(root)
    assert str(caught.value) == "Cannot export a lossless editing snapshot"
    [error] = caught.value.errors
    assert error["path"] == "scripts/logo.txt"
    assert "not UTF-8 text" in error["error"]
    assert "remove or convert this binary file" in error["error"]


def test_shared_put_payload_rejects_residue_with_remove_hint(tmp_path: Path) -> None:
    root = _shared(tmp_path)
    payload = [
        ("map.json", json.dumps(_MAP)),
        ("scripts/common.py", "X = 1\n"),
        ("scripts/__pycache__/common.cpython-312.pyc", "garbage"),
    ]
    with pytest.raises(SkillEditValidationError) as caught:
        validate_shared_put_payload(root, payload)
    [error] = caught.value.errors
    assert error["path"] == "scripts/__pycache__/common.cpython-312.pyc"
    assert "build residue" in error["error"] and "safe to remove" in error["error"]


def test_shared_round_trip_keeps_residue_on_disk(tmp_path: Path) -> None:
    root = _shared(tmp_path)
    (root / "references" / "old" / "__pycache__").mkdir(parents=True)
    (root / "references" / "old" / "gone.md").write_text("dropped\n", encoding="utf-8")
    (root / "references" / "old" / "__pycache__" / "x.pyc").write_bytes(_PYC)
    exported = [
        (item["path"], item["content"])
        for item in shared_edit_snapshot(root)
        if not item["path"].startswith("references/old/")
    ]
    exported = [
        (path, "X = 2\n" if path == "scripts/common.py" else content) for path, content in exported
    ]
    targets = validate_shared_put_payload(root, exported)
    write_shared_materials(root, list(targets.items()), tmp_path)

    assert (root / "scripts" / "common.py").read_text(encoding="utf-8") == "X = 2\n"
    # Residue the export skipped is not "deleted by omission"...
    assert (root / "scripts" / "__pycache__" / "common.cpython-312.pyc").read_bytes() == _PYC
    assert (root / "scripts" / "stray.pyc").read_bytes() == _PYC
    # ...but a dropped directory takes its residue with it.
    assert not (root / "references" / "old").exists()
    assert not list(root.parent.glob("_shared.*"))


def test_shared_round_trip_never_follows_residue_symlinks(tmp_path: Path) -> None:
    root = _shared(tmp_path)
    outside = tmp_path / "outside.pyc"
    outside.write_bytes(b"host secret")
    (root / "scripts" / "linked.pyc").symlink_to(outside)
    targets = validate_shared_put_payload(
        root, [(item["path"], item["content"]) for item in shared_edit_snapshot(root)]
    )
    write_shared_materials(root, list(targets.items()), tmp_path)
    assert not os.path.lexists(root / "scripts" / "linked.pyc")
    assert outside.read_bytes() == b"host secret"


# --- skill repo: dirty check, committed export, creation -------------------


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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A legacy skill repo (no .gitignore) whose bytecode cache is TRACKED."""
    repo = tmp_path / "skills" / "wf" / "review"
    (repo / "references").mkdir(parents=True)
    (repo / "scripts" / "__pycache__").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "SKILL.md").write_text("# Review\n", encoding="utf-8")
    (repo / "references" / "output-contract.md").write_text("# contract\n", encoding="utf-8")
    (repo / "scripts" / "validate_output.py").write_text("raise SystemExit(0)\n")
    (repo / "scripts" / "__pycache__" / "common.cpython-312.pyc").write_bytes(_PYC)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    _git(repo, "tag", "v1.0.0")
    return repo


def _service(repo: Path, tmp_path: Path) -> SkillEditingService:
    return SkillEditingService(base_dir=repo.parents[1], runs_dir=tmp_path / "runs")


def _validator_run(repo: Path) -> bytes:
    """What a local validator run leaves behind: rewritten tracked bytecode
    plus a brand-new untracked cache file."""
    rewritten = _PYC + b"-recompiled"
    (repo / "scripts" / "__pycache__" / "common.cpython-312.pyc").write_bytes(rewritten)
    (repo / "scripts" / "__pycache__" / "extra.cpython-312.pyc").write_bytes(_PYC)
    return rewritten


def test_save_ignores_unstaged_residue_and_commits_only_declared(
    repo: Path, tmp_path: Path
) -> None:
    rewritten = _validator_run(repo)
    result = _service(repo, tmp_path).save_version(
        "wf/review", [SkillFileWrite("SKILL.md", "# Review v2\n")], "v1.0.1", "bump"
    )
    assert result["files"] == ["SKILL.md"]
    changed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert changed == ["SKILL.md"]
    # The residue is untouched on disk and still NOT in the index.
    tracked = repo / "scripts" / "__pycache__" / "common.cpython-312.pyc"
    assert tracked.read_bytes() == rewritten
    assert (repo / "scripts" / "__pycache__" / "extra.cpython-312.pyc").exists()
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_save_still_refuses_staged_residue_and_other_dirt(repo: Path, tmp_path: Path) -> None:
    service = _service(repo, tmp_path)
    _validator_run(repo)
    (repo / "notes.md").write_text("wip\n", encoding="utf-8")
    with pytest.raises(ConflictError, match="uncommitted changes"):
        service.save_version("wf/review", [SkillFileWrite("SKILL.md", "x\n")], "v1.0.1", "m")
    (repo / "notes.md").unlink()
    _git(repo, "add", "scripts/__pycache__/common.cpython-312.pyc")
    with pytest.raises(ConflictError, match="uncommitted changes"):
        service.save_version("wf/review", [SkillFileWrite("SKILL.md", "x\n")], "v1.0.1", "m")


def test_committed_export_skips_tracked_residue(repo: Path) -> None:
    files = commit_snapshot(repo, _git(repo, "rev-parse", "HEAD"))
    assert [item["path"] for item in files] == [
        "SKILL.md",
        "references/output-contract.md",
        "scripts/validate_output.py",
    ]


class _FakeJobDB:
    def get_workspace(self, workspace_id: str):
        return {"id": workspace_id}


_QUARTET = [
    SkillFileWrite("SKILL.md", "# Skill\n"),
    SkillFileWrite("references/output-contract.md", "# contract\n"),
    SkillFileWrite("scripts/validate_output.py", "raise SystemExit(0)\n"),
    SkillFileWrite("contract.yaml", "files:\n  - path: out.md\n    format: text\n"),
]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home" / ".agents" / "skills"
    base.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return base


def test_created_skill_is_born_ignoring_residue(home: Path, tmp_path: Path) -> None:
    service = SkillCreationService(_FakeJobDB(), runs_dir=tmp_path / "runs")
    service.create_skill("ws-1", "fresh", list(_QUARTET), "v0.1.0", "init")
    repo = home / "ws-1" / "fresh"
    assert (repo / ".gitignore").read_text() == "__pycache__/\n*.pyc\n"
    assert ".gitignore" in _git(repo, "ls-files").splitlines()
    (repo / "scripts" / "__pycache__").mkdir()
    (repo / "scripts" / "__pycache__" / "v.cpython-312.pyc").write_bytes(_PYC)
    assert _git(repo, "status", "--porcelain") == ""


def test_created_skill_keeps_a_declared_gitignore(home: Path, tmp_path: Path) -> None:
    service = SkillCreationService(_FakeJobDB(), runs_dir=tmp_path / "runs")
    files = [*_QUARTET, SkillFileWrite(".gitignore", "out/\n")]
    service.create_skill("ws-1", "own", files, "v0.1.0", "init")
    assert (home / "ws-1" / "own" / ".gitignore").read_text() == "out/\n"


def test_residue_vanishing_mid_copy_is_skipped_other_errors_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.app.services import skill_build_residue_io
    from server.app.services.skill_shared_store import SharedMaterialWriteError

    root = _shared(tmp_path)
    targets = validate_shared_put_payload(
        root, [(item["path"], item["content"]) for item in shared_edit_snapshot(root)]
    )
    real_copy = skill_build_residue_io.shutil.copy2

    def vanishing_copy(source, target, **kwargs):
        if Path(source).name == "stray.pyc":
            Path(source).unlink()  # a validator outside the lock cleaned it
        return real_copy(source, target, **kwargs)

    monkeypatch.setattr(skill_build_residue_io.shutil, "copy2", vanishing_copy)
    write_shared_materials(root, list(targets.items()), tmp_path)
    assert not (root / "scripts" / "stray.pyc").exists()
    assert (root / "scripts" / "__pycache__" / "common.cpython-312.pyc").read_bytes() == _PYC

    def denied_copy(source, target, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(skill_build_residue_io.shutil, "copy2", denied_copy)
    with pytest.raises(SharedMaterialWriteError):
        write_shared_materials(root, [*targets.items(), ("scripts/new.py", "Y\n")], tmp_path)
    assert not (root / "scripts" / "new.py").exists()  # live dir untouched


def test_undecodable_status_output_is_a_conflict_not_a_crash(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``-z`` prints raw path bytes; a non-UTF-8 file name (Linux) breaks the
    runner's strict text decode — still the base behavior's 409."""
    real_git = SkillEditingService.__dict__["_git"].__func__

    def git(repo_dir: Path, args: list[str], *, check: bool = True):
        if args[:1] == ["status"]:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return real_git(repo_dir, args, check=check)

    monkeypatch.setattr(SkillEditingService, "_git", staticmethod(git))
    with pytest.raises(ConflictError, match="uncommitted changes"):
        _service(repo, tmp_path).save_version(
            "wf/review", [SkillFileWrite("SKILL.md", "x\n")], "v1.0.1", "m"
        )


@pytest.mark.parametrize("residue", ["references/data.pyc", "scripts/__pycache__/helper.py"])
def test_create_rejects_residue_paths_before_any_write(
    home: Path, tmp_path: Path, residue: str
) -> None:
    service = SkillCreationService(_FakeJobDB(), runs_dir=tmp_path / "runs")
    with pytest.raises(SkillEditValidationError) as caught:
        service.create_skill(
            "ws-1", "dirty", [*_QUARTET, SkillFileWrite(residue, "x\n")], "v0.1.0", "init"
        )
    [error] = caught.value.errors
    assert error["path"] == residue
    assert "build residue" in error["error"] and "safe to remove" in error["error"]
    assert not (home / "ws-1" / "dirty").exists()
