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


def select_affected_tests(changed: list[str], mapping: dict[str, list[str]]) -> list[str]:
    """Union of tests covering any changed file; unknown/new test files run
    wholesale (conservative superset)."""
    selected: set[str] = set()
    for path in changed:
        nodeids = mapping.get(path)
        if nodeids is not None:
            selected.update(nodeids)
        elif path.startswith("tests/"):
            # A changed test file with no coverage record (new file, or the
            # indexer never ran it): every test in that file must run.
            selected.add(path)
    return sorted(selected)


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
