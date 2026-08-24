"""Node SDK: the framework-level runtime API for workflow code nodes.

Design: ``docs/architecture/node-sdk-and-worker-execution-design.md``.

A code node exposes a module-level ``run`` entry; the SDK is the adaptation
layer nodes use *inside* ``run`` so they never hand-roll the scaffolding:
JSON artifact IO, config merging, cancellation checkpoints, prefetched
inputs (including the materialized local file for material-type job
inputs, ``ctx.material``), and the auth-failure back-channel. The preferred
shape is a plain business function decorated with ``@entrypoint``::

    from workspace_libs.node_sdk import NodeContext, entrypoint

    @entrypoint
    def run(ctx: NodeContext) -> None:
        data = ctx.artifacts.read_json_object("input.json")
        ctx.artifacts.write_json("output.json", {"echo": data})

The classic ``run(job, job_dir, runtime)`` signature keeps working
(``NodeContext(job, job_dir, runtime)`` adapts it) — frozen code versions are
unaffected either way. Nodes that talk to an external HTTP service use
``workspace_libs.http_client``; media helpers live in
``workspace_libs.media``. The framework carries no business semantics:
service-specific URL rules, payload parsing, and quality policy stay in the
node.

Layering rule: this module depends on the standard library plus sibling
``workspace_libs`` modules only. It must never import ``server.app.*`` — the
SDK is execution-plane code that also runs inside the velites sandbox
(EXEC-CODE-003) and on remote Workers, so it cannot drag control-plane
dependencies along.

Compatibility promise: custom node code versions are frozen at intake while
the SDK evolves with the repo, so this API only grows — never remove or rename
members; breaking changes land under new names.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from workspace_libs._service_config import merge_service_config
from workspace_libs.node_artifacts import ArtifactStore

# Auth-failure back-channel (design §5.3): the node records the fact, the
# parent executor performs the privileged token invalidation. The marker lives
# in a subdirectory so top-level file inventories in node code (an
# ``iterdir()`` listing) never pick it up.
NODE_RUNTIME_DIR = ".node_runtime"
AUTH_FAILURE_MARKER = "auth_failure"
AUTH_FAILURE_MARKER_PATH = Path(NODE_RUNTIME_DIR) / AUTH_FAILURE_MARKER


def parse_json_object(value: Any) -> dict[str, Any]:
    """Tolerantly parse *value* into a dict; anything else yields ``{}``."""
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


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
        self.artifacts = ArtifactStore(self)

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
        return merge_service_config(self._runtime, self.config, section, legacy_keys)

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
        """The prefetched run row (replaces the retired ``job_db`` read)."""
        batch = self._runtime.get("job_batch")
        return dict(batch) if isinstance(batch, Mapping) else None

    @property
    def batch_payload(self) -> dict[str, Any]:
        """Parsed ``source_payload_json`` of the prefetched run row.

        The dispatch layer prefetches the run row and synthesizes the legacy
        payload from the authoritative run/job freeze columns (RUN-FREEZE-001;
        nodes hold no database handle, EXEC-CODE-004); runtimes without a
        prefetch yield ``{}``.
        """
        batch = self.batch
        if not batch:
            return {}
        return parse_json_object(batch.get("source_payload_json"))

    @property
    def material(self) -> dict[str, Any] | None:
        """The materialized local file for a material-type job input.

        Jobs whose input is a material item carry ``runtime["materials"]``:
        the dispatching parent (Host or Worker) has already downloaded the
        object into the content-addressed materials cache, so the block's
        ``path`` is a local read-only file the node opens directly (the
        cache root is statically allow-read in the sandbox,
        MATERIAL-ACCESS-001). Keys: ``material_id`` / ``path`` /
        ``filename`` / ``content_type`` / ``size_bytes`` / ``content_hash``.
        Non-material inputs yield ``None``.

        A ``material_bundle`` input (#156) adds ``kind: "bundle"`` and
        ``entries`` (per-member ``path`` / ``size_bytes`` / ``content_hash``);
        ``path`` then points at the materialized **directory** whose
        relative layout matches the uploaded folder.
        """
        block = self._runtime.get("materials")
        return dict(block) if isinstance(block, Mapping) else None

    @property
    def root_dir(self) -> Path | None:
        """Repository/worktree root of the executing host, when provided.

        Injected by the parent executor as the runtime ``root_dir`` key; node
        code uses it to resolve machine-relative asset paths instead of
        ``__file__`` (which is meaningless for DB-loaded code text).
        """
        root = self._runtime.get("root_dir")
        return Path(str(root)) if root else None

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


def entrypoint(
    fn: Callable[[NodeContext], None],
) -> Callable[[Mapping[str, Any], Path, Mapping[str, Any] | None], None]:
    """Adapt a ``def run(ctx: NodeContext)`` business function to the executor
    entry contract ``run(job, job_dir, runtime)``.

    Usage::

        @entrypoint
        def run(ctx: NodeContext) -> None:
            ...

    Pure adaptation — no implicit checkpoints or error mapping; the business
    function's behavior is exactly what it writes. ``functools.wraps`` keeps
    the module-level name ``run`` so the loader and the draft validator find
    it unchanged.
    """

    @functools.wraps(fn)
    def run(
        job: Mapping[str, Any], job_dir: Path, runtime: Mapping[str, Any] | None = None
    ) -> None:
        fn(NodeContext(job, Path(job_dir), runtime))

    return run
