"""Resolve latest revision execution settings and Worker compatibility at claim."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from server.app.agent_broker.claim_timeout import decide_claim_timeout
from server.app.agent_runtime.catalog import get_adapter
from server.app.agent_runtime.execution import resolve_execution_chain, validate_execution_contract
from server.app.services.node_profile_pins import MANIFEST_KEY as NODE_PROFILE_PIN_MANIFEST_KEY
from server.app.workflows.pi_protocol import render_command_spec


def worker_model_declarations(row: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    """The Worker's registered model allowlist triples.

    Worker capabilities used to ride along here; claim admission no longer
    matches capabilities (issue #284), so only the model declarations are
    still relevant to claiming.
    """
    return {
        (str(item.get("runtime") or "*"), str(item["provider"]), str(item["model"]))
        for item in json.loads(row["models_json"] or "[]")
    }


def live_claim_manifest(row: Mapping[str, Any]) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(str(row["manifest_json"]))
    node_execution: dict[str, Any] = {}
    # #1079（D6）：质量回放副本的执行档案已移植进副本快照并在入队时冻结；
    # 副本 job 仍关联原 revision（只为谱系 / 存量分层），故不得按 revision
    # 重读 execution / prompt——否则回放会跑回原 revision 的模型与提示词。
    # 普通 job（无 pin）的 live 语义不变。
    replay_pinned = bool(manifest.get(NODE_PROFILE_PIN_MANIFEST_KEY))
    raw_revision = None if replay_pinned else row.get("revision_definition_json")
    if raw_revision:
        definition = json.loads(str(raw_revision))
        node = (definition.get("nodes") or {}).get(str(row["node_key"])) or {}
        node_execution = node.get("execution") or {}
        # The label feeds the auto-assembled default instructions at re-render
        # time; keep it in step with the revision (legacy manifests lack it
        # and fall back to the node key inside build_prompt).
        if label := str(node.get("label") or ""):
            manifest["node_label"] = label
    frozen = manifest.get("execution") or {}
    # Legacy key, absent on manifests enqueued after schema v64 (workspace
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
    # frozen enqueue-time value. The re-fetched key set is the runtime
    # adapter's execution contract (EXEC-RUNTIME-DISPATCH-001), not a
    # hardcoded list.
    runtime = str(manifest.get("runtime") or row.get("runtime") or "")
    contract_keys = tuple(get_adapter(runtime).execution.keys)
    # Resolve every contract-governed key through the chain so validation
    # also sees keys the runtime does NOT support: a revision upgrade that
    # configures one (or loses the last source of a required key) fails fast
    # here instead of silently shipping.
    resolved = resolve_execution_chain(node_execution, defaults, frozen)
    validate_execution_contract(node_key=str(row["node_key"]), runtime=runtime, values=resolved)
    # Only contract keys are re-fetched into the shipped execution block;
    # unsupported keys can only reach here empty (validation raised otherwise).
    manifest["execution"] = {**frozen, **{key: resolved[key] for key in contract_keys}}
    if not replay_pinned:
        manifest["additional_prompt"] = str(node_execution.get("prompt") or "")
        # #513：claim 侧重解析链与 dispatch 同源携带拼接模式。
        manifest["prompt_mode"] = str(node_execution.get("prompt_mode") or "")
    # #691: runtime-adjustable timeout re-resolved before the re-render.
    decide_claim_timeout(manifest, row, "agent")
    if all(key in manifest for key in ("tools", "inputs", "expected_outputs")):
        manifest["command_spec"] = render_command_spec(manifest)
    return manifest


def worker_can_run(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    worker_models: set[tuple[str, str, str]],
) -> bool:
    """Model allowlist check; capabilities no longer gate claims (issue #284)."""
    execution = manifest.get("execution") or {}
    model = (
        str(candidate.get("runtime") or ""),
        str(execution.get("provider") or ""),
        str(execution.get("model") or ""),
    )
    return (
        model in worker_models
        or ("*", model[1], model[2]) in worker_models
        or ("*", "*", "*") in worker_models
    )
