"""Build deterministic per-file PostgreSQL test timings from CI JUnit XML."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[1]


def _module_index(repo_root: Path) -> dict[str, str]:
    modules = {}
    for pattern in ("test_*.py", "*_test.py"):
        for path in (repo_root / "tests").rglob(pattern):
            relative = path.relative_to(repo_root)
            modules[".".join(relative.with_suffix("").parts)] = relative.as_posix()
    return modules


def build_manifest(
    junit_paths: list[Path], *, repo_root: Path = REPO_ROOT, source_run: str
) -> dict:
    modules = _module_index(repo_root)
    modules_by_specificity = sorted(modules, key=lambda name: (name.count("."), name), reverse=True)
    files: dict[str, list[float | int]] = defaultdict(lambda: [0.0, 0])
    seen: set[str] = set()
    for junit_path in junit_paths:
        for case in ElementTree.parse(junit_path).findall(".//testcase"):
            classname = case.get("classname", "")
            key = f"{classname}::{case.get('name', '')}"
            if key in seen:
                raise ValueError(f"duplicate JUnit testcase across inputs: {key}")
            seen.add(key)
            module = next(
                (
                    candidate
                    for candidate in modules_by_specificity
                    if classname == candidate or classname.startswith(f"{candidate}.")
                ),
                None,
            )
            if module is None:
                raise ValueError(f"cannot map JUnit classname to a test file: {classname}")
            seconds = float(case.get("time", "0"))
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError(f"invalid JUnit duration for {key}")
            entry = files[modules[module]]
            entry[0] += seconds
            entry[1] += 1
    return {
        "version": 1,
        "source_run": source_run,
        "files": {
            filename: {"seconds": round(values[0], 3), "tests": values[1]}
            for filename, values in sorted(files.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", action="append", required=True, type=Path)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo-root", default=REPO_ROOT, type=Path)
    args = parser.parse_args()
    manifest = build_manifest(args.junit, repo_root=args.repo_root, source_run=args.source_run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
