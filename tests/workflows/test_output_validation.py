"""Host-side worker output validation resolves the manifest's frozen skill (#76).

The validator script must come from the same (key, ref) pin the execution
used; legacy manifests without ``skill_ref`` resolve to ``latest`` (the
repo's live HEAD), matching the #322 dispatch semantics.

Since #443 the contract engine (``velites-sandbox validate``) runs first and
fails fast on generic contract violations; the legacy ``validate_output.py``
still runs afterwards for business rules the engine does not express.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import server.app.workflows.output_contract_engine as output_contract_engine
from server.app.skills.errors import SkillRepoError
from server.app.workflows.output_contract_engine import run_contract_engine
from server.app.workflows.output_validation import run_output_validator
from server.app.workflows.worker_output_validation import validate_worker_outputs

pytestmark = pytest.mark.no_db

_KEY = "group/name"
_REF = "v1.2.3"


@pytest.fixture(autouse=True)
def _no_real_engine_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic default: no velites binary — the legacy script path alone
    decides, whatever happens to be on this machine's PATH. Engine-layer
    tests override this via ``_use_engine``."""
    monkeypatch.setattr(output_contract_engine, "resolve_sandbox_binary", lambda: None)


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


# --- #443: contract engine layer (velites-sandbox validate) ---

# A declaring document: prose around the machine-readable block, exactly the
# shape velites's first-fence scan expects (#538 probe tests vary the doc).
_CONTRACT_BLOCK_DOC = (
    "# Output contract\n\n```yaml contract\nfiles:\n  - path: script.md\n    format: text\n```\n"
)


def _skill_dir(tmp_path: Path, legacy_body: str | None, doc: str = _CONTRACT_BLOCK_DOC) -> Path:
    skill_dir = tmp_path / "skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "output-contract.md").write_text(doc)
    if legacy_body is not None:
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "validate_output.py").write_text(legacy_body)
    return skill_dir


def _fake_engine(tmp_path: Path, *, stdout: str = "", stderr: str = "", rc: int = 0) -> str:
    script = tmp_path / "fake-engine.sh"
    script.write_text(
        f"#!/bin/sh\nprintf '%s' '{stdout}'\nprintf '%s' '{stderr}' 1>&2\nexit {rc}\n"
    )
    script.chmod(0o755)
    return str(script)


def _use_engine(monkeypatch: pytest.MonkeyPatch, binary: str | None) -> None:
    monkeypatch.setattr(output_contract_engine, "resolve_sandbox_binary", lambda: binary)


def _record_engine_spawns(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Intercept the engine's subprocess.run; the legacy script stays real."""
    calls: list[list[str]] = []

    def _fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="mode=existence", stderr="")

    monkeypatch.setattr(output_contract_engine, "subprocess", SimpleNamespace(run=_fake_run))
    return calls


def test_engine_violation_fails_fast_without_running_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "legacy-ran"
    legacy = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')\n"
    skill_dir = _skill_dir(tmp_path, legacy)
    _use_engine(
        monkeypatch,
        _fake_engine(tmp_path, stderr="script.md: missing required file", rc=1),
    )

    error = run_output_validator(skill_dir, tmp_path / "job")

    assert error is not None
    assert error.startswith("Output validation failed:")
    assert "missing required file" in error
    assert not marker.exists(), "engine failure must short-circuit the legacy script"


def test_engine_pass_still_runs_legacy_business_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode=contract means the generic checks passed; the legacy script then
    enforces the business rules the engine deliberately does not express."""
    skill_dir = _skill_dir(
        tmp_path, "import sys; sys.stderr.write('id mismatch\\n'); sys.exit(1)\n"
    )
    _use_engine(monkeypatch, _fake_engine(tmp_path, stdout="mode=contract", rc=0))

    error = run_output_validator(skill_dir, tmp_path / "job")

    assert error is not None
    assert error.startswith("Output validation failed:")
    assert "id mismatch" in error


def test_engine_existence_mode_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = _skill_dir(
        tmp_path, "import sys; sys.stderr.write('legacy says no\\n'); sys.exit(1)\n"
    )
    _use_engine(monkeypatch, _fake_engine(tmp_path, stdout="mode=existence", rc=0))

    error = run_output_validator(skill_dir, tmp_path / "job")

    assert error is not None
    assert "legacy says no" in error


def test_engine_broken_is_a_validator_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = _skill_dir(tmp_path, "import sys; sys.exit(0)\n")
    _use_engine(monkeypatch, _fake_engine(tmp_path, stderr="bad contract block", rc=2))

    error = run_output_validator(skill_dir, tmp_path / "job")

    assert error is not None
    assert error.startswith("Validator error:")
    assert "bad contract block" in error


def test_engine_spawn_failure_is_a_validator_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = _skill_dir(tmp_path, "import sys; sys.exit(0)\n")
    _use_engine(monkeypatch, str(tmp_path / "nonexistent-binary"))

    error = run_output_validator(skill_dir, tmp_path / "job")

    assert error is not None
    assert error.startswith("Validator error:")


def test_missing_engine_binary_keeps_legacy_only_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = _skill_dir(
        tmp_path, "import sys; sys.stderr.write('legacy only\\n'); sys.exit(1)\n"
    )
    _use_engine(monkeypatch, None)

    error = run_output_validator(skill_dir, tmp_path / "job")

    assert error is not None
    assert "legacy only" in error


def test_pre_443_binary_without_validate_subcommand_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollout shim: an old binary's clap front parser rejects the call —
    every clap usage error carries a "Usage:" line, which the current
    engine's own error output never prints. Both pre-#443 shapes fall back
    to legacy: old `velites` (unexpected argument) and old `velites-sandbox`
    (its trailing-arg parser swallows our flags, leaving --cwd missing)."""
    skill_dir = _skill_dir(
        tmp_path, "import sys; sys.stderr.write('legacy verdict\\n'); sys.exit(1)\n"
    )
    old_shapes = [
        "error: unexpected argument '--job-dir' found\n\nUsage: velites --provider <PROVIDER> <INSTRUCTION>...\n",
        "error: the following required arguments were not provided:\n  --cwd <CWD>\n\nUsage: velites-sandbox --cwd <CWD> <COMMAND>...\n",
    ]
    for shape in old_shapes:
        _use_engine(monkeypatch, _fake_engine(tmp_path, stderr=shape, rc=2))

        error = run_output_validator(skill_dir, tmp_path / "job")

        assert error is not None
        assert "legacy verdict" in error, shape


def test_current_broken_engine_stays_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A current binary's exit-2 failure (bad contract block etc.) prints no
    "Usage:" line — it must NOT be mistaken for an old binary."""
    skill_dir = _skill_dir(tmp_path, "import sys; sys.exit(0)\n")
    _use_engine(
        monkeypatch,
        _fake_engine(tmp_path, stderr="contract parse error: invalid contract YAML", rc=2),
    )

    error = run_output_validator(skill_dir, tmp_path / "job")

    assert error is not None
    assert error.startswith("Validator error:")
    assert "contract parse error" in error


# --- #538: the contract-block probe gates the velites spawn ---


def test_skill_without_contract_block_never_spawns_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine's only answer for a fence-less document is mode=existence
    (a no-verdict) — spawning it is pure latency, so the probe skips it."""
    skill_dir = _skill_dir(tmp_path, None, doc='# contract\n\n```json\n{"example": true}\n```\n')
    _use_engine(monkeypatch, "/nonexistent/velites")
    spawns = _record_engine_spawns(monkeypatch)

    assert run_contract_engine(skill_dir, tmp_path / "job", timeout_seconds=5) is None
    assert spawns == []


def test_missing_contract_document_never_spawns_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No references/output-contract.md at all — velites's degradation case."""
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    _use_engine(monkeypatch, "/nonexistent/velites")
    spawns = _record_engine_spawns(monkeypatch)

    assert run_contract_engine(skill_dir, tmp_path / "job", timeout_seconds=5) is None
    assert spawns == []


def test_skill_without_contract_block_lets_the_legacy_script_decide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The skip must be verdict-neutral end to end: the legacy script keeps
    the deciding vote exactly as when the engine answered existence mode."""
    skill_dir = _skill_dir(
        tmp_path,
        "import sys; sys.stderr.write('legacy only\\n'); sys.exit(1)\n",
        doc="prose only, no fences\n",
    )
    _use_engine(monkeypatch, "/nonexistent/velites")
    spawns = _record_engine_spawns(monkeypatch)

    error = run_output_validator(skill_dir, tmp_path / "job")

    assert error is not None
    assert "legacy only" in error
    assert spawns == []


def test_declared_contract_block_still_spawns_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared block keeps the engine authoritative — the pre-#538 spawn,
    same argv shape."""
    skill_dir = _skill_dir(tmp_path, None)
    _use_engine(monkeypatch, "/nonexistent/velites")
    spawns = _record_engine_spawns(monkeypatch)

    assert run_contract_engine(skill_dir, tmp_path / "job", timeout_seconds=5) is None
    assert len(spawns) == 1
    assert spawns[0][1] == "validate"
    assert str(skill_dir) in spawns[0]


def test_fence_variants_decide_the_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the exact info string declares a block (velites matches the
    trimmed line exactly): a plain ```yaml fence is prose, trailing text on
    the info string is prose, surrounding whitespace is tolerated."""
    cases = [
        ("```yaml\nfiles: []\n```\n", False),
        ("```YAML CONTRACT\n```\n", False),
        ("```yaml contract extras\n```\n", False),
        ("```yaml contract\nfiles:\n  - path: a.md\n    format: text\n```\n", True),
        ("  ```yaml contract  \nfiles:\n  - path: a.md\n    format: text\n```\n", True),
    ]
    for index, (doc, declares) in enumerate(cases):
        skill_dir = _skill_dir(tmp_path / f"case{index}", None, doc=doc)
        _use_engine(monkeypatch, "/nonexistent/velites")
        spawns = _record_engine_spawns(monkeypatch)

        assert run_contract_engine(skill_dir, tmp_path / "job", timeout_seconds=5) is None
        assert bool(spawns) is declares, doc


def test_unclosed_contract_fence_still_spawns_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An opening fence that is never closed is malformed, not absent — the
    fail-closed verdict is velites's to deliver, so the probe keeps the
    spawn."""
    skill_dir = _skill_dir(
        tmp_path, None, doc="```yaml contract\nfiles:\n  - path: a.md\n    format: text\n"
    )
    _use_engine(monkeypatch, "/nonexistent/velites")
    spawns = _record_engine_spawns(monkeypatch)

    assert run_contract_engine(skill_dir, tmp_path / "job", timeout_seconds=5) is None
    assert len(spawns) == 1


def test_unreadable_contract_document_still_spawns_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read failure other than not-found is velites's fail-closed case —
    the probe must not downgrade it to existence mode."""
    skill_dir = _skill_dir(tmp_path, None)

    def _deny(self: Path, **_kwargs: object) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", _deny)
    _use_engine(monkeypatch, "/nonexistent/velites")
    spawns = _record_engine_spawns(monkeypatch)

    assert run_contract_engine(skill_dir, tmp_path / "job", timeout_seconds=5) is None
    assert len(spawns) == 1


def test_non_utf8_contract_document_still_spawns_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-UTF-8 content is the other undeterminable case (velites's
    read_to_string fails → exit 2 fail-closed) — the probe must route it
    to the engine, not silently degrade to existence mode."""
    skill_dir = _skill_dir(tmp_path, None)
    (skill_dir / "references" / "output-contract.md").write_bytes(
        "```yaml contract\nfiles:\n  - path: a.md\n    format: text\n".encode("latin-1")
        + b"\xff\xfe binary tail"
    )
    _use_engine(monkeypatch, "/nonexistent/velites")
    spawns = _record_engine_spawns(monkeypatch)

    assert run_contract_engine(skill_dir, tmp_path / "job", timeout_seconds=5) is None
    assert len(spawns) == 1
