"""Shared schema, validation and helpers for workflow seed packages.

A *seed package* (``seed.json``) is a portable snapshot of workflow
definitions: DAG definitions, Agent definitions, custom node code texts and
skill source pins. ``scripts.seed.export_seed`` produces one from a live
instance database (read-only); ``scripts.seed.import_seed`` applies one to a
target instance over the HTTP API (idempotently).

The tooling is platform-generic: it carries no workflow keys, workspace
names, capabilities or other deployment-specific constants. Those live in
the seed file itself, so any organization can move its own workflow
definitions between instances (e.g. prod -> develop) with the same tool.
See ``scripts/seed/README.md``.

Seed schema (version 1), top-level keys:

- ``schema_version`` (int, required)
- ``exported_at`` / ``source`` (provenance, informational)
- ``workflows`` (list, required): ``{key, label, description, origin?, definition}``
- ``agents`` (list): ``{agent_id, capability, definition, ...provenance}``;
  every capability must be referenced by at least one exported workflow
- ``node_codes`` (list): ``{workflow_key, node_key, capability, code,
  code_sha256, change_note?, ...provenance}``
- ``skills`` (mapping, optional): ``{sources: {key: {repo, ref}}, lock: {...}}``

A legacy ``executors`` top-level key is tolerated and ignored: the executor
concept was retired in schema v47 (P-0.5), so current seeds simply omit it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SEED_SCHEMA_VERSION = 1

# Custom node code single-file limit (mirrors
# server/app/services/node_codes.py MAX_CODE_BYTES, EXEC-CODE-002).
MAX_CODE_BYTES = 64 * 1024

# Import prefixes that custom node code must never use: platform internals
# are not importable inside the sandbox (EXEC-CODE-003). Callers may extend
# this list (e.g. a private package forbidding its own retired libraries).
DEFAULT_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = ("server.app",)

# The only Agent definition fields the platform API accepts and stores
# (server/app/agent_catalog.py AgentDefinition). Export filters to these.
AGENT_DEFINITION_FIELDS: tuple[str, ...] = (
    "capability",
    "runtime",
    "skill",
    "tools",
    "requires_labels",
    "config_schema",
)

# Heuristic guard against leaking credentials into a seed package: any
# mapping key matching this pattern with a non-empty string value is
# reported. Secrets must travel via vault references, never as literals.
SECRET_KEY_PATTERN = re.compile(r"token|password|secret|api[_-]?key|credential", re.IGNORECASE)


def canonical_json(obj: Any) -> str:
    """Canonical form for content comparison: sorted keys, compact separators.

    List order stays significant.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_equal(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def workflow_capabilities(definition: dict[str, Any]) -> set[str]:
    """All node capabilities declared by a workflow definition (dict form)."""
    return {
        str(node.get("capability"))
        for node in (definition.get("nodes") or {}).values()
        if isinstance(node, dict) and node.get("capability")
    }


def node_key_for_capability(definition: dict[str, Any], capability: str) -> str | None:
    """Find the node key declaring ``capability`` in a workflow definition."""
    for node_key, node in (definition.get("nodes") or {}).items():
        if isinstance(node, dict) and node.get("capability") == capability:
            return str(node_key)
    return None


def filter_agent_definition(definition: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields the Agent catalog API accepts/stores."""
    return {key: definition[key] for key in AGENT_DEFINITION_FIELDS if key in definition}


def code_violations(
    code: str,
    forbidden_prefixes: tuple[str, ...] = DEFAULT_FORBIDDEN_IMPORT_PREFIXES,
) -> list[str]:
    """Per-file checks for one node code text: size, syntax, module-level
    ``run``, forbidden imports (same contract as the platform's
    ``validate_node_code`` plus the sandbox import policy)."""
    problems: list[str] = []
    size = len(code.encode("utf-8"))
    if size > MAX_CODE_BYTES:
        problems.append(f"exceeds the {MAX_CODE_BYTES}-byte size limit ({size} bytes)")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return problems + [f"not valid Python: {exc}"]
    has_run = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run"
        for node in tree.body
    )
    if not has_run:
        problems.append("missing module-level 'run' function")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        for module in modules:
            for prefix in forbidden_prefixes:
                if module == prefix or module.startswith(prefix + "."):
                    problems.append(f"forbidden import: {module}")
    return problems


def secret_like_fields(obj: Any, path: str = "$") -> list[str]:
    """Paths of mapping entries whose key looks secret-bearing and whose
    value is a non-empty string (heuristic leak guard for seed packages)."""
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_path = f"{path}.{key}"
            if SECRET_KEY_PATTERN.search(str(key)) and isinstance(value, str) and value:
                hits.append(key_path)
            hits.extend(secret_like_fields(value, key_path))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            hits.extend(secret_like_fields(value, f"{path}[{index}]"))
    return hits


def validate_seed(
    seed: dict[str, Any],
    forbidden_prefixes: tuple[str, ...] = DEFAULT_FORBIDDEN_IMPORT_PREFIXES,
) -> list[str]:
    """Structural validation of a seed package; returns problems ([] = ok)."""
    problems: list[str] = []
    if seed.get("schema_version") != SEED_SCHEMA_VERSION:
        problems.append(f"schema_version must be {SEED_SCHEMA_VERSION}")

    workflows = seed.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        problems.append("workflows must be a non-empty list")
        workflows = []
    workflow_by_key: dict[str, dict[str, Any]] = {}
    for entry in workflows:
        if not isinstance(entry, dict) or not entry.get("key"):
            problems.append("workflow entry must be a mapping with a key")
            continue
        key = str(entry["key"])
        workflow_by_key[key] = entry
        definition = entry.get("definition")
        if not isinstance(definition, dict) or not definition.get("nodes"):
            problems.append(f"workflow {key!r} has no definition.nodes")
    referenced_capabilities: set[str] = set()
    for entry in workflow_by_key.values():
        definition = entry.get("definition")
        if isinstance(definition, dict):
            referenced_capabilities |= workflow_capabilities(definition)

    agents = seed.get("agents", [])
    if not isinstance(agents, list):
        problems.append("agents must be a list")
        agents = []
    for agent in agents:
        if not isinstance(agent, dict) or not agent.get("agent_id"):
            problems.append("agent entry must be a mapping with agent_id")
            continue
        definition = agent.get("definition")
        capability = str((definition or {}).get("capability") or "")
        if not capability:
            problems.append(f"agent {agent.get('agent_id')}: definition has no capability")
        elif capability not in referenced_capabilities:
            problems.append(
                f"agent {agent.get('agent_id')}: capability {capability!r} "
                "is not referenced by any exported workflow"
            )

    node_codes = seed.get("node_codes", [])
    if not isinstance(node_codes, list):
        problems.append("node_codes must be a list")
        node_codes = []
    seen_targets: set[tuple[str, str]] = set()
    for entry in node_codes:
        if not isinstance(entry, dict):
            problems.append("node_codes entry must be a mapping")
            continue
        workflow_key = str(entry.get("workflow_key"))
        node_key = str(entry.get("node_key"))
        capability = str(entry.get("capability"))
        code = str(entry.get("code") or "")
        label = f"{workflow_key}/{node_key}"
        if (workflow_key, node_key) in seen_targets:
            problems.append(f"{label}: duplicate node_codes entry")
        seen_targets.add((workflow_key, node_key))
        workflow = workflow_by_key.get(workflow_key)
        if workflow is None:
            problems.append(f"{label}: workflow not present in seed")
        elif isinstance(workflow.get("definition"), dict):
            expected = node_key_for_capability(workflow["definition"], capability)
            if expected != node_key:
                problems.append(f"{label}: capability {capability!r} belongs to node {expected!r}")
        if entry.get("code_sha256") != sha256_text(code):
            problems.append(f"{label}: code_sha256 mismatch")
        for problem in code_violations(code, forbidden_prefixes):
            problems.append(f"{label}: {problem}")

    skills = seed.get("skills")
    if skills is not None:
        if not isinstance(skills, dict):
            problems.append("skills must be a mapping")
        else:
            sources = skills.get("sources") or {}
            lock = (skills.get("lock") or {}).get("skills") or {}
            if sorted(sources) != sorted(lock):
                problems.append("skills.sources keys differ from skills.lock keys")
            for key, locked in lock.items():
                commit = str((locked or {}).get("commit") or "")
                if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
                    problems.append(f"skill {key!r}: lock commit is not a 40-hex sha")
            for key, source in sources.items():
                if not str((source or {}).get("repo") or "") or not str(
                    (source or {}).get("ref") or ""
                ):
                    problems.append(f"skill {key!r}: source missing repo/ref")

    for hit in secret_like_fields(seed):
        problems.append(
            f"{hit}: looks like secret material; seed packages must carry "
            "vault references only, never literal credentials"
        )
    return problems


def load_seed(
    path: Path,
    forbidden_prefixes: tuple[str, ...] = DEFAULT_FORBIDDEN_IMPORT_PREFIXES,
) -> dict[str, Any]:
    seed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(seed, dict):
        raise SystemExit(f"{path}: seed.json must be a JSON object")
    problems = validate_seed(seed, forbidden_prefixes)
    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems)
        raise SystemExit(f"{path}: seed validation failed ({len(problems)} problems):\n{details}")
    return seed
