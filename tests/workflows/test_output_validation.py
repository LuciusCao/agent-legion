"""Host-side worker output validation resolves the manifest's frozen skill (#76).

The validator script must come from the same (key, ref) pin the execution
used; legacy manifests without ``skill_ref`` resolve to ``latest`` (the
repo's live HEAD), matching the #322 dispatch semantics.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.app.skills.errors import SkillRepoError
from server.app.workflows.output_validation import validate_worker_outputs

pytestmark = pytest.mark.no_db

_KEY = "group/name"
_REF = "v1.2.3"


def _manager(tmp_path: Path, validator_body: str) -> MagicMock:
    manager = MagicMock()
    manager.base_dir = tmp_path / "skills"
    # The contract check reads the execution-private run dir (codex P1 on
    # PR 317) — laid out as <root>/<execution_id>/<group>/<name> — and the
    # validator itself runs from the same copy.
    run_dir = tmp_path / "runs" / "exec" / _KEY
    (run_dir / "references").mkdir(parents=True)
    (run_dir / "scripts").mkdir()
    (run_dir / "SKILL.md").write_text("# skill\n")
    (run_dir / "references" / "output-contract.md").write_text("contract\n")
    (run_dir / "scripts" / "validate_output.py").write_text(validator_body)
    manager.checkout_skill.return_value = (run_dir, "c" * 40, f"{_REF}@{'c' * 12}")
    manager.checkout_skill_commit.return_value = run_dir
    return manager


def test_validates_against_the_manifests_frozen_ref(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    manifest = {"skill": _KEY, "skill_ref": _REF}

    assert validate_worker_outputs(manager, manifest, job_dir) is None

    key, _execution_id, ref = manager.checkout_skill.call_args.args
    assert key == _KEY
    assert ref == _REF
    manager.cleanup_execution.assert_called_once()


def test_failing_validator_fails_the_node(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "import sys; sys.stderr.write('bad output\\n'); sys.exit(1)\n")

    error = validate_worker_outputs(manager, {"skill": _KEY, "skill_ref": _REF}, tmp_path / "job")

    assert error is not None
    assert "Output validation failed" in error
    assert "bad output" in error


def test_manifest_with_skill_commit_materializes_the_exact_commit(tmp_path: Path) -> None:
    """#330: the manifest's full skill_commit wins over skill_ref — no lock,
    no HEAD, no tag involved."""
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    commit = "d" * 40
    manifest = {"skill": _KEY, "skill_ref": "latest", "skill_commit": commit}

    assert validate_worker_outputs(manager, manifest, job_dir) is None

    key, _execution_id, exact = manager.checkout_skill_commit.call_args.args
    assert key == _KEY
    assert exact == commit
    manager.checkout_skill.assert_not_called()
    manager.cleanup_execution.assert_called_once()


def test_malformed_skill_commit_is_a_validator_error(tmp_path: Path) -> None:
    """A manifest commit that is not a 40-hex sha fails closed (the SkillRepoError
    rides the validator-error contract channel)."""
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")
    manager.checkout_skill_commit.side_effect = SkillRepoError(
        "skill commit must be a 40-hex sha: 'latest'"
    )

    error = validate_worker_outputs(
        manager, {"skill": _KEY, "skill_commit": "latest"}, tmp_path / "job"
    )

    assert error is not None
    assert error.startswith("Validator error:")
    assert "40-hex" in error


def test_exact_commit_missing_from_repo_is_a_validator_error(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")
    manager.checkout_skill_commit.side_effect = SkillRepoError(
        f"commit {'0' * 40!r} is missing from local skill repo"
    )

    error = validate_worker_outputs(
        manager, {"skill": _KEY, "skill_commit": "0" * 40}, tmp_path / "job"
    )

    assert error is not None
    assert error.startswith("Validator error:")
    assert "missing" in error


def test_exact_commit_validation_is_immune_to_head_moves(tmp_path: Path) -> None:
    """#330 end to end: the manifest records commit C1; HEAD then moves to a
    commit whose validator REJECTS. Validation by skill_commit still runs C1's
    validator (a legacy manifest without skill_commit picks up the new HEAD)."""
    from tests.helpers.skill_git import (
        _git,
        _head_commit,
        _make_skill_repo,
    )
    from tests.helpers.skill_git import (
        _make_manager as _make_real_manager,
    )

    repo = _make_skill_repo(
        tmp_path / "skills", "wf/review", validate_script="import sys; sys.exit(0)\n"
    )
    old_commit = _head_commit(repo)
    (repo / "scripts" / "validate_output.py").write_text(
        "import sys; sys.stderr.write('new rejection\\n'); sys.exit(1)\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v2", "--no-gpg-sign")
    manager = _make_real_manager(tmp_path)
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    exact = {"skill": "wf/review", "skill_ref": "latest", "skill_commit": old_commit}
    assert validate_worker_outputs(manager, exact, job_dir) is None

    legacy = {"skill": "wf/review", "skill_ref": "latest"}
    legacy_error = validate_worker_outputs(manager, legacy, job_dir)
    assert legacy_error is not None
    assert "new rejection" in legacy_error


def test_legacy_manifest_without_skill_ref_resolves_latest(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")

    assert validate_worker_outputs(manager, {"skill": _KEY}, tmp_path / "job") is None

    assert manager.checkout_skill.call_args.args[2] is None


def test_manifest_without_skill_skips_validation(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")

    assert validate_worker_outputs(manager, {}, tmp_path / "job") is None

    manager.checkout_skill.assert_not_called()
