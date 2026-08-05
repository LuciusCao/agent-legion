"""Resolve latest revision execution settings and Worker compatibility at claim."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from server.app.workflows.pi_protocol import render_command_spec


def worker_declarations(row: Mapping[str, Any]) -> tuple[set[str], set[tuple[str, str]]]:
    capabilities = set(json.loads(row["capabilities_json"] or "[]"))
    models = {
        (str(item["provider"]), str(item["model"]))
        for item in json.loads(row["models_json"] or "[]")
    }
    return capabilities, models


def live_claim_manifest(row: Mapping[str, Any]) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(str(row["manifest_json"]))
    execution: dict[str, Any] = {}
    raw_revision = row.get("revision_definition_json")
    if raw_revision:
        definition = json.loads(str(raw_revision))
        node = (definition.get("nodes") or {}).get(str(row["node_key"])) or {}
        execution = node.get("execution") or {}
    defaults = manifest.get("pi_defaults") or manifest.get("pi") or {}
    manifest["pi"] = {
        **manifest.get("pi", {}),
        **{
            key: execution.get(key) or defaults.get(key) or ""
            for key in ("provider", "model", "thinking")
        },
    }
    manifest["additional_prompt"] = str(execution.get("prompt") or "")
    if all(key in manifest for key in ("tools", "inputs", "expected_outputs")):
        manifest["command_spec"] = render_command_spec(manifest)
    return manifest


def worker_can_run(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    worker_capabilities: set[str],
    worker_models: set[tuple[str, str]],
) -> bool:
    capability = str(candidate["capability"])
    pi = manifest.get("pi") or {}
    model = (str(pi.get("provider") or ""), str(pi.get("model") or ""))
    capability_matches = capability in worker_capabilities or "*" in worker_capabilities
    model_matches = model in worker_models or ("*", "*") in worker_models
    return capability_matches and model_matches
