"""Nested pool checkout lint (DB-POOL-CHECKOUT-001; #1239 census productized, #1308).

All PostgreSQL connections come from ``pool_for(dsn)`` (server/app/db/pools.py):
``read_connection`` / ``write_transaction`` / ``connect_database`` and the
JobQueries facade methods (``read`` / ``write`` / ``connect`` / ``_connect_read``)
share the same per-(pid, dsn) pool, whose size can be configured down to 1
(``AGENT_LEGION_DB_POOL_MAX_SIZE``) with a 10s checkout timeout. A *nested
checkout* — acquiring a second pooled connection while the caller still holds
one — is therefore a bounded stall (PoolTimeout after 10s) at pool size 1 and
needless double occupancy at the default size; the #1239 family (M16 census,
M18 fixes) showed it leaking into hot paths (lease finish, Worker claim).

A lexical grep cannot tell a self-checkout callee from a conn-first same-named
helper (the census's representative false hits: workspace.py:310,
leases.py:120), so this check is AST-based:

1. Build the self-checkout set by introspection: any function whose dynamic
   execution may acquire a fresh pooled connection — directly (a checkout call)
   or transitively through callees resolved same-module + via imports (the
   census's two-layer resolution, iterated to a fixpoint).
2. Flag checkout ``with`` blocks whose body calls into that set, and lexically
   nested checkout ``with`` blocks (production code only — tests open two
   connections deliberately for concurrency fixtures).
3. Flag the riding→fresh transition: a conn-first function (a parameter named
   ``conn`` / ``*_conn`` / ``conn_*`` rides the caller's checkout) calling a
   non-conn-first member of the self-checkout set — the call nests a fresh
   checkout under whatever checkout the caller holds (#1226 conn-first
   contract). Deferred callbacks (nested ``def`` / ``lambda`` bodies) are
   excluded: they run after the block releases its checkout.

Known instances are pinned (path:line, both directions) in
``config/architecture/nested-pool-checkout-allowlist.json``; the allowlist
only shrinks — fixing the root cause makes its entries stale and the check
fails until they are removed.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

__test__ = False

ALLOWLIST_RELATIVE_PATH = "config/architecture/nested-pool-checkout-allowlist.json"

SCAN_ROOTS = ("server/app", "tests")
PRODUCTION_ROOT = "server/app/"
QUERIES_PREFIX = "server/app/jobs/queries/"

# Bare/attribute calls that acquire a pooled connection (server/app/db/).
_CHECKOUT_FUNCS = frozenset({"read_connection", "write_transaction", "connect_database"})
# JobQueries facade checkout methods (jobs/queries/connection.py).
_CHECKOUT_ATTRS = frozenset({"read", "write", "connect", "_connect_read"})

_CLOSURE_LIMIT = 12


class NestedPoolCheckoutConfigurationError(ValueError):
    """Allowlist configuration error captured by check_nested_pool_checkout."""


@dataclass
class _Module:
    rel: str
    name: str
    tree: ast.Module
    defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    # local name -> (absolute module, original name or None for module aliases)


def _dotted(expr: ast.AST) -> str | None:
    """Dotted rendering of a Name/Attribute chain without ast.unparse (hot path)."""
    parts: list[str] = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
        return ".".join(reversed(parts))
    return None


def _call_name(call: ast.Call) -> tuple[str | None, str]:
    func = call.func
    if isinstance(func, ast.Name):
        return None, func.id
    if isinstance(func, ast.Attribute):
        return _dotted(func.value), func.attr
    return None, ""


def _receiver_db_like(receiver: str) -> bool:
    base = receiver.split(".")[-1].split("[")[0]
    return "db" in base.lower() or "queries" in base.lower()


def _is_checkout_call(receiver: str | None, attr: str, in_queries: bool) -> bool:
    if receiver is None:
        return attr in _CHECKOUT_FUNCS
    if attr in _CHECKOUT_FUNCS:
        return True
    return attr in _CHECKOUT_ATTRS and (
        _receiver_db_like(receiver) or (receiver == "self" and in_queries)
    )


def _is_conn_first(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A conn-first function rides the caller's checkout (#1226 contract)."""
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    for arg in args:
        name = arg.arg
        if name in {"conn", "connection"} or name.startswith("conn_") or name.endswith("_conn"):
            return True
    return False


def _absolute_module(package_parts: list[str], node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    base = package_parts[: len(package_parts) - (node.level - 1)]
    if node.module:
        base = [*base, *node.module.split(".")]
    return ".".join(base) if base else None


def _parse_module(root: Path, path: Path) -> _Module | None:
    rel = path.relative_to(root).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (SyntaxError, UnicodeDecodeError):
        return None
    name = ".".join(path.relative_to(root).with_suffix("").parts)
    module = _Module(rel=rel, name=name, tree=tree)
    package_parts = name.split(".")[:-1]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module.defs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    module.defs[f"{node.name}.{sub.name}"] = sub
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module.imports[alias.asname or alias.name.split(".")[0]] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom):
            absolute = _absolute_module(package_parts, node)
            if absolute is None:
                continue
            for alias in node.names:
                if node.module is None and node.level:
                    module.imports[alias.asname or alias.name] = (f"{absolute}.{alias.name}", None)
                else:
                    module.imports[alias.asname or alias.name] = (absolute, alias.name)
    return module


def _build_modules(root: Path) -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for scan_root in SCAN_ROOTS:
        base = root / scan_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            module = _parse_module(root, path)
            if module is not None:
                modules[module.name] = module
    return modules


def _resolve(module: _Module, receiver: str | None, attr: str) -> list[tuple[str, str]]:
    """Resolve a call to candidate (module, qualname) pairs — same module first,
    then one import hop (the census's two-layer resolution)."""
    candidates: list[tuple[str, str]] = []
    if receiver is None:
        if attr in module.defs:
            candidates.append((module.name, attr))
        imported = module.imports.get(attr)
        if imported is not None and imported[1] is not None:
            candidates.append((imported[0], imported[1]))
        return candidates
    if receiver == "self":
        for qualname in module.defs:
            if qualname.endswith(f".{attr}"):
                candidates.append((module.name, qualname))
        return candidates
    imported = module.imports.get(receiver.split(".")[0])
    if imported is not None and imported[1] is None:
        candidates.append((imported[0], attr))
    return candidates


def _iter_own_calls(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Calls in a function body, excluding nested ``def`` / ``lambda`` scopes.

    Deferred callbacks (on_commit hooks, registered closures) execute after
    the registering checkout is released — they neither taint the enclosing
    function's self-checkout status nor nest under a held connection.
    """

    calls: list[ast.Call] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is not fn:
                return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node is not fn:
                return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_Call(self, node: ast.Call) -> None:
            calls.append(node)
            self.generic_visit(node)

    visitor = _Visitor()
    for stmt in fn.body:
        visitor.visit(stmt)
    return calls


def _called_candidates(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, module: _Module
) -> list[tuple[tuple[str, str], int, str]]:
    """All ((module, qualname), lineno, rendered) callee candidates of a body."""
    found: list[tuple[tuple[str, str], int, str]] = []
    for node in _iter_own_calls(fn):
        receiver, attr = _call_name(node)
        for candidate in _resolve(module, receiver, attr):
            found.append((candidate, node.lineno, attr))
    return found


def _directly_acquires(fn: ast.FunctionDef | ast.AsyncFunctionDef, in_queries: bool) -> bool:
    for node in _iter_own_calls(fn):
        receiver, attr = _call_name(node)
        if _is_checkout_call(receiver, attr, in_queries):
            return True
    return False


def _self_checkout_set(
    modules: dict[str, _Module],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], list[tuple[tuple[str, str], int, str]]]]:
    """Functions whose dynamic execution may acquire a fresh pooled connection.

    Returns the acquirer set plus the per-function callee candidate cache the
    hit scan reuses (resolution is the expensive half — compute it once).
    """
    acquirers: set[tuple[str, str]] = set()
    calls_cache: dict[tuple[str, str], list[tuple[tuple[str, str], int, str]]] = {}
    for name, module in modules.items():
        in_queries = module.rel.startswith(QUERIES_PREFIX)
        for qualname, fn in module.defs.items():
            calls_cache[(name, qualname)] = _called_candidates(fn, module)
            if _directly_acquires(fn, in_queries):
                acquirers.add((name, qualname))
    for _ in range(_CLOSURE_LIMIT):
        changed = False
        for key, candidates in calls_cache.items():
            if key in acquirers:
                continue
            if any(candidate in acquirers for candidate, _lineno, _r in candidates):
                acquirers.add(key)
                changed = True
        if not changed:
            break
    return acquirers, calls_cache


def _iter_block_calls(
    with_node: ast.With | ast.AsyncWith,
) -> tuple[list[ast.Call], list[ast.With | ast.AsyncWith]]:
    """Calls and nested with-blocks in a with body.

    Nested ``def`` / ``lambda`` bodies are excluded (deferred execution —
    an on_commit callback runs after the checkout is released), as are the
    context expressions of nested with-blocks (reported separately).
    """

    calls: list[ast.Call] = []
    nested_withs: list[ast.With | ast.AsyncWith] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_With(self, node: ast.With) -> None:
            nested_withs.append(node)
            for stmt in node.body:
                self.visit(stmt)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
            nested_withs.append(node)
            for stmt in node.body:
                self.visit(stmt)

        def visit_Call(self, node: ast.Call) -> None:
            calls.append(node)
            self.generic_visit(node)

    visitor = _Visitor()
    for stmt in with_node.body:
        visitor.visit(stmt)
    return calls, nested_withs


def _is_checkout_withitem(item: ast.withitem, in_queries: bool) -> bool:
    context = item.context_expr
    if isinstance(context, ast.Call):
        receiver, attr = _call_name(context)
        return _is_checkout_call(receiver, attr, in_queries)
    return False


def load_nested_pool_checkout_allowlist(path: Path) -> dict[str, str]:
    """Load the pinned known instances: ``path:lineno`` -> reason."""
    # A missing allowlist is an empty one, not an error: hits still fail
    # unallowlisted, so deleting the file equals emptying it. This keeps
    # fixture repos (tests build partial trees) free of governance plumbing.
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NestedPoolCheckoutConfigurationError(f"Malformed JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "allowlist"}:
        raise NestedPoolCheckoutConfigurationError(
            "Allowlist root must be a mapping with exactly 'version' and 'allowlist'"
        )
    if raw["version"] != 1:
        raise NestedPoolCheckoutConfigurationError(
            f"Unsupported allowlist version: {raw['version']!r}"
        )
    entries = raw["allowlist"]
    if not isinstance(entries, dict):
        raise NestedPoolCheckoutConfigurationError("allowlist must be a mapping")
    normalized: dict[str, str] = {}
    for key, value in entries.items():
        if not isinstance(key, str) or ":" not in key:
            raise NestedPoolCheckoutConfigurationError(
                f"allowlist key must be 'path:lineno': {key!r}"
            )
        if not isinstance(value, str) or not value.strip():
            raise NestedPoolCheckoutConfigurationError(
                f"allowlist reason must be non-empty: {key!r}"
            )
        normalized_key = str(PurePosixPath(key.rsplit(":", 1)[0])) + ":" + key.rsplit(":", 1)[1]
        normalized[normalized_key] = value
    return normalized


def _find_hits(root: Path) -> list[tuple[str, int, str]]:
    modules = _build_modules(root)
    acquirers, calls_cache = _self_checkout_set(modules)
    fn_keys: dict[int, tuple[str, str]] = {}
    for mod_name, module in modules.items():
        for qualname, fn in module.defs.items():
            fn_keys[id(fn)] = (mod_name, qualname)
    # JobQueries facade methods in the self-checkout set, matched by name for
    # attribute calls on db/queries-like receivers (job_db.get_x(...)).
    facade_methods = {
        qualname.split(".", 1)[1]
        for name, qualname in acquirers
        if name in modules and modules[name].rel.startswith(QUERIES_PREFIX) and "." in qualname
    }

    hits: list[tuple[str, int, str]] = []
    for module in modules.values():
        in_queries = module.rel.startswith(QUERIES_PREFIX)
        in_production = module.rel.startswith(PRODUCTION_ROOT)
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                if not any(_is_checkout_withitem(item, in_queries) for item in node.items):
                    continue
                calls, nested_withs = _iter_block_calls(node)
                if in_production:
                    for nested in nested_withs:
                        if any(_is_checkout_withitem(item, in_queries) for item in nested.items):
                            hits.append(
                                (
                                    module.rel,
                                    nested.lineno,
                                    "lexically nested pool checkout inside the "
                                    f"checkout block at line {node.lineno}",
                                )
                            )
                for call in calls:
                    receiver, attr = _call_name(call)
                    detail = None
                    for candidate in _resolve(module, receiver, attr):
                        if candidate in acquirers:
                            detail = (
                                f"call to self-checkout {candidate[1]} "
                                f"({candidate[0]}) inside the checkout block "
                                f"at line {node.lineno}"
                            )
                            break
                    if (
                        detail is None
                        and receiver is not None
                        and receiver != "self"
                        and _receiver_db_like(receiver)
                        and attr in facade_methods
                    ):
                        detail = (
                            f"call to JobQueries self-checkout method {attr} "
                            f"inside the checkout block at line {node.lineno}"
                        )
                    if detail is not None:
                        hits.append((module.rel, call.lineno, detail))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _is_conn_first(node):
                    continue
                # Nested named functions are not in the closure cache (deferred
                # scopes never taint), but a conn-first nested function is still
                # analyzed on the fly — a helper closure that self-checkouts
                # nests under the held connection all the same.
                own_calls = calls_cache.get(fn_keys.get(id(node), ("", "")))
                if own_calls is None:
                    own_calls = _called_candidates(node, module)
                for call in _iter_own_calls(node):
                    receiver, attr = _call_name(call)
                    if (
                        receiver is not None
                        and receiver != "self"
                        and _receiver_db_like(receiver)
                        and attr in facade_methods
                    ):
                        hits.append(
                            (
                                module.rel,
                                call.lineno,
                                f"conn-first function {node.name} calls JobQueries "
                                f"self-checkout method {attr}: a fresh checkout nests "
                                "under the caller's held connection",
                            )
                        )
                for candidate, lineno, rendered in own_calls:
                    if candidate not in acquirers:
                        continue
                    target = modules.get(candidate[0])
                    target_fn = target.defs.get(candidate[1]) if target else None
                    if target_fn is not None and _is_conn_first(target_fn):
                        continue  # the ride continues; not a fresh checkout
                    hits.append(
                        (
                            module.rel,
                            lineno,
                            f"conn-first function {node.name} calls self-checkout "
                            f"{rendered} ({candidate[0]}): a fresh checkout nests "
                            "under the caller's held connection",
                        )
                    )
    return hits


def check_nested_pool_checkout(root: Path) -> list[str]:
    """Reject nested pool checkouts beyond the pinned allowlist (#1308)."""
    try:
        allowlist = load_nested_pool_checkout_allowlist(root / ALLOWLIST_RELATIVE_PATH)
    except NestedPoolCheckoutConfigurationError as exc:
        return [f"nested pool checkout configuration: {exc}"]

    hits = _find_hits(root)
    hit_keys = {f"{rel}:{lineno}" for rel, lineno, _detail in hits}

    errors: list[str] = []
    for rel, lineno, detail in sorted(hits, key=lambda hit: (hit[0], hit[1])):
        key = f"{rel}:{lineno}"
        if key in allowlist:
            continue
        errors.append(
            f"{key}: nested pool checkout (DB-POOL-CHECKOUT-001): {detail}; "
            "split into sequential checkouts or ride the caller's connection "
            "(conn-first); known instances need an allowlist entry with a reason in "
            f"{ALLOWLIST_RELATIVE_PATH}"
        )
    for key in sorted(set(allowlist) - hit_keys):
        errors.append(
            f"{key}: stale nested-pool-checkout allowlist entry; "
            "the instance is gone — remove the entry (allowlist only shrinks)"
        )
    return errors
