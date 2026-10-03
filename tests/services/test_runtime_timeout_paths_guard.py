"""Structural guard for CONFIG-RUNTIME-TIMEOUT-001 execution paths (#869).

#869 was a local execution path (the local shard fallback) that built its
``ExecutionContext`` without going through the dispatch decision entry, so
the timeout matrix never saw it. This guard makes the path list
self-maintaining: it AST-scans ``server/app`` for every function that
constructs an ``ExecutionContext`` (an execution about to run or be staged)
or an ``AgentExecutionRequest`` (a queued remote execution) and requires each
one to be

1. registered in ``SITES`` below with the matrix paths it is driven through
   (``tests/helpers/runtime_timeout_matrix.py::PATHS``, driven by
   ``tests/services/test_runtime_timeout_matrix.py``), and
2. wired to its decision: the registered evidence identifier must be
   referenced in the named function (local paths: the shared dispatch entry
   ``local_dispatch.decide_local_code_dispatch``; remote paths: the enqueue-time
   ``timeout_base`` the Worker claim decides from). Local sites must also hand
   the decided ``node_config`` to the context.

A new execution path therefore fails here until it is wired to the decision
and added to the matrix. Deliberately narrow: names, not call graphs.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.helpers.runtime_timeout_matrix import PATHS, matrix_cases

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSTRUCTORS = frozenset({"ExecutionContext", "AgentExecutionRequest"})

WORKER = "server/app/workflow_worker"
BROKER = "server/app/agent_broker"


@dataclass(frozen=True)
class Site:
    paths: tuple[str, ...]
    # (file, qualified function, identifier) that must be referenced.
    evidence: tuple[str, str, str]
    # Local sites run the context directly: it must carry the decided config.
    carries_node_config: bool = False


SITES: dict[tuple[str, str], Site] = {
    # Ordinary local code dispatch: claim_submit decides, the pass-end flush
    # builds the context from the buffered PreparedClaim.
    (f"{WORKER}/claim_flush.py", "flush_prepared_claims"): Site(
        paths=("local",),
        evidence=(
            f"{WORKER}/claim_submit.py",
            "try_claim_and_submit",
            "decide_local_code_dispatch",
        ),
        carries_node_config=True,
    ),
    (f"{WORKER}/shard_dispatch.py", "claim_shard_locally"): Site(
        paths=("local_shard",),
        evidence=(
            f"{WORKER}/shard_dispatch.py",
            "claim_shard_locally",
            "decide_local_code_dispatch",
        ),
        carries_node_config=True,
    ),
    (f"{BROKER}/code_dispatch.py", "CodeDispatchService.enqueue"): Site(
        paths=("single", "batch", "legacy", "remote_shard"),
        evidence=(
            f"{BROKER}/code_dispatch.py",
            "CodeDispatchService.enqueue",
            "TIMEOUT_BASE_MANIFEST_KEY",
        ),
    ),
    (f"{BROKER}/dispatch.py", "AgentDispatchService.enqueue"): Site(
        paths=("single", "batch", "legacy"),
        evidence=(
            f"{BROKER}/dispatch.py",
            "AgentDispatchService.enqueue",
            "TIMEOUT_BASE_MANIFEST_KEY",
        ),
    ),
}


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Qualified name → function node (module functions and class methods)."""
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[f"{node.name}.{item.name}"] = item
    return found


def _constructor_calls(function: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in CONSTRUCTORS
    ]


def _references(function: ast.AST, identifier: str) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id == identifier:
            return True
        if isinstance(node, ast.Attribute) and node.attr == identifier:
            return True
    return False


def _parse(relpath: str) -> ast.Module:
    return ast.parse((REPO_ROOT / relpath).read_text(encoding="utf-8"))


def _scan_sites() -> dict[tuple[str, str], list[ast.Call]]:
    sites: dict[tuple[str, str], list[ast.Call]] = {}
    for path in sorted((REPO_ROOT / "server" / "app").rglob("*.py")):
        relpath = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, function in _functions(tree).items():
            calls = _constructor_calls(function)
            if calls:
                sites[(relpath, name)] = calls
    # A construction outside any function/method would escape the scan.
    return sites


def test_every_execution_construction_site_is_registered() -> None:
    scanned = _scan_sites()
    unregistered = sorted(set(scanned) - set(SITES))
    stale = sorted(set(SITES) - set(scanned))
    assert not unregistered, (
        "new execution path(s) build an ExecutionContext / AgentExecutionRequest: wire them "
        "to the timeout decision, add them to the timeout matrix and register them in "
        f"SITES: {unregistered}"
    )
    assert not stale, f"registered sites no longer construct executions: {stale}"


def test_registered_sites_map_onto_matrix_paths() -> None:
    driven = {path for _kind, path, _layer, _timing in matrix_cases()}
    for key, site in SITES.items():
        assert site.paths, key
        assert set(site.paths) <= set(PATHS), (key, site.paths)
        assert set(site.paths) <= driven, f"{key}: path(s) not driven by the matrix"


@pytest.mark.parametrize("key", sorted(SITES))
def test_registered_sites_go_through_the_decision(key: tuple[str, str]) -> None:
    site = SITES[key]
    evidence_file, evidence_function, identifier = site.evidence
    function = _functions(_parse(evidence_file)).get(evidence_function)
    assert function is not None, f"{evidence_file}::{evidence_function} not found"
    assert _references(function, identifier), (
        f"{evidence_file}::{evidence_function} no longer references {identifier}"
    )
    if site.carries_node_config:
        calls = _scan_sites()[key]
        contexts = [call for call in calls if call.func.id == "ExecutionContext"]
        assert contexts and all(
            any(keyword.arg == "node_config" for keyword in call.keywords) for call in contexts
        ), f"{key}: the local ExecutionContext must carry the decided node_config"
