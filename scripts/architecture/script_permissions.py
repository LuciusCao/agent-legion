"""Tracked shell scripts must carry the executable bit (#623).

``make install`` on a fresh clone runs ``./scripts/install-deps.sh`` directly;
a script tracked as ``100644`` boots as ``Permission denied`` on
core.fileMode=true platforms (macOS/Linux), while a locally chmod-ed checkout
or Windows hides the breakage until CI. Rule: every ``*.sh`` in the git index
must be ``100755`` — checking the index (not the filesystem) means a local
``chmod +x`` without staging still fails the gate until the mode change is
committed.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

__test__ = False


def parse_index_modes(lines: Iterable[str]) -> dict[str, str]:
    """Parse ``git ls-files -s`` output lines into ``{path: mode}``.

    Input format per line: ``<mode> <sha> <stage>\\t<path>``. Paths containing
    spaces survive because the separator is a literal tab.
    """
    modes: dict[str, str] = {}
    for line in lines:
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and path:
            modes[path] = fields[0]
    return modes


def check_script_exec_bits(index_modes: dict[str, str]) -> list[str]:
    """Every tracked regular-file ``*.sh`` must be mode 100755.

    Symlinks (120000) and submodule entries (160000) are exempt: neither
    carries a chmod-able mode, so flagging them is an unactionable false
    positive (subagent review P2)."""
    return [
        f"{path}: tracked shell script is {mode}, must be 100755 "
        "(chmod +x and commit the mode change; direct `./scripts/...` "
        "invocations fail on fresh clones otherwise, #623)"
        for path, mode in sorted(index_modes.items())
        if path.endswith(".sh") and mode.startswith("100") and mode != "100755"
    ]


def check_script_permissions(root: Path) -> list[str]:
    """Gate entry: query the index via git; skip silently without git metadata
    (synthetic test layouts, non-repo exports)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-s", "--", "*.sh"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return check_script_exec_bits(parse_index_modes(result.stdout.splitlines()))
