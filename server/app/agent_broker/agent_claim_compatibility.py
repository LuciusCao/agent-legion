"""Resolve latest revision execution settings and Worker compatibility at claim."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from server.app.workflows.pi_protocol import render_command_spec


def worker_declarations(row: Mapping[str, Any]) -> tuple[set[str], set[tuple[str, str, str]]]:
    capabilities = set(json.loads(row["capabilities_json"] or "[]"))
    models = {
        (str(item.get("runtime") or "*"), str(item["provider"]), str(item["model"]))
        for item in json.loads(row["models_json"] or "[]")
    }
    return capabilities, models


def live_claim_manifest(row: Mapping[str, Any]) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(str(row["manifest_json"]))
    node_execution: dict[str, Any] = {}
    raw_revision = row.get("revision_definition_json")
    if raw_revision:
        definition = json.loads(str(raw_revision))
        node = (definition.get("nodes") or {}).get(str(row["node_key"])) or {}
        node_execution = node.get("execution") or {}
    frozen = manifest.get("execution") or {}
    # Legacy key, absent on manifests enqueued after schema v63 (workspace
    # Agent defaults retired): kept so in-flight queued manifests still
    # resolve exactly as enqueued.
    defaults = manifest.get("execution_defaults") or {}
    # Revisions are immutable: "live" means a job upgraded to a new revision
    # pin gets that revision's node execution at claim time. The revision's
    # node execution already carries the workflow top-level defaults merged
    # by the loader, so it is the effective value. Resolution chain per key:
    # current node execution -> enqueue-time workspace defaults (legacy
    # manifests only) -> the fully resolved execution frozen at enqueue.
    # Removing a node override therefore falls back to the workflow top-level
    # default (merged into the revision) or, when neither exists, to the
    # frozen enqueue-time value.
    manifest["execution"] = {
        **frozen,
        **{
            key: node_execution.get(key) or defaults.get(key) or frozen.get(key) or ""
            for key in ("provider", "model", "thinking")
        },
    }
    manifest["additional_prompt"] = str(node_execution.get("prompt") or "")
    if all(key in manifest for key in ("tools", "inputs", "expected_outputs")):
        manifest["command_spec"] = render_command_spec(manifest)
    return manifest


def worker_can_run(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    worker_capabilities: set[str],
    worker_models: set[tuple[str, str, str]],
) -> bool:
    capability = str(candidate["capability"])
    execution = manifest.get("execution") or {}
    model = (
        str(candidate.get("runtime") or ""),
        str(execution.get("provider") or ""),
        str(execution.get("model") or ""),
    )
    capability_matches = capability in worker_capabilities or "*" in worker_capabilities
    model_matches = (
        model in worker_models
        or ("*", model[1], model[2]) in worker_models
        or ("*", "*", "*") in worker_models
    )
    return capability_matches and model_matches
