"""Publish-time prune of workspace node overrides (codex 终轮 P1).

Split from ``node_config`` for the file-size budget (the exemption there
already named the prune as the split seam). The prune strips override
entries the newly published revision no longer accepts — unknown keys, and
values failing the *full* partial validation (type + enum + minimum/
maximum, not just a type mirror), so a tightened constraint cannot strand a
value that every later intake would reject (P1-2). The section-wide
computation lives in ``node_config_prune_logic`` (shared by the plan and
the in-transaction apply); this module owns the read/plan/apply plumbing.

Secret semantics (P1-1): a field that flips to ``secret: true`` in the new
revision keeps only genuine vault markers (``{"secret_ref": ...}``) written
by the settings PATCH chain (``apply_node_secret_fields``). A leftover
plaintext value — possible when the pre-secret revision stored one — is
deleted, never migrated: pushing existing plaintext through the vault would
bless data that never passed the vault's write path, and the settings API
would mask it as 「已设置」 while the raw string sat in the workspace row
and job snapshots. The user re-enters the secret through the vault-backed
channel. The marker exemption is bound to the field still being secret in
the published schema (终轮 P1-3): once the field flips back to plain, the
marker is an ordinary value and faces the generic validation — otherwise
intake rejects the dict-under-string long after the prune had its chance.

Transaction boundary (P1-3): the prune is PLANNED as a pure read before the
revision transaction (a cheap 「does anything need pruning」 check) and
APPLIED inside it (``override_prune_commit_hook`` + the ``on_commit`` hook
of ``create_workflow_revision``), so a prune failure rolls the whole
publish back instead of stranding an active revision whose stale overrides
keep blocking intake. Inside the transaction the section is RE-computed
from a workspace re-read taken on the same connection under the same row
lock the section write takes (both via the JobQueries facade —
``read/write_workspace_node_config_section``): the plan's snapshot read can
race a concurrent settings PATCH, and writing that snapshot back under the
lock would clobber the PATCH's committed values — or let a PATCH built on
the pre-prune override resurrect a just-pruned key after the publish.
Recomputing under the lock means the PATCH commits either fully before the
prune (its fresh values are re-judged by the new schema) or fully after it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, cast

from server.app.services.node_config import workflow_node_config_schemas
from server.app.services.node_config_prune_logic import prune_workflow_override_section

if TYPE_CHECKING:
    from server.app.agent_catalog import AgentDefinition
    from server.app.jobs import JobQueries
    from server.app.workflows.definition import WorkflowDefinition


class _RevisionConnection(Protocol):
    """The revision transaction's connection, structurally.

    The concrete type stays unnamed (even under TYPE_CHECKING): importing it
    counts as a DB-primitive reference under BOUNDARY-DATA-001 — services
    stay facade-only. The signature matches the facade connection's
    ``execute``, so the cast in ``_apply`` is a formality.
    """

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> Any: ...


def override_prune_commit_hook(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
) -> Callable[[_RevisionConnection], None] | None:
    """Plan the override prune and wrap it as a revision-transaction hook.

    The publish pipeline passes the result to
    ``create_workflow_revision(on_commit=...)``: None when the plan read saw
    a workspace needing no prune (no workspace write is warranted),
    otherwise a hook that re-reads the workflow's override section on the
    caller's connection, RE-computes the prune there under the section
    write's row lock, and rewrites the section — the plan's snapshot is
    never written back, so a settings PATCH that committed between the plan
    and the lock keeps its values when they satisfy the new schema (and has
    its violations pruned like any other) instead of being flattened by a
    stale pre-lock snapshot (codex 终轮 P1-1).
    """
    if not plan_workspace_node_override_prune(job_db, workspace_id, definition, agent_definitions):
        return None
    workflow_key = definition.key
    schemas = workflow_node_config_schemas(definition, agent_definitions)
    node_keys = frozenset(definition.nodes)

    def _apply(conn: _RevisionConnection) -> None:
        # cast(Any): the concrete connection type must not be imported
        # (BOUNDARY-DATA-001 counts even TYPE_CHECKING imports); the
        # revision transaction only ever hands over the facade's connection.
        section = job_db.read_workspace_node_config_section(
            cast(Any, conn), workspace_id, workflow_key
        )
        pruned = prune_workflow_override_section(section, schemas, node_keys)
        job_db.write_workspace_node_config_section(
            cast(Any, conn), workspace_id, workflow_key, pruned
        )

    return _apply


def plan_workspace_node_override_prune(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
) -> bool:
    """Would the newly published revision prune anything in this workspace?

    Pure computation on a read (no writes): the publish pipeline runs this
    BEFORE creating the revision and applies the actual prune inside the
    revision's own transaction, so a prune failure rolls the whole publish
    back instead of stranding an active revision whose stale overrides still
    block intake (codex 终轮 P1-3). The plan runs the same pure section
    pruner as the in-transaction apply, so a False verdict only skips the
    hook while the section still matches the new schema — a racing write
    landing between plan and lock is judged under the lock regardless (the
    publish itself does not owe that race a prune).
    """
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        return False
    overrides = _workflow_overrides(workspace, definition.key)
    if not overrides:
        return False
    return (
        prune_workflow_override_section(
            overrides,
            workflow_node_config_schemas(definition, agent_definitions),
            frozenset(definition.nodes),
        )
        != overrides
    )


def _workflow_overrides(
    workspace: Mapping[str, Any] | None,
    workflow_key: str,
) -> dict[str, dict[str, Any]]:
    """The workspace's override mapping for one workflow, in copy form.

    The pruner returns a fresh mapping without touching its input; the
    result becomes the workflow's whole override section.
    """
    if not isinstance(workspace, Mapping):
        return {}
    node_config = workspace.get("node_config")
    if not isinstance(node_config, Mapping):
        return {}
    workflow_overrides = node_config.get(workflow_key)
    if not isinstance(workflow_overrides, Mapping):
        return {}
    return {
        str(node_key): dict(values)
        for node_key, values in workflow_overrides.items()
        if isinstance(values, Mapping)
    }
