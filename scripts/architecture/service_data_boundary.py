"""Ratchet guard: services must reach the database through the JobQueries
facade, not by opening their own connections.

Direction (architecture review P1): the service layer is being consolidated
onto ``server/app/jobs/queries`` (the established pattern — see
``services/job_pause.py`` and ~38 other facade-only services). Legacy
services that hand-write SQL or import ``server.app.db.transaction`` /
``connection`` directly are grandfathered by a baseline that only ratchets
down:

- a service file with raw SQL or DB-primitive imports but **no baseline
  entry** is an error (new bypasses are rejected);
- a count **above** the baseline entry is an error (no growth);
- counts below the baseline pass (cleanup lowers the baseline by hand or
  via the ratchet re-run).

Counted per service file (``server/app/services/**``):
1. SQL statement string literals (SELECT/INSERT/UPDATE/DELETE/CREATE/
   ALTER/DROP keyword match, mirroring the sql_placeholders scanner's
   keyword heuristic extended with DDL);
2. ``read_connection`` / ``write_transaction`` name references (imports or
   calls) — the DB-primitive escape hatch;
3. ``.path`` attribute access on a ``job_db``/``db``-shaped object — the
   DSN escape hatch (heuristic: attribute ``path`` on any Name).

The baseline loader lives in ``service_data_boundary_baseline.py``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts.architecture.service_data_boundary_baseline import (
    ServiceDataBoundaryConfigurationError,
    load_service_data_boundary_baseline,
)

__test__ = False

BASELINE_RELATIVE_PATH = "config/architecture/service-data-boundary-baseline.json"

_SQL_KEYWORD = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b", re.IGNORECASE)

_DB_PRIMITIVE_NAMES = {"read_connection", "write_transaction"}

_DB_PRIMITIVE_MODULES = ("transaction", "connection")

_SCAN_ROOT = "server/app/services"


def count_service_data_bypasses(source: str) -> tuple[int, int, int]:
    """Count (sql_literals, db_primitive_refs, dsn_path_refs) in one module."""
    tree = ast.parse(source)
    sql_literals = 0
    db_primitive_refs = 0
    dsn_path_refs = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _SQL_KEYWORD.search(node.value):
                sql_literals += 1
        elif isinstance(node, ast.Attribute):
            if node.attr == "path" and isinstance(node.value, ast.Name):
                dsn_path_refs += 1
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                from_primitive_module = (
                    module.startswith("server.app.db.")
                    and module.split(".")[-1] in _DB_PRIMITIVE_MODULES
                )
                # Package-level form (`from server.app.db import transaction`)
                # hands over the same primitives module — count it too.
                via_package = module == "server.app.db" and alias.name in _DB_PRIMITIVE_MODULES
                if alias.name in _DB_PRIMITIVE_NAMES or (
                    alias.name != "*" and (from_primitive_module or via_package)
                ):
                    db_primitive_refs += 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {f"server.app.db.{m}" for m in _DB_PRIMITIVE_MODULES}:
                    db_primitive_refs += 1
    return sql_literals, db_primitive_refs, dsn_path_refs


def collect_service_data_bypasses(root: Path) -> dict[str, tuple[int, int, int]]:
    """Count raw-SQL, DB-primitive and DSN-path usage per service file."""
    counts: dict[str, tuple[int, int, int]] = {}
    base = root / _SCAN_ROOT
    if not base.is_dir():
        return counts
    for path in sorted(base.rglob("*.py")):
        bypasses = count_service_data_bypasses(path.read_text(encoding="utf-8"))
        if any(bypasses):
            counts[path.relative_to(root).as_posix()] = bypasses
    return counts


def check_service_data_boundary(root: Path) -> list[str]:
    """Reject facade bypasses above the baseline or in new service files."""
    try:
        baseline = load_service_data_boundary_baseline(root / BASELINE_RELATIVE_PATH)
    except ServiceDataBoundaryConfigurationError as exc:
        return [f"service data boundary configuration: {exc}"]

    errors: list[str] = []
    for path, (sql_literals, db_primitive_refs, dsn_path_refs) in collect_service_data_bypasses(
        root
    ).items():
        allowed = baseline.files.get(path)
        if allowed is None:
            errors.append(
                f"{path}: {sql_literals} SQL literal(s) / {db_primitive_refs} "
                f"DB-primitive reference(s) / {dsn_path_refs} DSN path reference(s) "
                "with no baseline entry; new services must reach the database "
                "through JobQueries (jobs/queries)"
            )
        elif (
            sql_literals > allowed[0]
            or db_primitive_refs > allowed[1]
            or dsn_path_refs > allowed[2]
        ):
            errors.append(
                f"{path}: ({sql_literals}, {db_primitive_refs}, {dsn_path_refs}) "
                f"exceeds baseline {allowed}; route new DB access through "
                "JobQueries instead"
            )
    return sorted(errors)
