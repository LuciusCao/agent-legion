"""Pure section pruner for the publish-time workspace override prune (#428).

Split from ``node_config_prune`` for the file-size budget: the plan (before
the revision transaction) and the in-transaction apply (under the section
write's row lock) both call ``prune_workflow_override_section`` on their own
view of the workflow's override section, so the verdict never depends on
WHEN the overrides were read — the apply recomputes from the locked row and
cannot write a stale pre-lock snapshot back (codex 终轮 P1-1).

Node-level verdicts (终轮 P1-1 补):
- schema present → per-value prune (``_prune_stale_override_values``);
- no schema but the node stays in the new revision → the whole override
  goes: an empty schema with a non-empty override is exactly the state
  ``resolve_node_config`` rejects at intake, and the new revision (e.g. a
  published Agent that dropped its config_schema) declared the node has no
  configurable surface anymore;
- node dropped from the revision → the override stays: resolve skips it and
  the settings PATCH rejects it, and a later revision re-adding the node
  still finds its tuning (the same keep-hands-off stance as before).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.config_schema import ConfigSchemaError, validate_config_values
from server.app.services.node_secrets import is_secret_ref_marker


def prune_workflow_override_section(
    overrides: Mapping[str, Mapping[str, Any]],
    schemas: Mapping[str, Mapping[str, Any]],
    node_keys: frozenset[str],
) -> dict[str, dict[str, Any]]:
    """The workflow's override section pruned for the newly published revision.

    ``overrides`` is one workflow's node→values mapping (either the plan's
    pre-transaction read or the apply's locked read); ``schemas`` maps the
    node keys the new revision gives a config surface; ``node_keys`` is the
    new revision's full node set. The input is never mutated — the caller
    writes the result back as the workflow's whole section.
    """
    pruned: dict[str, dict[str, Any]] = {}
    for node_key, values in overrides.items():
        schema = schemas.get(node_key)
        if schema is None:
            # No config surface in the new revision: drop the override only
            # when the node itself survives (empty schema + non-empty
            # override is an intake error); a deleted node's override is
            # dead but harmless, so it stays.
            if node_key in node_keys:
                continue
            pruned[node_key] = dict(values)
            continue
        cleaned = _prune_stale_override_values(values, schema)
        if cleaned:
            pruned[node_key] = cleaned
    return pruned


def _prune_stale_override_values(
    values: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy of one node's override without entries the schema rejects.

    Keys absent from the declared properties go (rename/removal); values
    must pass the full partial validation; fields still marked ``secret``
    in the published schema keep ``{"secret_ref": ...}`` vault markers only
    — the settings PATCH stores markers there (``apply_node_secret_fields``),
    so anything else under a secret field is stale plaintext from before the
    field flipped secret and gets deleted rather than migrated into the
    vault (codex 终轮 P1-1). A marker under a field that is NO LONGER secret
    is an ordinary value: the new schema sends it through the generic
    validation, which the dict shape fails, so it is pruned here (终轮 P1-3).
    The platform-reserved execution keys are part of the node's effective
    schema (merged upstream), so overrides of them are judged by the same
    rules as any declared key.
    """
    properties = schema.get("properties") or {}
    return {
        key: value
        for key, value in values.items()
        if key in properties and _value_survives(properties[key], key, value)
    }


def _value_survives(
    prop: Mapping[str, Any],
    key: str,
    value: Any,
) -> bool:
    """Does one override value pass the property's full constraint set?

    The intake chain (``resolve_node_config``) validates overrides with
    ``validate_config_values(partial=True)``; the prune must prune by the
    same bar — a value surviving only the type check (enum tightened from
    ``[v1, v2]`` to ``[v2]`` while the override still says ``v1``) would
    block every new job at intake (codex 终轮 P1-2). The vault marker
    exemption lives one level up, bound to the property still being secret
    (a marker under a plain field is judged here as an ordinary value).
    """
    if prop.get("secret"):
        return is_secret_ref_marker(value)
    try:
        validate_config_values({"properties": {key: dict(prop)}}, {key: value}, partial=True)
    except ConfigSchemaError:
        return False
    return True
