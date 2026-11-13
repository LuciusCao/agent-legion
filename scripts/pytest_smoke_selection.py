"""Resolve the exact smoke-tier file set without collecting the full test tree."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IGNORED_TIER_DIRS = {"ci", "full"}


def smoke_test_files(repo_root: Path = REPO_ROOT) -> list[str]:
    manifest_path = repo_root / "config" / "architecture" / "smoke-test-files.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_files = payload.get("files")
    if not isinstance(manifest_files, list) or not all(
        isinstance(entry, str) for entry in manifest_files
    ):
        raise ValueError(f"malformed smoke manifest: {manifest_path}")
    for entry in manifest_files:
        path = Path(entry)
        if path.is_absolute() or not path.parts or path.parts[0] != "tests" or ".." in path.parts:
            raise ValueError(f"smoke manifest path escapes tests/: {entry!r}")
    tests_root = repo_root / "tests"
    selected = {
        entry
        for entry in manifest_files
        if len(Path(entry).parts) > 1 and Path(entry).parts[1] not in IGNORED_TIER_DIRS
    }
    selected.update(
        path.relative_to(repo_root).as_posix()
        for path in tests_root.rglob("test_architecture_*.py")
        if path.relative_to(tests_root).parts[0] not in IGNORED_TIER_DIRS
    )
    missing = [entry for entry in selected if not (repo_root / entry).is_file()]
    if missing:
        raise ValueError(f"smoke manifest references missing files: {sorted(missing)}")
    if not selected:
        raise ValueError("smoke selection is empty")
    return sorted(selected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=REPO_ROOT, type=Path)
    args = parser.parse_args()
    for filename in smoke_test_files(args.repo_root):
        print(filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
