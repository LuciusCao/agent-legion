"""#757: the Host-side business-rule validator runs against the declared validation view.

A job dir accumulates every node's outputs across all attempts; a glob-based
review validator (``review_<pair>*.json`` checks both A/B files) then sees a
sibling node's stale fail file and flips this node's clean run — the
cross-attributed "verdict is 'fail'" errors of #757. The validator must see
exactly this node's declared inputs plus this attempt's declared outputs —
sibling outputs and stale residues never enter.

The view is zero-copy (hardlinks); its exit arms reconcile the validator's
output mutations back into the run view (the replace family included) and
enforce the inputs read-only contract. These tests pin all of it through
``validate_worker_outputs`` with the result-validate pool inlined (same seam
as tests/workflows/test_output_validation.py). The trailing family pins the
#828/#830/#833 input semantics in the same view: names are compared on the
normalized spelling (``./``/``//`` collapsed) for the input/output overlap
exclusion, and input bytes resolve to the dispatch-frozen CAS copy
(``input_artifacts`` refs + store root) with the job dir as fallback —
still placed as private copies, so the write isolation covers CAS-sourced
bytes too. The per-file mechanics regressions (reflink probe residue,
reconcile I/O discipline) live in the sibling
``test_output_validation_view_files.py``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

import server.app.workflows.output_contract_engine as output_contract_engine
import server.app.workflows.validation_view as validation_view_module
import server.app.workflows.worker_output_validation as worker_output_validation
from server.app.services.artifact_store import ArtifactStore
from server.app.skills.manager import SkillManager
from server.app.workflows.worker_output_validation import validate_worker_outputs
from tests.helpers.skill_git import _make_manager as _make_real_manager
from tests.helpers.skill_git import _make_skill_repo

pytestmark = pytest.mark.no_db

_KEY = "group/review"


@pytest.fixture(autouse=True)
def _no_real_engine_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic default: the business-rule script alone decides."""
    monkeypatch.setattr(output_contract_engine, "resolve_sandbox_binary", lambda: None)


@pytest.fixture(autouse=True)
def _inline_validate_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        worker_output_validation, "validate_in_pool", lambda call, *args: call(*args)
    )


def _manager(tmp_path: Path, validator_body: str | None) -> SkillManager:
    _make_skill_repo(tmp_path / "skills", _KEY, validate_script=validator_body)
    return _make_real_manager(tmp_path)


def _validator(seen_file: Path, rules: str) -> str:
    """A validator recording every file it sees, then applying ``rules``."""
    return (
        "import json, pathlib, sys\n"
        "job = pathlib.Path(sys.argv[1])\n"
        "seen = sorted(str(p.relative_to(job)) for p in job.rglob('*') if p.is_file())\n"
        f"pathlib.Path({str(seen_file)!r}).write_text('\\n'.join(seen))\n"
    ) + rules


_REVIEW_RULES = (
    "bad = []\n"
    "for p in job.glob('review_*.json'):\n"
    "    if json.loads(p.read_text())['verdict'] != 'pass':\n"
    "        bad.append(p.name)\n"
    "if bad:\n"
    "    sys.stderr.write(', '.join(bad) + ': verdict is fail\\n')\n"
    "    sys.exit(1)\n"
)


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    """The accumulating job dir and this attempt's read view (staging)."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    run_view = tmp_path / "staging"
    run_view.mkdir()
    return job_dir, run_view


def _manifest(inputs: list[str], outputs: list[str]) -> dict:
    return {"skill": _KEY, "skill_ref": "latest", "inputs": inputs, "expected_outputs": outputs}


def _cas_blob(root: Path, data: bytes) -> str:
    """Write ``data`` as a CAS blob under ``root``; return its digest."""
    digest = hashlib.sha256(data).hexdigest()
    blob = root / digest[:2] / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(data)
    return digest


def _cas_store(root: Path) -> ArtifactStore:
    """A store over ``root`` for its read side only (``open_blob`` never
    touches the connect source, and these tests stay off the DB)."""
    return ArtifactStore(root, "")


def _seen(seen_file: Path) -> list[str]:
    return seen_file.read_text().splitlines()


def test_sibling_and_stale_fail_files_cannot_poison_this_node(tmp_path: Path) -> None:
    """The #757 fingerprint: job_dir holds the sibling's stale fail file AND
    this node's own previous-attempt fail file; this attempt's run passes —
    the validator must see only this attempt's outputs + declared inputs."""
    seen_file = tmp_path / "seen.txt"
    manager = _manager(tmp_path, _validator(seen_file, _REVIEW_RULES))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text('{"id": 1}')
    (job_dir / "review_b.json").write_text('{"verdict": "fail"}')  # sibling, stale
    (job_dir / "review_a.json").write_text('{"verdict": "fail"}')  # own, previous attempt
    (run_view / "review_a.json").write_text('{"verdict": "pass"}')  # this attempt

    manifest = _manifest(["cleaned_question.json"], ["review_a.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert _seen(seen_file) == ["cleaned_question.json", "review_a.json"]


def test_own_verdict_still_decides_the_run(tmp_path: Path) -> None:
    """View filtering must not weaken validation: this attempt's own fail
    verdict fails the run, attributed to this node's own file."""
    seen_file = tmp_path / "seen.txt"
    manager = _manager(tmp_path, _validator(seen_file, _REVIEW_RULES))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text('{"verdict": "fail"}')

    error = validate_worker_outputs(manager, _manifest([], ["review_a.json"]), job_dir, run_view)

    assert error is not None
    assert "review_a.json: verdict is fail" in error
    assert _seen(seen_file) == ["review_a.json"]


def test_inputs_drive_cross_file_consistency_checks(tmp_path: Path) -> None:
    """Inputs passthrough: a validator cross-checking an output against a
    declared input keeps working in both directions (and read-only input
    access passes the read-only enforcement)."""
    rules = (
        "question = json.loads((job / 'cleaned_question.json').read_text())\n"
        "review = json.loads((job / 'review_a.json').read_text())\n"
        "if review['question_id'] != question['id']:\n"
        "    sys.stderr.write('question id mismatch\\n')\n"
        "    sys.exit(1)\n"
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text('{"id": 1}')
    (run_view / "review_a.json").write_text('{"question_id": 1, "verdict": "pass"}')
    manifest = _manifest(["cleaned_question.json"], ["review_a.json"])

    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None

    (job_dir / "cleaned_question.json").write_text('{"id": 2}')
    error = validate_worker_outputs(manager, manifest, job_dir, run_view)
    assert error is not None
    assert "question id mismatch" in error


def test_undeclared_files_never_enter_the_view(tmp_path: Path) -> None:
    """The view is exactly the declared names: undeclared job-dir files and
    the run view's non-output members (run_dir logs etc.) stay invisible;
    nested declared paths keep their relative shape."""
    seen_file = tmp_path / "seen.txt"
    manager = _manager(tmp_path, _validator(seen_file, ""))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("{}")
    (job_dir / "undeclared.json").write_text("{}")
    (run_view / "review_a.json").write_text("{}")
    (run_view / "reports").mkdir()
    (run_view / "reports" / "summary.json").write_text("{}")
    events = run_view / "runs" / "node" / "worker"
    events.mkdir(parents=True)
    (events / "events.jsonl").write_text("{}\n")

    manifest = _manifest(["cleaned_question.json"], ["review_a.json", "reports/summary.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert _seen(seen_file) == [
        "cleaned_question.json",
        "reports/summary.json",
        "review_a.json",
    ]


def test_missing_declared_entries_are_absent_not_errors(tmp_path: Path) -> None:
    """Construction never fails on missing declarations (the local job dir is
    an evictable cache): the entries are simply absent and the validator's
    own rules decide — here one that tolerates absence."""
    seen_file = tmp_path / "seen.txt"
    manager = _manager(tmp_path, _validator(seen_file, ""))
    job_dir, run_view = _layout(tmp_path)

    manifest = _manifest(["missing_input.json"], ["missing_output.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert _seen(seen_file) == []


def test_double_review_outcomes_reflect_own_verdicts_only(tmp_path: Path) -> None:
    """End-to-end #757 timeline: a stale B fail file sits in the job dir;
    A completes first with pass, then B completes with pass — both runs
    validate clean, and B's validation cannot see A's (now current) file."""
    seen_file = tmp_path / "seen.txt"
    manager = _manager(tmp_path, _validator(seen_file, _REVIEW_RULES))
    job_dir, run_view_a = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text('{"id": 1}')
    (job_dir / "review_b.json").write_text('{"verdict": "fail"}')  # last round's residue

    # A completes first: its attempt passes.
    (run_view_a / "review_a.json").write_text('{"verdict": "pass"}')
    manifest_a = _manifest(["cleaned_question.json"], ["review_a.json"])
    assert validate_worker_outputs(manager, manifest_a, job_dir, run_view_a) is None
    assert _seen(seen_file) == ["cleaned_question.json", "review_a.json"]

    # A's bytes land in the job dir; B then completes with its own pass.
    (job_dir / "review_a.json").write_text('{"verdict": "pass"}')
    run_view_b = tmp_path / "staging-b"
    run_view_b.mkdir()
    (run_view_b / "review_b.json").write_text('{"verdict": "pass"}')
    manifest_b = _manifest(["cleaned_question.json"], ["review_b.json"])
    assert validate_worker_outputs(manager, manifest_b, job_dir, run_view_b) is None
    assert _seen(seen_file) == ["cleaned_question.json", "review_b.json"]


def test_output_bytes_win_when_a_name_is_both_input_and_output(tmp_path: Path) -> None:
    """Read-modify-write nodes declare the same name twice; the validator
    must see this attempt's bytes — and the read-only input enforcement must
    not fire on the shared name (it resolves to the output semantics)."""
    rules = (
        "if (job / 'shared.json').read_text() != 'new':\n"
        "    sys.stderr.write('stale bytes\\n')\n"
        "    sys.exit(1)\n"
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "shared.json").write_text("old")
    (run_view / "shared.json").write_text("new")

    manifest = _manifest(["shared.json"], ["shared.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None


def test_unsafe_declared_names_never_escape_the_view(tmp_path: Path) -> None:
    """Absolute and ``..`` declarations are dropped, never linked through."""
    seen_file = tmp_path / "seen.txt"
    manager = _manager(tmp_path, _validator(seen_file, ""))
    job_dir, run_view = _layout(tmp_path)
    (tmp_path / "outside.json").write_text("{}")
    (run_view / "ok.json").write_text("{}")

    manifest = _manifest(["../outside.json"], ["/abs.json", "ok.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert _seen(seen_file) == ["ok.json"]


def test_skill_missing_business_rule_script_fails_closed(tmp_path: Path) -> None:
    """Why there is no "nothing to validate" skip: the dispatch contract
    trio makes scripts/validate_output.py mandatory, and a tree missing it
    (the #638 poisoned-cache shape) must keep failing closed — never skip."""
    manager = _manager(tmp_path, None)
    job_dir, run_view = _layout(tmp_path)

    error = validate_worker_outputs(manager, _manifest([], []), job_dir, run_view)

    assert error is not None
    assert error.startswith("Validator error:")
    assert "scripts/validate_output.py" in error


def test_view_construction_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unbuildable view is an unrunnable validator: fail THIS node closed
    through the Validator error channel, never treat it as validated."""
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")
    job_dir, run_view = _layout(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("view boom")

    monkeypatch.setattr(validation_view_module, "materialize_validation_view", _boom)
    error = validate_worker_outputs(manager, _manifest([], []), job_dir, run_view)

    assert error is not None
    assert error.startswith("Validator error:")
    assert "view boom" in error


def test_placement_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure grading: a missing SOURCE is fail-open (absent from the view),
    but a placement FAILURE (disk full on the reflink AND the copy fallback)
    is an infra error — the validator must never judge a silently incomplete
    view, so the run fails closed as a Validator error."""
    import server.app.workflows._reflink_copy as reflink_copy

    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("{}")

    def _enospc(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(reflink_copy, "_clone", _enospc)
    monkeypatch.setattr(shutil, "copy2", _enospc)
    error = validate_worker_outputs(
        manager, _manifest(["cleaned_question.json"], []), job_dir, run_view
    )

    assert error is not None
    assert error.startswith("Validator error:")
    assert "No space left on device" in error


def test_validator_input_writes_cannot_reach_the_job_dir(tmp_path: Path) -> None:
    """P1 regression: the view's input is a private copy (reflink or full
    copy), so an in-place write physically cannot reach the job dir's
    upstream artifact — closing the stale-completion pollution path (a late
    completion's validator corrupting the new generation's local inputs).
    The snapshot check stays as defense in depth and fails the run closed."""
    rules = "(job / 'cleaned_question.json').write_text('rewritten by validator')\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("original")

    error = validate_worker_outputs(
        manager, _manifest(["cleaned_question.json"], []), job_dir, run_view
    )

    assert error is not None
    assert error.startswith("Validator error:")
    assert "validator mutated declared input 'cleaned_question.json'" in error
    assert (job_dir / "cleaned_question.json").read_text() == "original"


def test_validator_chmod_on_an_input_cannot_reach_the_job_dir(tmp_path: Path) -> None:
    """The chmod family is why hardlinks were never enough: chmod crosses a
    shared inode without touching mtime/size. With a private copy the
    validator's chmod is scratch-local — no contract violation fires and the
    job dir's mode is untouched."""
    rules = "import os\nos.chmod(job / 'cleaned_question.json', 0o000)\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("original")
    original_mode = (job_dir / "cleaned_question.json").stat().st_mode

    assert (
        validate_worker_outputs(
            manager, _manifest(["cleaned_question.json"], []), job_dir, run_view
        )
        is None
    )
    assert (job_dir / "cleaned_question.json").stat().st_mode == original_mode


def test_validator_replacing_an_input_fails_closed_without_polluting_the_job_dir(
    tmp_path: Path,
) -> None:
    """The replace family against an input: os.replace swaps only the VIEW's
    dir entry (the private copy's inode is left behind), so the upstream
    artifact's bytes survive — and the exit check still fails the run."""
    rules = (
        "import os\n"
        "(job / 'cleaned_question.json.tmp').write_text('rewritten')\n"
        "os.replace(job / 'cleaned_question.json.tmp', job / 'cleaned_question.json')\n"
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("original")

    error = validate_worker_outputs(
        manager, _manifest(["cleaned_question.json"], []), job_dir, run_view
    )

    assert error is not None
    assert "validator mutated declared input" in error
    assert (job_dir / "cleaned_question.json").read_text() == "original"


def test_validator_output_writes_reach_the_run_view_bytes(tmp_path: Path) -> None:
    """In-place output writes propagate through the hardlink — the review
    skills' clean-in-place helpers (write_text) keep reaching the bytes the
    finish gate will promote."""
    rules = "(job / 'review_a.json').write_text('cleaned')\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("raw")

    manifest = _manifest([], ["review_a.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert (run_view / "review_a.json").read_text() == "cleaned"


def test_validator_output_replace_family_reconciles_into_the_run_view(tmp_path: Path) -> None:
    """P2-1 regression: the replace family breaks hardlink sharing — a
    validator cleaning via temp+os.replace or delete-and-rewrite must still
    have its bytes reconciled back into the run view, or the finish gate
    would promote the uncleaned pre-validation bytes."""
    rules = (
        "import os\n"
        "tmp = job / 'review_a.json.tmp'\n"
        "tmp.write_text('cleaned-a')\n"
        "os.replace(tmp, job / 'review_a.json')\n"
        "(job / 'review_b.json').unlink()\n"
        "(job / 'review_b.json').write_text('cleaned-b')\n"
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("raw-a")
    (run_view / "review_b.json").write_text("raw-b")

    manifest = _manifest([], ["review_a.json", "review_b.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert (run_view / "review_a.json").read_text() == "cleaned-a"
    assert (run_view / "review_b.json").read_text() == "cleaned-b"


def test_validator_output_mutations_reconcile_even_when_the_verdict_fails(
    tmp_path: Path,
) -> None:
    """Reconcile runs on every clean exit, verdict or not — the same
    always-propagate semantics in-place writes have (a failed run's staged
    bytes still land for observability, so they must be the real ones)."""
    rules = (
        "(job / 'review_a.json').write_text('cleaned-then-failed')\n"
        "sys.stderr.write('still bad\\n')\n"
        "sys.exit(1)\n"
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("raw")

    manifest = _manifest([], ["review_a.json"])
    error = validate_worker_outputs(manager, manifest, job_dir, run_view)

    assert error is not None
    assert "still bad" in error
    assert (run_view / "review_a.json").read_text() == "cleaned-then-failed"


def test_validator_created_undeclared_files_never_propagate(tmp_path: Path) -> None:
    """The promotion plan is frozen at unpack time (#759): a file the
    validator creates in the view is scratch, never an artifact — the view
    is not a backdoor around the declared output surface."""
    rules = "(job / 'extra.json').write_text('{}')\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("raw")

    manifest = _manifest([], ["review_a.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert not (run_view / "extra.json").exists()


def test_validator_deleted_output_propagates_the_deletion(tmp_path: Path) -> None:
    """A validator deleting a declared output and passing: the deletion
    propagates to the run view — the pre-view semantics, where the finish
    gate's missing-source containment then fails the run honestly (tested
    at the view layer only; the promotion arm is #759 machinery)."""
    rules = "(job / 'review_b.json').unlink()\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("{}")
    (run_view / "review_b.json").write_text("{}")

    manifest = _manifest([], ["review_a.json", "review_b.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert (run_view / "review_a.json").is_file()
    assert not (run_view / "review_b.json").exists()


# --- #757 P1: reflink-or-copy private input copies ---


def test_private_input_copy_has_its_own_inode(tmp_path: Path) -> None:
    """copy_private must never alias the source's inode — on CoW filesystems
    via reflink, elsewhere via full copy; content and metadata ride along."""
    from server.app.workflows._reflink_copy import copy_private

    source = tmp_path / "source.json"
    source.write_text('{"id": 1}')
    spot = tmp_path / "view" / "source.json"
    spot.parent.mkdir()

    copy_private(source, spot)

    assert spot.read_text() == '{"id": 1}'
    assert os.stat(spot).st_ino != os.stat(source).st_ino


def test_reflink_probe_is_cached_per_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The support probe runs once per filesystem (st_dev), not per file:
    two validations against the same job dir probe exactly once."""
    import server.app.workflows._reflink_copy as reflink_copy

    monkeypatch.setattr(reflink_copy, "_support", {})
    probes: list[int] = []
    original_probe = reflink_copy._probe

    def _spying_probe(probe_dir: Path) -> bool:
        probes.append(probe_dir.stat().st_dev)
        return original_probe(probe_dir)

    monkeypatch.setattr(reflink_copy, "_probe", _spying_probe)
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", ""))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("{}")

    manifest = _manifest(["cleaned_question.json"], [])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert len(probes) == 1


def test_reflink_unsupported_falls_back_to_full_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem without CoW (or a failed probe) takes the full-copy
    fallback silently — same isolation, same fail-closed detection."""
    import server.app.workflows._reflink_copy as reflink_copy

    monkeypatch.setattr(reflink_copy, "_support", {})
    monkeypatch.setattr(reflink_copy, "_supported", lambda *_args: False)
    rules = "(job / 'cleaned_question.json').write_text('rewritten by validator')\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("original")

    error = validate_worker_outputs(
        manager, _manifest(["cleaned_question.json"], []), job_dir, run_view
    )

    assert error is not None
    assert "validator mutated declared input" in error
    assert (job_dir / "cleaned_question.json").read_text() == "original"


# --- #828/#830/#833: input name decision + dispatch-frozen CAS bytes ---


def test_input_bytes_come_from_the_dispatch_frozen_cas_copy(tmp_path: Path) -> None:
    """#833 codex P1: dispatch froze the bytes the Worker actually consumed
    (``stage_agent_inputs`` → manifest ``input_artifacts``); a parallel
    producer then overwrote the same-name job-dir file. Validation must run
    against the frozen bytes, not the overwritten present."""
    rules = (
        "if (job / 'cleaned_question.json').read_text() != 'dispatch-frozen':\n"
        "    sys.stderr.write('validated against overwritten job-dir bytes\\n')\n"
        "    sys.exit(1)\n"
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    cas_root = tmp_path / "cas"
    digest = _cas_blob(cas_root, b"dispatch-frozen")
    (job_dir / "cleaned_question.json").write_text("overwritten-by-parallel-producer")

    manifest = _manifest(["cleaned_question.json"], [])
    manifest["input_artifacts"] = {"cleaned_question.json": f"sha256:{digest}"}
    assert (
        validate_worker_outputs(manager, manifest, job_dir, run_view, _cas_store(cas_root)) is None
    )
    assert (job_dir / "cleaned_question.json").read_text() == "overwritten-by-parallel-producer"


def test_cas_sourced_input_is_still_a_private_copy(tmp_path: Path) -> None:
    """Threat-model stacking: CAS reads the right bytes, the private inode
    keeps them write-isolated — a validator's in-place write on a CAS-sourced
    input must neither reach the shared blob nor escape the read-only
    enforcement."""
    rules = "(job / 'cleaned_question.json').write_text('rewritten by validator')\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    cas_root = tmp_path / "cas"
    digest = _cas_blob(cas_root, b"dispatch-frozen")
    blob = cas_root / digest[:2] / digest

    manifest = _manifest(["cleaned_question.json"], [])
    manifest["input_artifacts"] = {"cleaned_question.json": f"sha256:{digest}"}
    error = validate_worker_outputs(manager, manifest, job_dir, run_view, _cas_store(cas_root))

    assert error is not None
    assert "validator mutated declared input" in error
    assert blob.read_bytes() == b"dispatch-frozen"


def test_cas_blob_missing_falls_back_to_the_job_dir(tmp_path: Path) -> None:
    """A stale ref whose blob is gone (GC race) takes the job-dir fallback —
    the pre-#833 exposure, judged by the validator's own rules. This arm is
    the fail-open degradation of EXEC-INPUT-IDENTITY-001, not a normal
    channel: (job,node) refs shield the blob for the job's lifetime, so the
    race stays rare by construction."""
    rules = "if (job / 'cleaned_question.json').read_text() != 'local-bytes':\n    sys.exit(1)\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    cas_root = tmp_path / "cas"
    cas_root.mkdir()
    (job_dir / "cleaned_question.json").write_text("local-bytes")

    manifest = _manifest(["cleaned_question.json"], [])
    manifest["input_artifacts"] = {"cleaned_question.json": f"sha256:{'0' * 64}"}
    assert (
        validate_worker_outputs(manager, manifest, job_dir, run_view, _cas_store(cas_root)) is None
    )


def test_non_cas_ref_shape_falls_back_to_the_job_dir(tmp_path: Path) -> None:
    """A claim-time presigned dict is not a CAS ref — job-dir fallback."""
    rules = "if (job / 'cleaned_question.json').read_text() != 'local-bytes':\n    sys.exit(1)\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("local-bytes")

    manifest = _manifest(["cleaned_question.json"], [])
    manifest["input_artifacts"] = {"cleaned_question.json": {"url": "https://x", "sha256": "z"}}
    assert (
        validate_worker_outputs(manager, manifest, job_dir, run_view, _cas_store(tmp_path / "cas"))
        is None
    )


def test_noncanonical_input_spelling_resolves_to_the_output_bytes(tmp_path: Path) -> None:
    """#833 codex P2: an input declared as ``./shared.json`` is the same view
    name as the ``shared.json`` output — the overlap exclusion compares
    normalized spellings, so the stale job-dir input bytes can never smuggle
    over this attempt's fresh output (and the read-only check stays exempt)."""
    rules = (
        "if (job / 'shared.json').read_text() != 'new':\n"
        "    sys.stderr.write('stale bytes\\n')\n"
        "    sys.exit(1)\n"
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "shared.json").write_text("old")
    (run_view / "shared.json").write_text("new")

    manifest = _manifest(["./shared.json"], ["shared.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None


def test_input_refs_without_a_store_take_the_job_dir(tmp_path: Path) -> None:
    """Refs in the manifest but no store on the caller (artifact_store=None)
    — every input falls back to the job dir. Same fallback family as the
    no-ref legacy exemption (EXEC-INPUT-IDENTITY-001): pre-#833 manifests
    without frozen refs drain to zero as old jobs exhaust; new manifests
    always carry frozen refs and a store."""
    rules = "if (job / 'cleaned_question.json').read_text() != 'local-bytes':\n    sys.exit(1)\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("local-bytes")

    manifest = _manifest(["cleaned_question.json"], [])
    manifest["input_artifacts"] = {"cleaned_question.json": f"sha256:{'0' * 64}"}
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None


def test_duplicate_declarations_place_once(tmp_path: Path) -> None:
    """#868: duplicate declarations — including normalized-equivalent
    spellings (``in.json`` vs ``./in.json``) — place exactly once. A second
    placement would delete the first private copy and leave its snapshot
    pointing at a dead inode, misfiring the read-only check on a clean run."""
    seen_file = tmp_path / "seen.txt"
    manager = _manager(tmp_path, _validator(seen_file, ""))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("{}")
    (run_view / "review_a.json").write_text("{}")

    manifest = _manifest(
        ["cleaned_question.json", "./cleaned_question.json"],
        ["review_a.json", "review_a.json"],
    )
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert _seen(seen_file) == ["cleaned_question.json", "review_a.json"]


def test_duplicate_input_aliases_follow_serialized_key_order(tmp_path: Path) -> None:
    """#876 P2-a legacy 消耗规则：冻结点去重前的存量 manifest 里，同一归
    一化名的两个别名冻结了不同 digest——Worker 按排序后键序（enqueue
    sort_keys）下载、同路径后者覆盖前者，实际消费「排序后原始拼写最后
    者」（max 原拼写）。校验必须取同一身份；声明列表顺序无关（此处故
    意反序声明）。"""
    rules = (
        "if (job / 'cleaned_question.json').read_text() != 'consumed-bytes':\n"
        "    sys.stderr.write('validated the shadowed alias, not the consumed one\\n')\n"
        "    sys.exit(1)\n"
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    cas_root = tmp_path / "cas"
    shadowed_digest = _cas_blob(cas_root, b"shadowed-bytes")
    consumed_digest = _cas_blob(cas_root, b"consumed-bytes")
    (job_dir / "cleaned_question.json").write_text("overwritten-later")

    # "./cleaned_question.json" < "cleaned_question.json"（'.' < 'c'）——
    # 排序后最后者是 "cleaned_question.json"，Worker 消费它的 digest。
    manifest = _manifest(["./cleaned_question.json", "cleaned_question.json"], [])
    manifest["input_artifacts"] = {
        "./cleaned_question.json": f"sha256:{shadowed_digest}",
        "cleaned_question.json": f"sha256:{consumed_digest}",
    }
    assert (
        validate_worker_outputs(manager, manifest, job_dir, run_view, _cas_store(cas_root)) is None
    )


def test_duplicate_aliases_on_deduped_manifest_hit_the_normalized_key(tmp_path: Path) -> None:
    """#876 P2-a 新形态：冻结点去重后的 manifest 只有归一化单键——声明
    列表仍含别名（展示/审计语义），两个别名的 raw 查找都落空、按归一
    化名命中同一冻结 digest，序无关。"""
    rules = "if (job / 'cleaned_question.json').read_text() != 'frozen':\n    sys.exit(1)\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    cas_root = tmp_path / "cas"
    digest = _cas_blob(cas_root, b"frozen")
    (job_dir / "cleaned_question.json").write_text("overwritten-later")

    manifest = _manifest(["cleaned_question.json", "./cleaned_question.json"], [])
    manifest["input_artifacts"] = {"cleaned_question.json": f"sha256:{digest}"}
    assert (
        validate_worker_outputs(manager, manifest, job_dir, run_view, _cas_store(cas_root)) is None
    )
