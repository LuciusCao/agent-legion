"""Every compose ``down`` entry must stop profile-gated services (#1105).

``docker compose down`` without ``--profile`` skips services that sit behind
a profile, so the local object-storage containers (seaweedfs / rustfs) keep
running and the stack network cannot be removed. The Makefile down targets
therefore pass every profile declared by the compose files they operate on.

The test renders the real recipes with ``make -n`` (no docker involved) and
derives the required profile set from the compose files themselves, so a new
profile added to a compose file without wiring it into down fails here.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]

# Every Makefile target (with its goal arguments) that runs ``compose down``.
_DOWN_GOALS = (
    ("prod-down", "docker"),
    ("stack-host-down",),
    ("stack-worker-down",),
    ("stack-down",),
)


def _declared_profiles(compose_file: Path) -> set[str]:
    data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    profiles: set[str] = set()
    for service in (data.get("services") or {}).values():
        profiles.update((service or {}).get("profiles") or ())
    return profiles


def _render(goals: tuple[str, ...]) -> list[list[str]]:
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is required to render Makefile recipes")
    result = subprocess.run(
        [make, "-n", "-C", str(ROOT), *goals],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    commands = []
    for line in result.stdout.splitlines():
        argv = shlex.split(line.lstrip("-"))
        if argv[:2] == ["docker", "compose"] and "down" in argv:
            commands.append(argv)
    return commands


def _split_compose_args(argv: list[str]) -> tuple[list[str], set[str], int]:
    files: list[str] = []
    profiles: set[str] = set()
    index = 2
    while index < len(argv) and argv[index] != "down":
        flag = argv[index]
        if flag == "-f":
            files.append(argv[index + 1])
            index += 2
        elif flag == "--profile":
            profiles.add(argv[index + 1])
            index += 2
        else:
            index += 1
    return files, profiles, index


def test_host_compose_declares_local_storage_profiles() -> None:
    """Anchor: the profiles the fix exists for are still declared."""
    assert _declared_profiles(ROOT / "deploy" / "compose.host.yaml") >= {
        "materials-local",
        "materials-local-rustfs",
    }


@pytest.mark.parametrize("goals", _DOWN_GOALS, ids=lambda goals: " ".join(goals))
def test_down_passes_every_declared_profile(goals: tuple[str, ...]) -> None:
    commands = _render(goals)
    assert commands, f"make {' '.join(goals)} rendered no compose down command"
    for argv in commands:
        files, profiles, down_at = _split_compose_args(argv)
        assert down_at < len(argv), argv
        assert files, f"compose down without -f: {argv}"
        required: set[str] = set()
        for name in files:
            required |= _declared_profiles(ROOT / name)
        missing = required - profiles
        assert not missing, f"{' '.join(argv)} misses --profile {sorted(missing)}"


def test_host_down_covers_both_local_storage_profiles() -> None:
    """Explicit regression for #1105 on the documented stop entry."""
    (command,) = _render(("prod-down", "docker"))
    text = " ".join(command)
    assert re.search(r"--profile materials-local\b(?!-)", text)
    assert "--profile materials-local-rustfs" in text
    # Profiles are global options: they must precede the subcommand.
    assert text.index("--profile") < text.index(" down")
