"""Select the command builder config for an Agent runtime (EXEC-RUNTIME-DISPATCH-001).

Split out of ``dispatch.py`` for the file-size budget; pure selection logic
kept separate so runtime dispatch stays unit-testable without the enqueue
chain (SkillManager/ArtifactStore/bundle).
"""

from __future__ import annotations

from dataclasses import replace

from server.app.workflows.pi_config import PiConfig


def pi_config_for_runtime(pi: PiConfig, runtime: str) -> PiConfig:
    """Select the command builder for an Agent runtime (EXEC-RUNTIME-DISPATCH-001).

    ``runtime: velites`` pins the velites builder and ignores the global
    ``workflows.pi.flavor`` switch — flavor only selects the implementation
    for ``runtime: pi`` agents. Binary normalization follows the same default
    rule as ``PiRuntimeConfig._flavor_binary``. Unknown runtimes fail fast
    here so no manifest is ever frozen with an unbuildable command spec.
    """
    if runtime == "velites":
        return replace(
            pi,
            flavor="velites",
            binary="velites" if pi.binary == "pi" else pi.binary,
        )
    if runtime == "pi":
        return pi
    raise ValueError(
        f"Agent runtime {runtime!r} is not implemented yet (supported runtimes: pi, velites)"
    )
