"""Host-side worker output validation resolves the manifest's frozen skill (#76).

The validator script must come from the same (key, ref) pin the execution
used; legacy manifests without ``skill_ref`` fall back to the source default
ref, exactly as before.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.app.workflows.output_validation import validate_worker_outputs

pytestmark = pytest.mark.no_db

_KEY = "group/name"
_REF = "v1.2.3"


def _manager(tmp_path: Path, validator_body: str) -> MagicMock:
    manager = MagicMock()
    manager.base_dir = tmp_path / "skills"
    # The contract check (resolve_workflow_skill) reads the shared cache dir;
    # the validator itself runs from the execution-private run dir.
    cache_dir = manager.base_dir / _KEY
    (cache_dir / "references").mkdir(parents=True)
    (cache_dir / "scripts").mkdir()
    (cache_dir / "SKILL.md").write_text("# skill\n")
    (cache_dir / "references" / "output-contract.md").write_text("contract\n")
    (cache_dir / "scripts" / "validate_output.py").write_text("print('ok')\n")
    run_dir = tmp_path / "run"
    (run_dir / "scripts").mkdir(parents=True)
    (run_dir / "scripts" / "validate_output.py").write_text(validator_body)
    manager.checkout_skill.return_value = (run_dir, "c" * 40, f"{_REF}@{'c' * 12}")
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


def test_legacy_manifest_without_skill_ref_uses_the_source_default(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")

    assert validate_worker_outputs(manager, {"skill": _KEY}, tmp_path / "job") is None

    assert manager.checkout_skill.call_args.args[2] is None


def test_manifest_without_skill_skips_validation(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")

    assert validate_worker_outputs(manager, {}, tmp_path / "job") is None

    manager.checkout_skill.assert_not_called()
