"""Publish-time prune of workspace node overrides (codex 终轮 P1).

Split from ``node_config`` for the file-size budget (the exemption there
already named the prune as the split seam). The prune strips override
entries the newly published revision no longer accepts — unknown keys, and
values failing the *full* partial validation (type + enum + minimum/
maximum, not just a type mirror), so a tightened constraint cannot strand a
value that every later intake would reject (P1-2).

Secret semantics (P1-1): a field that flips to ``secret: true`` in the new
revision keeps only genuine vault markers (``{"secret_ref": ...}``) written
by the settings PATCH chain (``apply_node_secret_fields``). A leftover
plaintext value — possible when the pre-secret revision stored one — is
deleted, never migrated: pushing existing plaintext through the vault would
bless data that never passed the vault's write path, and the settings API
would mask it as 「已设置」 while the raw string sat in the workspace row
and job snapshots. The user re-enters the secret through the vault-backed
channel.

Transaction boundary (P1-3): the prune is PLANNED as a pure read before the
revision transaction and APPLIED inside it (``override_prune_commit_hook``
+ the ``on_commit`` hook of ``create_workflow_revision``), so a prune
failure rolls the whole publish back instead of stranding an active
revision whose stale overrides keep blocking intake.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, cast

from server.app.config_schema import ConfigSchemaError, validate_config_values
from server.app.services.node_config import workflow_node_config_schemas
from server.app.services.node_secrets import is_secret_ref_marker, secret_config_fields

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
    ``create_workflow_revision(on_commit=...)``: None when the workspace is
    already clean (no workspace write is warranted), otherwise a hook that
    rewrites the workflow's override section on the caller's connection.
    """
    section = plan_workspace_node_override_prune(
        job_db, workspace_id, definition, agent_definitions
    )
    if section is None:
        return None
    workflow_key = definition.key

    def _apply(conn: _RevisionConnection) -> None:
        # cast(Any): the concrete connection type must not be imported
        # (BOUNDARY-DATA-001 counts even TYPE_CHECKING imports); the
        # revision transaction only ever hands over the facade's connection.
        job_db.write_workspace_node_config_section(
            cast(Any, conn), workspace_id, workflow_key, section
        )

    return _apply


def plan_workspace_node_override_prune(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
) -> dict[str, dict[str, Any]] | None:
    """The workflow's post-prune override section, or None when already clean.

    Pure computation on a read (no writes): the publish pipeline runs this
    BEFORE creating the revision and applies the result inside the revision's
    own transaction, so a prune failure rolls the whole publish back instead
    of stranding an active revision whose stale overrides still block intake
    (codex 终轮 P1-3). Overrides of nodes the new revision dropped are left
    alone — resolve skips them, and the settings PATCH already rejects them.
    """
    workspace = job_db.get_workspace(workspace_id)
    overrides = _workflow_overrides(workspace, definition.key)
    if workspace is None or not overrides:
        return None
    schemas = workflow_node_config_schemas(definition, agent_definitions)
    pruned = False
    for node_key in list(overrides):
        values = overrides[node_key]
        if node_key not in schemas:
            continue  # unknown node: PATCH rejects it, but keep hands off here
        cleaned = _prune_stale_override_values(values, schemas[node_key])
        if cleaned == values:
            continue
        pruned = True
        if cleaned:
            overrides[node_key] = cleaned
        else:
            overrides.pop(node_key, None)
    return overrides if pruned else None


def _workflow_overrides(
    workspace: Mapping[str, Any] | None,
    workflow_key: str,
) -> dict[str, dict[str, Any]]:
    """The workspace's override mapping for one workflow, in mutated copy form.

    The caller mutates this mapping in place (prune keys / replace values)
    and writes it back as the workflow's whole override section, so every
    level is copied away from the cached record.
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


def _value_survives(
    prop: Mapping[str, Any],
    key: str,
    value: Any,
) -> bool:
    """Does one override value pass the property's full constraint set?

    The intake chain (``resolve_node_config``) validates overrides with
    ``validate_config_values(partial=True)``; the prune must prune by the same
    bar — a value surviving only the type check (enum tightened from
    ``[v1, v2]`` to ``[v2]`` while the override still says ``v1``) would
    block every new job at intake (codex 终轮 P1-2). Vault
    ``{"secret_ref": ...}`` markers never reach that validation (secret
    fields are stripped first), so they are checked by shape here; a
    plaintext value under a newly-secret field is deleted rather than
    migrated (P1-1).
    """
    if is_secret_ref_marker(value):
        return True
    try:
        validate_config_values({"properties": {key: dict(prop)}}, {key: value}, partial=True)
    except ConfigSchemaError:
        return False
    return True


def _prune_stale_override_values(
    values: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy of one node's override without entries the schema rejects.

    Keys absent from the declared properties go (rename/removal); values must
    pass the full partial validation; secret fields keep only
    ``{"secret_ref": ...}`` vault markers — the settings PATCH stores markers
    there (``apply_node_secret_fields``), so anything else under a secret
    field is stale plaintext from before the field flipped secret and gets
    deleted rather than migrated into the vault (codex 终轮 P1-1). The
    platform-reserved execution keys are part of the node's effective schema
    (merged upstream), so overrides of them are judged by the same rules as
    any declared key.
    """
    properties = schema.get("properties") or {}
    secret_fields = secret_config_fields(dict(properties=properties))
    return {
        key: value
        for key, value in values.items()
        if key in properties
        and (key not in secret_fields or is_secret_ref_marker(value))
        and _value_survives(properties[key], key, value)
    }
