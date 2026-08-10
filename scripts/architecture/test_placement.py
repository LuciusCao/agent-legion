"""Guard against new test files at the ``tests/`` root.

AGENTS.md §4（质量门）requires new tests to live in the matching subsystem
subdirectory (``tests/services/``, ``tests/scripts/``, ``tests/workers/``,
…); the root is frozen to the files recorded in
``config/architecture/test-root-files-baseline.json``. Any ``*.py`` directly
under ``tests/`` that is not in the baseline is an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__test__ = False

BASELINE_RELATIVE_PATH = "config/architecture/test-root-files-baseline.json"


@dataclass(frozen=True)
class TestRootBaseline:
    files: frozenset[str]


class _TestPlacementConfigurationError(ValueError):
    """Internal configuration error captured by check_test_placement."""


def load_test_root_baseline(path: Path) -> TestRootBaseline:
    """Require exactly version 1 and a normalized tests/-root file list."""
    if not path.is_file():
        raise _TestPlacementConfigurationError(f"Baseline file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _TestPlacementConfigurationError(f"Malformed JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict) or set(raw) != {"version", "files"}:
        raise _TestPlacementConfigurationError(
            "Baseline root must be a mapping with exactly {version, files}"
        )
    if raw.get("version") != 1:
        raise _TestPlacementConfigurationError(
            f"Unsupported baseline version: {raw.get('version')!r}"
        )
    files = raw.get("files")
    if not isinstance(files, list):
        raise _TestPlacementConfigurationError("files must be a list")

    normalized: set[str] = set()
    for entry in files:
        if not isinstance(entry, str):
            raise _TestPlacementConfigurationError("baseline entries must be strings")
        key = str(PurePosixPath(entry))
        if not key.startswith("tests/") or "/" in key.removeprefix("tests/"):
            raise _TestPlacementConfigurationError(
                f"baseline entry is not a tests/ root file: {key}"
            )
        if key in normalized:
            raise _TestPlacementConfigurationError(f"duplicate baseline entry: {key}")
        normalized.add(key)
    return TestRootBaseline(files=frozenset(normalized))


def check_test_placement(root: Path) -> list[str]:
    """Reject ``*.py`` files directly under ``tests/`` that are not baselined."""
    try:
        baseline = load_test_root_baseline(root / BASELINE_RELATIVE_PATH)
    except _TestPlacementConfigurationError as exc:
        return [f"test placement configuration: {exc}"]

    tests_root = root / "tests"
    if not tests_root.is_dir():
        return []
    errors = []
    for path in sorted(tests_root.glob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative not in baseline.files:
            errors.append(
                f"{relative}: new test file at tests/ root; move it into the matching "
                "subsystem subdirectory (AGENTS.md §4 质量门)"
            )
    return errors
