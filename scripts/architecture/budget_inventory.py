"""Closed-world inventory scanner for source-file budget governance."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from .budget_policy import BudgetPolicy

__test__ = False


@dataclass(frozen=True)
class BudgetInventory:
    production: tuple[str, ...]
    tests: tuple[str, ...]
    excluded: tuple[str, ...]


def build_budget_inventory(root: Path, policy: BudgetPolicy) -> tuple[BudgetInventory, list[str]]:
    """Scan policy roots and return uniquely classified POSIX paths."""
    errors: list[str] = []
    test_paths: set[str] = set()
    production_paths: set[str] = set()

    # 1. Classify tests first.
    for test_root in policy.test_roots:
        test_dir = root / test_root.path
        if not test_dir.is_dir():
            state = "does not exist" if not test_dir.exists() else "is not a directory"
            errors.append(f"configured root {state}: {test_root.path}")
            continue
        for path in _walk_files(root, test_dir):
            rel_root = _as_posix(path.relative_to(test_dir))
            if _matches_any_pattern(rel_root, test_root.patterns):
                repo_rel = _as_posix(path.relative_to(root))
                # Tests take precedence over production and exclusion by design; this is
                # not a duplicate classification error.
                test_paths.add(repo_rel)

    # 2. Classify production files.
    for prod_root in policy.production_roots:
        prod_dir = root / prod_root.path
        if not prod_dir.is_dir():
            state = "does not exist" if not prod_dir.exists() else "is not a directory"
            errors.append(f"configured root {state}: {prod_root.path}")
            continue
        for path in _walk_files(root, prod_dir):
            repo_rel = _as_posix(path.relative_to(root))
            if repo_rel in test_paths:
                # Tests take precedence; not a duplicate because tests were classified first.
                continue
            if any(repo_rel.endswith(ext) for ext in prod_root.extensions):
                if repo_rel in production_paths:
                    errors.append(
                        f"duplicate classification: {repo_rel} matches multiple production roots"
                    )
                    production_paths.discard(repo_rel)
                    continue
                production_paths.add(repo_rel)

    # 3. Apply production exclusions.
    excluded_paths: set[str] = set()
    for exclude_glob in policy.production_exclude:
        matched = False
        for prod_path in list(production_paths):
            if _matches_exclude(prod_path, exclude_glob):
                matched = True
                excluded_paths.add(prod_path)
                production_paths.discard(prod_path)
        if not matched:
            errors.append(f"exclude glob matched no production file: {exclude_glob}")

    inventory = BudgetInventory(
        production=tuple(sorted(production_paths)),
        tests=tuple(sorted(test_paths)),
        excluded=tuple(sorted(excluded_paths)),
    )
    return inventory, sorted(errors)


def _walk_files(root: Path, start_dir: Path):
    """Yield regular files under start_dir, without following directory symlinks."""
    for dirpath, dirnames, filenames in os.walk(start_dir, followlinks=False):
        dir_path = Path(dirpath)
        # Prune symlinked subdirectories to match the no-directory-symlink rule.
        dirnames[:] = [d for d in dirnames if not (dir_path / d).is_symlink()]
        for filename in filenames:
            file_path = dir_path / filename
            if file_path.is_symlink():
                continue
            if file_path.is_file():
                yield file_path


def _as_posix(path: Path) -> str:
    return path.as_posix()


def _matches_any_pattern(rel_path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches_pattern(rel_path, pattern) for pattern in patterns)


def _matches_pattern(rel_path: str, pattern: str) -> bool:
    return _matches_glob(rel_path, pattern)


def _matches_exclude(rel_path: str, exclude_glob: str) -> bool:
    """Match a path against an exclude glob anchored at the repository root."""
    return _matches_glob(rel_path, exclude_glob, allow_dir_prefix=True)


def _matches_glob(rel_path: str, pattern: str, *, allow_dir_prefix: bool = False) -> bool:
    if fnmatch.fnmatch(rel_path, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatch(rel_path, pattern[3:]):
        return True
    if allow_dir_prefix and pattern.endswith("/**"):
        prefix = pattern[:-3]
        if rel_path == prefix or rel_path.startswith(prefix + "/"):
            return True
    return False
