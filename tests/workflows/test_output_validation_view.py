"""#757: the Host-side legacy validator runs against the declared validation view.

A job dir accumulates every node's outputs across all attempts; a glob-based
review validator (``review_<pair>*.json`` checks both A/B files) then sees a
sibling node's stale fail file and flips this node's clean run — the
cross-attributed "verdict is 'fail'" errors of #757. The validator must see
exactly this node's declared inputs (copied from the job dir, cross-file
checks keep working) plus this attempt's declared outputs (linked from the
run's read view) — sibling outputs and stale residues never enter.

These tests pin the view semantics through ``validate_worker_outputs`` with
the result-validate pool inlined (same seam as
tests/workflows/test_output_validation.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import server.app.workflows.output_contract_engine as output_contract_engine
import server.app.workflows.worker_output_validation as worker_output_validation
from server.app.skills.manager import SkillManager
from server.app.workflows.worker_output_validation import validate_worker_outputs
from tests.helpers.skill_git import _make_manager as _make_real_manager
from tests.helpers.skill_git import _make_skill_repo

pytestmark = pytest.mark.no_db

_KEY = "group/review"


@pytest.fixture(autouse=True)
def _no_real_engine_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic default: the legacy script path alone decides."""
    monkeypatch.setattr(output_contract_engine, "resolve_sandbox_binary", lambda: None)


@pytest.fixture(autouse=True)
def _inline_validate_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        worker_output_validation, "validate_in_pool", lambda call, *args: call(*args)
    )


def _manager(tmp_path: Path, validator_body: str) -> SkillManager:
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
    declared input keeps working in both directions."""
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
    must see this attempt's bytes, not the input copy."""
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


def test_view_construction_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unbuildable view is an unrunnable validator: fail THIS node closed
    through the Validator error channel, never treat it as validated."""
    manager = _manager(tmp_path, "import sys; sys.exit(0)\n")
    job_dir, run_view = _layout(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("view boom")

    monkeypatch.setattr(worker_output_validation, "validation_view", _boom)
    error = validate_worker_outputs(manager, _manifest([], []), job_dir, run_view)

    assert error is not None
    assert error.startswith("Validator error:")
    assert "view boom" in error


def test_validator_input_writes_stay_out_of_the_job_dir(tmp_path: Path) -> None:
    """Inputs are copied, not linked: a validator rewriting an input file
    must not mutate an upstream node's artifact in the job dir."""
    rules = "(job / 'cleaned_question.json').write_text('rewritten by validator')\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("original")

    manifest = _manifest(["cleaned_question.json"], [])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert (job_dir / "cleaned_question.json").read_text() == "original"


def test_validator_output_writes_reach_the_run_view_bytes(tmp_path: Path) -> None:
    """Outputs are hardlinked: a validator cleaning an output in place (the
    review skills' clean-in-place helpers) keeps reaching the bytes that the
    finish gate will promote — the pre-#757 staging-view semantics."""
    rules = "(job / 'review_a.json').write_text('cleaned')\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("raw")

    manifest = _manifest([], ["review_a.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert (run_view / "review_a.json").read_text() == "cleaned"
