"""Zero-reference export lint (#732): catch "callers deleted, implementation left" dead code.

The review-0916 legacy cleanups (#328 shadow layer, skill_version.py, …) all
came from one shape: a migration PR deletes every caller but forgets the
implementation, and no gate noticed. This check is the knip/vulture-style
guard the issue asks for, unified across the two stacks:

- backend (Python): public module-level ``def`` / ``class`` / assignments in
  ``server/app``, ``worker``, ``shared``, ``workspace_libs`` and
  ``workflow_nodes``. Definitions with known registration decorators (route
  decorators, pytest fixtures and ``@entrypoint``) are exempt; transforming
  decorators such as ``@dataclass`` and ``@lru_cache`` do not hide dead code.
  ``__init__.py`` re-export surfaces and ``_``-private names are also exempt.
  ``scripts/``
  is out of scope — those modules are invoked by path from the Makefile/CI.
- frontend (TypeScript): ``export <kind> NAME`` and ``export { … }`` symbols
  under ``frontend/src`` (``.ts``/``.tsx``), excluding ``export default``
  (imported under arbitrary local names) and ``frontend/src/generated/``
  (regenerated from the OpenAPI schema; unused exports there are normal).

Reference counting is deliberately textual: one pass tokenizes every text file
in the repository (code, yaml, markdown, json, shell, sql), so dynamic and
cross-language consumers — ``__all__`` re-exports, pytest fixture parameter
names, yaml capability strings, docstring mentions — all count as references.
That makes the check conservative in the safe direction: a false *negative*
needs the name quoted somewhere, a false *positive* is near-impossible.

A definition is flagged when its identifier occurs nowhere repository-wide
beyond its declaration sites. Frontend barrel re-exports are declaration
sites too: their tokens are deducted instead of acting as self-justifying
consumers (#1380). Common names (``main``, ``run``, …) self-exempt through
sheer occurrence count. Surviving known-dead symbols are
pinned with reasons in
``config/architecture/zero-reference-exports-allowlist.json``, matched both
directions — deleting the dead symbol makes its pin stale and fails the check
until the pin is removed (the allowlist only shrinks).
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.architecture.zero_reference_decorators import registers_definition

__test__ = False

ALLOWLIST_RELATIVE_PATH = "config/architecture/zero-reference-exports-allowlist.json"

_BACKEND_ROOTS = ("server/app", "worker", "shared", "workspace_libs", "workflow_nodes")
_FRONTEND_ROOTS = ("frontend/src",)
_FRONTEND_EXCLUDED_PREFIXES = ("frontend/src/generated/",)
_FRONTEND_EXTENSIONS = frozenset({".ts", ".tsx"})

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Build outputs, dependency trees, runtime data and VCS internals are not
# reference sources.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        ".tower",
        ".worktrees",
        "__pycache__",
        "node_modules",
        "dist",
        "coverage",
        "target",
        "data",
    }
)
_REFERENCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".md",
        ".sh",
        ".sql",
        ".html",
        ".css",
        ".rs",
    }
)

_EXPORT_NAMED = re.compile(
    r"^\s*export\s+(?:declare\s+)?(?:async\s+)?"
    r"(?:function|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)
_EXPORT_BRACE = re.compile(r"^\s*export\s*\{([^}]*)\}", re.MULTILINE)
_EXPORT_BRACE_MEMBER = re.compile(
    r"^(?:type\s+)?([A-Za-z_$][A-Za-z0-9_$]*)(?:\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*))?$"
)


class ZeroReferenceConfigurationError(ValueError):
    """Allowlist configuration error captured by check_zero_reference_exports."""


@dataclass(frozen=True)
class _Definition:
    path: str
    symbol: str
    kind: str
    lineno: int


# The mission's own governance pins quote symbol names as prose; counting them
# as references would make every pinned symbol self-justifying.
_NON_REFERENCE_FILES = frozenset(
    {
        ALLOWLIST_RELATIVE_PATH,
        "config/architecture/nested-pool-checkout-allowlist.json",
    }
)


def _iter_reference_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() not in _REFERENCE_EXTENSIONS:
            continue
        if relative.as_posix() in _NON_REFERENCE_FILES:
            continue
        yield path


def count_identifier_references(root: Path) -> Counter[str]:
    """Repo-wide textual identifier occurrences (strings/comments included)."""
    counts: Counter[str] = Counter()
    for path in _iter_reference_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        counts.update(_IDENTIFIER.findall(text))
    return counts


def _backend_definitions(path: Path, relative: str) -> list[_Definition]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (SyntaxError, UnicodeDecodeError):
        return []
    definitions: list[_Definition] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_") or any(
                registers_definition(decorator) for decorator in node.decorator_list
            ):
                continue
            definitions.append(_Definition(relative, node.name, "symbol", node.lineno))
        elif isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if not name.startswith("_"):
                definitions.append(_Definition(relative, name, "assignment", node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                definitions.append(_Definition(relative, node.target.id, "assignment", node.lineno))
    return definitions


def _frontend_definitions(path: Path, relative: str) -> list[_Definition]:
    text = path.read_text(encoding="utf-8")
    definitions: list[_Definition] = []
    for match in _EXPORT_NAMED.finditer(text):
        lineno = text.count("\n", 0, match.start(1)) + 1
        definitions.append(_Definition(relative, match.group(1), "export", lineno))
    for match in _EXPORT_BRACE.finditer(text):
        for member in match.group(1).split(","):
            member_match = _EXPORT_BRACE_MEMBER.match(member.strip())
            if not member_match:
                continue
            exported = member_match.group(2) or member_match.group(1)
            lineno = text.count("\n", 0, match.start()) + 1
            definitions.append(_Definition(relative, exported, "export", lineno))
    return definitions


def collect_definitions(root: Path) -> list[_Definition]:
    """All in-scope backend/frontend definitions."""
    definitions: list[_Definition] = []
    for backend_root in _BACKEND_ROOTS:
        base = root / backend_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            if path.name == "__init__.py":
                continue
            definitions.extend(_backend_definitions(path, relative))
    for frontend_root in _FRONTEND_ROOTS:
        base = root / frontend_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() not in _FRONTEND_EXTENSIONS or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith(_FRONTEND_EXCLUDED_PREFIXES):
                continue
            definitions.extend(_frontend_definitions(path, relative))
    return definitions


def load_zero_reference_allowlist(path: Path) -> dict[tuple[str, str], str]:
    """Load the pinned known-dead symbols: (path, symbol) -> reason."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ZeroReferenceConfigurationError(f"Malformed JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "allowlist"}:
        raise ZeroReferenceConfigurationError(
            "Allowlist root must be a mapping with exactly 'version' and 'allowlist'"
        )
    if raw["version"] != 1:
        raise ZeroReferenceConfigurationError(f"Unsupported allowlist version: {raw['version']!r}")
    entries = raw["allowlist"]
    if not isinstance(entries, list):
        raise ZeroReferenceConfigurationError("allowlist must be a list")
    normalized: dict[tuple[str, str], str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "symbol", "reason"}:
            raise ZeroReferenceConfigurationError(
                f"allowlist entries need exactly path/symbol/reason: {entry!r}"
            )
        key = (str(PurePosixPath(entry["path"])), str(entry["symbol"]))
        if not str(entry["reason"]).strip():
            raise ZeroReferenceConfigurationError(f"allowlist reason must be non-empty: {key!r}")
        normalized[key] = entry["reason"]
    return normalized


def check_zero_reference_exports(root: Path) -> list[str]:
    """Flag definitions with zero repository-wide references (#732).

    Inactive when the allowlist config is absent: zero-reference detection is
    a whole-repo liveness audit, and partial fixture trees would flag every
    fixture symbol. The real repo's config is pinned by
    tests/scripts/test_zero_reference_exports.py::test_real_repo_pins_allowlist_config
    so deleting the file cannot silently disable the check.
    """
    config_path = root / ALLOWLIST_RELATIVE_PATH
    if not config_path.is_file():
        return []
    try:
        allowlist = load_zero_reference_allowlist(config_path)
    except ZeroReferenceConfigurationError as exc:
        return [f"zero-reference exports configuration: {exc}"]

    references = count_identifier_references(root)
    definitions = collect_definitions(root)
    declaration_counts = Counter(
        definition.symbol for definition in definitions if definition.kind == "export"
    )
    hits: list[_Definition] = []
    for definition in definitions:
        declaration_count = max(1, declaration_counts.get(definition.symbol, 0))
        if references.get(definition.symbol, 0) <= declaration_count:
            hits.append(definition)

    hit_keys = {(hit.path, hit.symbol) for hit in hits}
    errors: list[str] = []
    for hit in hits:
        if (hit.path, hit.symbol) in allowlist:
            continue
        errors.append(
            f"{hit.path}:{hit.lineno}: zero-reference export {hit.symbol!r} — no "
            "repository-wide reference beyond the declaration; delete the dead "
            "implementation, or pin a reasoned allowlist entry in "
            f"{ALLOWLIST_RELATIVE_PATH} if it is consumed out of band (#732)"
        )
    for path, symbol in sorted(set(allowlist) - hit_keys):
        errors.append(
            f"{path}: stale zero-reference allowlist entry for {symbol!r}; "
            "the symbol is referenced (or gone) — remove the entry (allowlist only shrinks)"
        )
    return sorted(errors)
