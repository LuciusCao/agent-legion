"""Host-side adapter for the harness contract engine (#443).

The engine lives in the velites binaries (``velites-sandbox validate`` /
``velites validate``) and reads the skill's machine-readable contract block;
this module is the subprocess seam so ``output_validation`` stays under its
file-size budget. See that module for the two-layer validation contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from shared.code_sandbox import resolve_sandbox_binary


def run_contract_engine(
    skill_dir: Path,
    job_dir: Path,
    *,
    timeout_seconds: int,
) -> str | None:
    """Run the harness contract engine; ``None`` = no verdict against the outputs.

    The engine is authoritative only when it reports contract violations
    (exit 1) or is itself broken (exit 2); exit 0 means either the contract
    block passed (``mode=contract``) or the skill has no machine-readable
    contract block (``mode=existence``) — either way the legacy script in
    ``output_validation`` still runs. Hosts without a velites binary keep
    the legacy-only behavior.
    """
    binary = resolve_sandbox_binary()
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [binary, "validate", "--job-dir", str(job_dir), "--skill", str(skill_dir)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        # #204 broad-except audit: convert-to-contract — the string verdict is
        # the only failure channel (None = no verdict). The width is the
        # subprocess surface (OSError when the binary path is broken,
        # TimeoutExpired past timeout_seconds); each must surface as
        # "Validator error: ..." so the node FAILS closed — an unrunnable
        # engine must never be treated as validated output. The exception
        # text rides the returned message.
        return f"Validator error: {exc}"
    if proc.returncode == 1:
        return f"Output validation failed: {proc.stderr.strip()}"
    if proc.returncode != 0:
        # Rollout shim: pre-#443 binaries have no `validate` subcommand and
        # their clap front parser rejects the call — old `velites` with
        # "unexpected argument '--job-dir'", old `velites-sandbox` (whose
        # trailing-arg parser swallows our flags) with "required arguments
        # were not provided: --cwd". Both shapes carry clap's "Usage:" line,
        # which the current engine's own error output never prints; that
        # signature means "engine unavailable", not "engine broken", so the
        # legacy script takes over. A current binary's exit-2 failures are
        # real (bad contract block, unreadable job dir) and stay fail-closed.
        if "Usage:" in proc.stderr:
            return None
        return f"Validator error: contract engine exited {proc.returncode}: {proc.stderr.strip()}"
    return None
