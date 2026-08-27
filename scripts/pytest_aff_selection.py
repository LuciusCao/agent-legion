"""Affected-test selection for the backend lane (agent inner loop).

Two cooperating pieces:

* ``AffIndexBuilder`` — a pytest plugin (``-p scripts.pytest_aff_index``)
  that runs the suite with ``--cov-context=test`` and post-processes the
  coverage SQLite data into a compact JSON mapping ``source file ->
  [test nodeid]``. One full indexed run primes the index; the index is
  invalidated by tracked-source changes outside the recorded mapping (the
  gate falls back to the full tier until it is rebuilt).
* ``select_affected_tests`` / the ``agent-legion-aff`` CLI — selects the
  tests whose recorded coverage intersects the changed source files and
  prints a pytest ``--deselect``-compatible filter (``--select`` via
  ``-k``-free nodeid matching happens in the ``GATE_TIER=aff`` backend
  path: scripts/check-quick-backend.sh passes explicit nodeids).

Semantics and guardrails:

* Tests never seen by the indexer (new tests, or tests whose file is new)
  always run — the selection is a *superset* of affected tests.
* Only files under the repo's tracked source roots (``server/``,
  ``worker/``, ``shared/``, ``workflow_nodes/``, ``scripts/``,
  ``workspace_libs/``, ``tests/``) are mapped; anything else (venv,
  generated, data) is ignored.
* The index stores the pytest nodeids verbatim; a nodeid that no longer
  exists is dropped at selection time (collection failure would otherwise
  fail the run).
* This is an inner-loop accelerator only: a pass of the selected subset is
  NOT a gate pass. The full suite remains the pre-push/CI boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO_ROOT / ".pytest-aff-index.json"
_GIT_PATH_PREFIXES = (
    "server/",
    "worker/",
    "shared/",
    "workflow_nodes/",
    "scripts/",
    "workspace_libs/",
    "tests/",
)


def _repo_relative(path: str, repo_root: Path | None = None) -> str | None:
    """Map an absolute (possibly /private-prefixed) path to a repo-relative
    POSIX path, or None when the file lives outside the repository."""
    root = repo_root if repo_root is not None else REPO_ROOT
    current = Path(path)
    seen: list[str] = []
    while True:
        seen.append(current.name)
        parent = current.parent
        if parent == current:
            return None
        current = parent
        if current == root:
            return "/".join(reversed(seen))


def changed_source_files(base: str | None) -> list[str]:
    """Repo-relative changed files (uncommitted + committed vs base)."""
    files: set[str] = set()
    commands = [["git", "status", "--porcelain=v1", "--untracked-files=all"]]
    if base:
        commands.append(["git", "diff", "--name-only", f"{base}..HEAD"])
    for command in commands:
        try:
            output = subprocess.run(
                command, capture_output=True, text=True, check=True, cwd=REPO_ROOT
            ).stdout
        except (subprocess.CalledProcessError, OSError):
            continue
        for line in output.splitlines():
            path = line[3:] if line[:2] in ("??", "!!") else line[3:]
            path = path.split(" -> ")[-1].strip()
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            if path.startswith(_GIT_PATH_PREFIXES):
                files.add(path)
    return sorted(files)


def build_index_from_coverage(
    coverage_file: Path, repo_root: Path | None = None
) -> dict[str, list[str]]:
    """Extract ``{repo-relative source file: [nodeid]}`` from a coverage
    SQLite data file produced with ``--cov-context=test``."""
    conn = sqlite3.connect(f"file:{coverage_file}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            select distinct f.path, c.context
            from line_bits lb
            join file f on f.id = lb.file_id
            join context c on c.id = lb.context_id
            union
            select distinct f.path, c.context
            from arc a
            join file f on f.id = a.file_id
            join context c on c.id = a.context_id
            """
        ).fetchall()
    finally:
        conn.close()
    mapping: dict[str, set[str]] = {}
    for raw_path, context in rows:
        if not context or not context.endswith("|run"):
            continue
        nodeid = context[: -len("|run")]
        if "::" not in nodeid:
            continue
        rel = _repo_relative(raw_path, repo_root)
        if rel is None:
            continue
        mapping.setdefault(rel, set()).add(nodeid)
    return {path: sorted(nodeids) for path, nodeids in sorted(mapping.items())}


def load_index() -> dict[str, list[str]] | None:
    if not INDEX_PATH.exists():
        return None
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    files = data.get("files")
    if not isinstance(files, dict):
        return None
    return files


def save_index(mapping: dict[str, list[str]], base_commit: str) -> None:
    payload = {
        "version": 1,
        "base_commit": base_commit,
        "files": mapping,
    }
    tmp = INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(tmp, INDEX_PATH)


def select_affected_tests(
    changed: list[str], mapping: dict[str, list[str]], repo_root: Path | None = None
) -> list[str]:
    """Union of tests covering any changed file; unknown/new test files run
    wholesale (conservative superset).

    Deleted paths are dropped at the nodeid level: a test file removed
    relative to the merge base has no tests to run — its recorded nodeids are
    stale, and passing the stale path to pytest would fail collection for as
    long as the deletion sits in the diff (rebuilding the index would not
    recover either, because the diff against the merge base keeps listing the
    deleted file).
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    selected: set[str] = set()
    for path in changed:
        nodeids = mapping.get(path)
        if nodeids is not None:
            for nodeid in nodeids:
                if _nodeid_file_exists(nodeid, root):
                    selected.add(nodeid)
        elif path.startswith("tests/") and (root / path).is_file():
            # A changed test file with no coverage record (new file, or the
            # indexer never ran it): every test in that file must run.
            selected.add(path)
    return sorted(selected)


def _nodeid_file_exists(nodeid: str, root: Path) -> bool:
    """True when the collection file part of a nodeid still exists.

    A nodeid looks like ``tests/path/test_x.py::TestClass::test_case``; only
    the file part is checked — class/function targets are validated by
    pytest collection itself, and an unknown name there deselects cleanly.
    """
    file_part = nodeid.split("::", 1)[0]
    return (root / file_part).is_file()


def unmapped_source_files(
    changed: list[str], mapping: dict[str, list[str]], repo_root: Path | None = None
) -> list[str]:
    """Changed non-test source files the index has no record of.

    The aff tier refuses to select when this is non-empty (the gate falls
    back to the full unit tier): a source file outside the index means the
    index was built without coverage for that tree (e.g. a --cov root was
    missed, or a brand-new module), so the tests it affects are unknown.
    Falling back only widens what runs — the documented aff semantics.
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    unmapped: list[str] = []
    for path in changed:
        if path.startswith("tests/"):
            continue
        if mapping.get(path) is None and (root / path).is_file():
            unmapped.append(path)
    return sorted(unmapped)


def _cmd_build(args: argparse.Namespace) -> int:
    mapping = build_index_from_coverage(Path(args.coverage_file))
    if not mapping:
        print("No test contexts found in coverage data.", file=sys.stderr)
        return 1
    save_index(mapping, args.base_commit or "")
    print(f"Indexed {len(mapping)} source files into {INDEX_PATH}")
    return 0


def _cmd_select(args: argparse.Namespace) -> int:
    mapping = load_index()
    if mapping is None:
        # No index: print nothing (caller falls back to the full tier).
        print("no-index", file=sys.stderr)
        return 3
    changed = changed_source_files(args.base)
    unmapped = unmapped_source_files(changed, mapping)
    if unmapped:
        # A changed source file the index cannot see: the affected tests are
        # unknown, so refuse to select (exit 4) and let the gate run the full
        # unit tier instead of silently skipping those tests.
        print("unmapped-source-files", file=sys.stderr)
        for path in unmapped:
            print(f"# unmapped {path}", file=sys.stderr)
        return 4
    selected = select_affected_tests(changed, mapping)
    for nodeid in selected:
        print(nodeid)
    print(f"# selected {len(selected)} of {len(changed)} changed file(s)", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build the index from a coverage data file")
    build.add_argument("coverage_file")
    build.add_argument("--base-commit", default="")
    build.set_defaults(func=_cmd_build)

    select = sub.add_parser("select", help="print affected test nodeids")
    select.add_argument("--base", default=None)
    select.set_defaults(func=_cmd_select)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
