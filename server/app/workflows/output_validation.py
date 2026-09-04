"""Deterministic output validation for skill-produced artifacts.

Agent output is untrusted regardless of where the agent ran. Validation is
two-layered (#443): the harness contract engine (``velites-sandbox validate``,
reading the skill's machine-readable contract block) runs first and fails
fast on generic contract violations; the skill's legacy
``scripts/validate_output.py`` then runs for the business rules the engine
deliberately does not express (cross-file consistency etc.). The Worker
completion path lives in ``worker_output_validation``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from server.app.workflows.output_contract_engine import run_contract_engine

VALIDATOR_TIMEOUT_SECONDS = 30


def run_output_validator(
    skill_dir: Path,
    job_dir: Path,
    *,
    timeout_seconds: int = VALIDATOR_TIMEOUT_SECONDS,
) -> str | None:
    """Validate ``job_dir`` against the skill's contract, engine first (#443).

    Returns ``None`` when outputs are valid (or the skill declares nothing to
    check); otherwise the error message to record on the failed run.
    """
    engine_error = run_contract_engine(skill_dir, job_dir, timeout_seconds=timeout_seconds)
    if engine_error is not None:
        return engine_error
    return _run_legacy_validator(skill_dir, job_dir, timeout_seconds=timeout_seconds)


def _run_legacy_validator(
    skill_dir: Path,
    job_dir: Path,
    *,
    timeout_seconds: int,
) -> str | None:
    """Run the skill's legacy ``validate_output.py`` against ``job_dir``."""
    validator = skill_dir / "scripts" / "validate_output.py"
    if not validator.is_file():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(validator), str(job_dir)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        # #204 broad-except audit: convert-to-contract, not a swallow — the
        # function's return value IS the verdict channel (None = valid,
        # string = failure message recorded on the run). The width is the
        # subprocess surface (OSError when the interpreter/script path is
        # broken, TimeoutExpired past timeout_seconds, spawn failures), none
        # of which is a business family; each must surface as "Validator
        # error: ..." so the node FAILS closed — an unrunnable validator
        # must never be treated as validated output (the untrusted-output
        # contract in the module docstring). The exception text rides the
        # returned message; the run row records it.
        return f"Validator error: {exc}"
    if proc.returncode != 0:
        return f"Output validation failed: {proc.stderr.strip()}"
    return None
