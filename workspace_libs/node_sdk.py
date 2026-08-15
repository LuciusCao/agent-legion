"""Node SDK: the framework-level runtime API for workflow code nodes.

Design: ``docs/architecture/node-sdk-and-worker-execution-design.md``.

A code node still exposes the module-level ``run(job, job_dir, runtime)``
entry contract (EXEC-CODE-001/002); the SDK is the adaptation layer nodes use
*inside* ``run`` so they never hand-roll the scaffolding: JSON artifact IO,
config merging, cancellation checkpoints, prefetched inputs, and the
auth-failure back-channel.

Layering rule: this module depends on the standard library only. It must never
import ``server.app.*`` — the SDK is execution-plane code that also runs
inside the velites sandbox (EXEC-CODE-003) and, eventually, on remote Workers,
so it cannot drag control-plane dependencies along.

Compatibility promise: custom node code versions are frozen at intake while
the SDK evolves with the repo, so this API only grows — never remove or rename
members; breaking changes land under new names.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Auth-failure back-channel (design §5.3): the node records the fact, the
# parent executor performs the privileged token invalidation. The marker lives
# in a subdirectory so top-level file inventories in node code (an
# ``iterdir()`` listing) never pick it up.
NODE_RUNTIME_DIR = ".node_runtime"
AUTH_FAILURE_MARKER = "auth_failure"
AUTH_FAILURE_MARKER_PATH = Path(NODE_RUNTIME_DIR) / AUTH_FAILURE_MARKER

# Keys in node_config that reference (or carry) the injected connection; they
# are selectors, not business overrides, and never merge into service config.
_CONNECTION_SELECTOR_KEYS = ("connection", "connection_config")


def parse_json_object(value: Any) -> dict[str, Any]:
    """Tolerantly parse *value* into a dict; anything else yields ``{}``."""
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


class _ArtifactStore:
    """Uniform access to the files a node reads and writes under ``job_dir``."""

    def __init__(self, context: NodeContext) -> None:
        self._context = context

    @property
    def dir(self) -> Path:
        return self._context.job_dir

    def path(self, name: str) -> Path:
        return self._context.job_dir / name

    def read_text(self, name: str) -> str:
        return self.path(name).read_text(encoding="utf-8")

    def read_json(self, name: str) -> Any:
        return json.loads(self.read_text(name))

    def read_json_object(self, name: str) -> dict[str, Any]:
        """Read *name* and require a JSON object (dict) payload."""
        path = self.path(name)
        if not path.is_file():
            raise ValueError(f"Missing input: {name}")
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise ValueError(f"Invalid content in {name}")
        return content

    def write_text(self, name: str, text: str) -> Path:
        # Writing is the natural stage-commit boundary: checkpoint here so
        # cancelled executions stop before producing partial output batches.
        self._context.checkpoint()
        self._context.job_dir.mkdir(parents=True, exist_ok=True)
        path = self.path(name)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, name: str, payload: Any) -> Path:
        return self.write_text(name, json.dumps(payload, ensure_ascii=False, indent=2))


class NodeContext:
    """Framework handle passed through the node: inputs, config, artifacts."""

    def __init__(
        self,
        job: Mapping[str, Any],
        job_dir: Path,
        runtime: Mapping[str, Any] | None = None,
    ) -> None:
        self._job = job
        self._job_dir = Path(job_dir)
        self._runtime = runtime or {}
        self.artifacts = _ArtifactStore(self)

    @property
    def job(self) -> Mapping[str, Any]:
        return self._job

    @property
    def job_dir(self) -> Path:
        return self._job_dir

    @property
    def logger(self) -> logging.Logger:
        node_key = self._runtime.get("node_key")
        return logging.getLogger(f"workflow_node.{node_key}" if node_key else __name__)

    @property
    def config(self) -> Mapping[str, Any]:
        """The dispatch-resolved node config (schema defaults → node/workspace
        overrides → vault resolution → connection injection)."""
        node_config = self._runtime.get("node_config")
        return node_config if isinstance(node_config, Mapping) else {}

    def service_config(
        self,
        section: str | None = None,
        legacy_keys: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Effective config for an external service the node talks to.

        Merge order (later wins): optional ``settings_config[section]`` base
        (machine/env-injected values), the dispatch-injected
        ``connection_config`` block (resolved endpoint config + plaintext
        token, in memory only), then node/workspace business overrides.
        Empty values (``None``/``""``) never override; *legacy_keys* are
        retired pre-connection credential keys that only apply when no
        connection was injected (legacy frozen payloads).
        """
        merged: dict[str, Any] = {}
        if section is not None:
            settings_config = self._runtime.get("settings_config")
            if isinstance(settings_config, Mapping):
                base = settings_config.get(section)
                if isinstance(base, Mapping):
                    merged.update(base)
        node_config = self.config
        injected = node_config.get("connection_config")
        has_connection = isinstance(injected, Mapping) and bool(injected)
        if isinstance(injected, Mapping) and injected:
            merged.update(
                {key: value for key, value in injected.items() if value not in (None, "")}
            )
        for key, value in node_config.items():
            if key in _CONNECTION_SELECTOR_KEYS or value in (None, ""):
                continue
            if has_connection and key in legacy_keys:
                continue
            merged[key] = value
        return merged

    def checkpoint(self) -> None:
        """Raise when cancellation was requested (cooperative cancellation).

        Duck-typed against the runtime's cancellation token so both the
        builtin child's multiprocessing token and the sandboxed child's
        threading token work; no token means no-op.
        """
        token = self._runtime.get("cancellation")
        raise_if_cancelled = getattr(token, "raise_if_cancelled", None)
        if callable(raise_if_cancelled):
            raise_if_cancelled()

    @property
    def batch(self) -> dict[str, Any] | None:
        """The prefetched batch row (replaces the retired ``job_db`` read)."""
        batch = self._runtime.get("job_batch")
        return dict(batch) if isinstance(batch, Mapping) else None

    @property
    def skill_versions(self) -> dict[str, str]:
        """Prefetched ``node_key -> skill_version`` for this job's runs."""
        versions = self._runtime.get("skill_versions")
        if not isinstance(versions, Mapping):
            return {}
        return {str(key): str(value) for key, value in versions.items()}

    def workflow_manifest(self, default_key: str = "") -> dict[str, Any]:
        """Workflow identity block of the job (key/version/revision/hash)."""
        return {
            "key": self._job.get("workflow_key", default_key),
            "version": self._job.get("workflow_version"),
            "revision_id": self._job.get("workflow_revision_id", ""),
            "definition_hash": self._job.get("workflow_definition_hash", ""),
        }

    def report_auth_failure(self) -> None:
        """Record that the upstream rejected the injected connection token.

        The node only records the fact (a marker under ``job_dir``); the
        parent executor performs the privileged cache invalidation after the
        child exits — nodes hold no database handle (design §5.3).
        """
        marker = self._job_dir / AUTH_FAILURE_MARKER_PATH
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(self.config.get("connection") or "").strip(), encoding="utf-8")
