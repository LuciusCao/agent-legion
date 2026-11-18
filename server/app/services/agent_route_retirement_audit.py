"""Fail-closed historical audit for retiring materialized Agent routes (#936).

The audit covers every readable job snapshot, every still-legacy active
revision, every ``runs.frozen_pins_json.agent_versions`` entry and every queued
Agent-definition request. It asks whether removing the route row would preserve
both execution identity and failure behavior; it never infers that a real
instance is clean from properties of the current schema.

Only identifiers, hashes, lifecycle states and stable reason codes enter the
report. Snapshot/manifest/config/input bodies and parser messages do not. The
report is deterministic local-only operational material, not an API response.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from server.app.agent_catalog import AgentDefinition
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.workflow_node_profile import (
    FROZEN_NODE_LEGACY_AGENT,
    frozen_node_execution_kind,
)

if TYPE_CHECKING:
    from server.app.jobs.queries.agent_backfill_reader import AgentBackfillReader

REPORT_KIND = "agent-route-retirement-audit"
SAFE_CATEGORIES = frozenset({"equivalent", "route_less_fallback"})
CATEGORIES = (
    "equivalent",
    "route_less_fallback",
    "invalid_route",
    "drift",
    "missing_target",
    "unpublished_target",
    "pin_mismatch",
    "missing_snapshot",
    "malformed_snapshot",
    "capability_unresolved",
)
SOURCES = ("job_snapshot", "active_revision", "agent_version_pin", "queued_request")
_NO_PIN = object()
_INVALID_PIN = object()


def _is_canonical_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


@dataclass(frozen=True)
class _Definition:
    agent_id: str
    version: int
    status: str
    stored_hash: str
    hash_valid: bool
    capability: str
    value: AgentDefinition | None


class _Catalog:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.versions: dict[str, list[_Definition]] = {}
        for row in rows:
            value: AgentDefinition | None = None
            capability = ""
            try:
                document = json.loads(str(row["definition_json"]))
                if isinstance(document, dict) and isinstance(document.get("capability"), str):
                    capability = document["capability"]
                value = AgentDefinition.model_validate(document)
            except Exception:
                # #204 broad-except audit: only untrusted in-memory definition parsing is
                # degraded to an invalid historical row; DB errors occur outside this block.
                # The report keeps a stable code and no document/parser content.
                value = None
            item = _Definition(
                agent_id=str(row["entity_key"]),
                version=int(row["version"]),
                status=str(row["status"]),
                stored_hash=str(row["definition_hash"]),
                hash_valid=_is_canonical_sha256(row["definition_hash"]),
                capability=capability,
                value=value,
            )
            self.versions.setdefault(item.agent_id, []).append(item)

    def exact(self, agent_id: str, version: int) -> _Definition | None:
        return next(
            (row for row in self.versions.get(agent_id, []) if row.version == version), None
        )

    def published(self, agent_id: str) -> _Definition | None:
        return next(
            (row for row in self.versions.get(agent_id, []) if row.status == "published"), None
        )

    def latest_status(self, agent_id: str) -> str:
        rows = self.versions.get(agent_id, [])
        return max(rows, key=lambda row: row.version).status if rows else "missing"

    def candidates(self, capability: str) -> list[_Definition]:
        if not capability:
            return []
        return sorted(
            (
                row
                for rows in self.versions.values()
                for row in rows
                if row.status == "published" and row.capability == capability
            ),
            key=lambda row: row.agent_id,
        )


@dataclass
class _RunPins:
    workspace_id: str
    pins: dict[str, Any]
    error: str | None = None


def _result(ref: dict[str, Any], category: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {
        **ref,
        **fields,
        "category": category,
        "reason": reason,
        "blocking": category not in SAFE_CATEGORIES,
    }


def _pin_projection(pin: Any) -> dict[str, Any] | None:
    if not isinstance(pin, Mapping):
        return None
    version = pin.get("version")
    definition_hash = pin.get("definition_hash")
    return {
        "agent_id": pin.get("agent_id") if isinstance(pin.get("agent_id"), str) else "",
        "version": version if isinstance(version, int) and not isinstance(version, bool) else None,
        "definition_hash": definition_hash
        if _is_canonical_sha256(definition_hash)
        else "<invalid>",
    }


def _validate_pin(
    catalog: _Catalog, pin: Any
) -> tuple[str | None, _Definition | None, dict[str, Any] | None]:
    projected = _pin_projection(pin)
    if pin is _INVALID_PIN:
        return "malformed_run_pins", None, projected
    if not isinstance(pin, Mapping):
        return "malformed_pin", None, projected
    agent_id = pin.get("agent_id")
    version = pin.get("version")
    definition_hash = pin.get("definition_hash")
    if not isinstance(agent_id, str) or not agent_id:
        return "missing_agent_id", None, projected
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return "invalid_version", None, projected
    if not _is_canonical_sha256(definition_hash):
        return "invalid_definition_hash", None, projected
    target = catalog.exact(agent_id, version)
    if target is None:
        return "missing_version", None, projected
    if not target.hash_valid:
        return "stored_definition_hash_invalid", target, projected
    if target.stored_hash != definition_hash:
        return "definition_hash_mismatch", target, projected
    if target.value is None:
        return "invalid_definition", target, projected
    return None, target, projected


def _classify(
    ref: dict[str, Any],
    capability: str,
    route: Mapping[str, Any] | None,
    catalog: _Catalog,
    pin: Any = _NO_PIN,
) -> dict[str, Any]:
    candidates = catalog.candidates(capability)
    candidate_ids = [row.agent_id for row in candidates]
    fallback = candidates[0] if len(candidates) == 1 else None
    raw_kind = route.get("target_kind") if route is not None else ""
    route_kind = raw_kind if isinstance(raw_kind, str) else "<invalid>"
    raw_target = route.get("target_id") if route is not None else ""
    route_id = raw_target if route_kind == "agent" and isinstance(raw_target, str) else ""
    target = catalog.published(route_id) if route_id else None
    fields: dict[str, Any] = {
        "capability": capability,
        "route_present": route is not None,
        "route_kind": route_kind or None,
        "route_target": route_id or None,
        "route_target_status": catalog.latest_status(route_id) if route_id else None,
        "fallback_target": fallback.agent_id if fallback is not None else None,
        "fallback_candidates": candidate_ids,
    }
    if pin is not _NO_PIN:
        fields["pin"] = _pin_projection(pin)

    if route is not None and route_kind != "agent":
        category, reason = "invalid_route", "route_kind_not_agent"
    elif route is not None and not route_id:
        category, reason = "invalid_route", "route_target_missing"
    elif not capability:
        category, reason = "capability_unresolved", "empty_capability"
    elif route_id:
        if not catalog.versions.get(route_id):
            category, reason = "missing_target", "route_target_missing"
        elif target is None:
            category, reason = "unpublished_target", "route_target_unpublished"
        elif not target.hash_valid:
            category, reason = "pin_mismatch", "stored_definition_hash_invalid"
        elif target.value is None:
            category, reason = "unpublished_target", "route_target_invalid"
        elif target.capability != capability:
            category, reason = "drift", "route_capability_mismatch"
        elif fallback is None or fallback.agent_id != route_id:
            category, reason = "drift", "route_fallback_mismatch"
        else:
            category, reason = "equivalent", "route_matches_unique_fallback"
    elif len(candidates) > 1:
        category, reason = "capability_unresolved", "ambiguous_capability"
    elif fallback is None:
        category, reason = "capability_unresolved", "no_published_fallback"
    elif not fallback.hash_valid:
        category, reason = "pin_mismatch", "stored_definition_hash_invalid"
    elif fallback.value is None:
        category, reason = "unpublished_target", "fallback_definition_invalid"
    else:
        category, reason = "route_less_fallback", "unique_published_fallback"

    if pin is not _NO_PIN:
        pin_error, pinned, projected = _validate_pin(catalog, pin)
        fields["pin"] = projected
        if pin_error is None and pinned is not None and pinned.value is not None:
            effective_id = route_id or (fallback.agent_id if fallback is not None else "")
            if str(pin["agent_id"]) != effective_id:
                pin_error = "pin_route_mismatch"
            elif pinned.capability != capability:
                pin_error = "pin_capability_mismatch"
        if pin_error is not None:
            category, reason = "pin_mismatch", pin_error
    return _result(ref, category, reason, **fields)


def _load_snapshot(document: Any) -> WorkflowDefinition:
    try:
        return workflow_definition_from_dict(document)
    except ValueError:
        if not isinstance(document, dict) or not isinstance(document.get("nodes"), dict):
            raise
        patched = copy.deepcopy(document)
        empty_legacy: list[str] = []
        top_execution = patched.get("execution")
        top_runtime = top_execution.get("runtime") if isinstance(top_execution, dict) else ""
        for node_key, node in patched["nodes"].items():
            if not isinstance(node, dict) or node.get("node_type") != "agent":
                continue
            execution = node.get("execution")
            runtime = execution.get("runtime") if isinstance(execution, dict) else ""
            if node.get("capability", "") == "" and not (runtime or top_runtime):
                node["capability"] = "__route_audit_empty_capability__"
                empty_legacy.append(node_key)
        if not empty_legacy:
            raise
        definition = workflow_definition_from_dict(patched)
        for node_key in empty_legacy:
            definition.nodes[node_key] = replace(definition.nodes[node_key], capability="")
        return definition


def _parse_snapshot(raw: Any) -> tuple[WorkflowDefinition | None, str | None]:
    if not raw:
        return None, "missing_snapshot"
    try:
        return _load_snapshot(json.loads(str(raw))), None
    except Exception:
        # #204 broad-except audit: persisted snapshots are untrusted data; every parser,
        # shape or validation failure becomes one blocking classification without
        # leaking parser text. No DB/external call occurs inside this block.
        return None, "malformed_snapshot"


def _snapshot_result(ref: dict[str, Any], category: str, pin: Any = _NO_PIN) -> dict[str, Any]:
    fields: dict[str, Any] = {"capability": "", "node_key": ref.get("node_key", "")}
    if pin is not _NO_PIN:
        fields["pin"] = _pin_projection(pin)
    return _result(ref, category, category, **fields)


def _read_run_pins(row: Mapping[str, Any]) -> _RunPins:
    result = _RunPins(str(row["workspace_id"]), {})
    try:
        document = json.loads(str(row["frozen_pins_json"]))
    except (TypeError, ValueError):
        result.error = "malformed_frozen_pins"
        return result
    if not isinstance(document, dict):
        result.error = "malformed_frozen_pins"
        return result
    if "agent_versions" not in document:
        return result
    pins = document["agent_versions"]
    if not isinstance(pins, dict):
        result.error = "malformed_agent_versions"
        return result
    result.pins = dict(pins)
    return result


def _job_ref(source: str, job: Mapping[str, Any], node_key: str = "") -> dict[str, Any]:
    return {
        "source": source,
        "workspace_id": str(job["workspace_id"]),
        "job_id": str(job["id"]),
        "run_id": str(job["run_id"] or ""),
        "revision_id": str(job["workflow_revision_id"] or ""),
        "node_key": node_key,
    }


def _request_error(
    item: Mapping[str, Any], request: Mapping[str, Any], pin: Any, catalog: _Catalog
) -> str | None:
    agent_id = str(request["agent_id"])
    pinned_version = request["pinned_agent_version"]
    request_hash = request["agent_definition_hash"]
    if not _is_canonical_sha256(request_hash):
        return "request_definition_hash_invalid"
    if item["category"] in SAFE_CATEGORIES:
        expected = str(item["route_target"] or item["fallback_target"] or "")
        if agent_id != expected:
            return "request_agent_mismatch"
    if pin is _NO_PIN:
        if pinned_version is not None:
            return "unexpected_request_pin"
    elif isinstance(pin, Mapping) and _validate_pin(catalog, pin)[0] is None:
        if pinned_version != pin["version"]:
            return "request_version_mismatch"
        if agent_id != pin["agent_id"]:
            return "request_pin_agent_mismatch"
        if request_hash != pin["definition_hash"]:
            return "request_pin_hash_mismatch"
    target = (
        catalog.exact(agent_id, int(pinned_version))
        if pinned_version is not None
        else catalog.published(agent_id)
    )
    if target is None:
        return "request_definition_hash_mismatch"
    if not target.hash_valid:
        return "stored_definition_hash_invalid"
    if target.stored_hash != request_hash:
        return "request_definition_hash_mismatch"
    return None


def build_agent_route_retirement_audit(reader: AgentBackfillReader) -> dict[str, Any]:
    """Build the complete deterministic audit from one consistent reader snapshot."""
    data = reader.agent_route_retirement_snapshot()
    jobs = data["jobs"]
    jobs_by_id = {str(row["id"]): row for row in jobs}
    jobs_by_run: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        jobs_by_run.setdefault(str(job["run_id"] or ""), []).append(job)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in data["agent_versions"]:
        grouped.setdefault(str(row["workspace_id"] or ""), []).append(row)
    catalogs = {workspace_id: _Catalog(rows) for workspace_id, rows in grouped.items()}
    routes = {(str(row["workspace_id"]), str(row["node_key"])): row for row in data["routes"]}
    run_pins = {str(row["id"]): _read_run_pins(row) for row in data["runs"]}
    parsed: dict[str, tuple[WorkflowDefinition | None, str | None]] = {}
    items: list[dict[str, Any]] = []

    def job_definition(job: Mapping[str, Any]) -> tuple[WorkflowDefinition | None, str | None]:
        job_id = str(job["id"])
        if job_id not in parsed:
            parsed[job_id] = _parse_snapshot(job["workflow_definition_snapshot_json"])
        return parsed[job_id]

    def pin_for(job: Mapping[str, Any], node_key: str) -> Any:
        run = run_pins.get(str(job["run_id"] or ""))
        if run is None:
            return _NO_PIN
        if run.error is not None:
            return _INVALID_PIN
        return run.pins.get(node_key, _NO_PIN)

    def legacy_nodes(
        definition: WorkflowDefinition,
        ref: dict[str, Any],
        job: Mapping[str, Any] | None,
    ) -> None:
        for node_key in sorted(definition.nodes):
            node: WorkflowNode = definition.nodes[node_key]
            if frozen_node_execution_kind(node) != FROZEN_NODE_LEGACY_AGENT:
                continue
            node_ref = {**ref, "node_key": node_key}
            items.append(
                _classify(
                    node_ref,
                    node.capability,
                    routes.get((str(ref["workspace_id"]), node_key)),
                    catalogs.get(str(ref["workspace_id"]), _Catalog([])),
                    pin_for(job, node_key) if job is not None else _NO_PIN,
                )
            )

    for job in jobs:
        definition, error = job_definition(job)
        ref = _job_ref("job_snapshot", job)
        if error is not None:
            items.append(_snapshot_result(ref, error, pin_for(job, "")))
        elif definition is not None:
            legacy_nodes(definition, ref, job)

    for revision in data["active_revisions"]:
        ref = {
            "source": "active_revision",
            "workspace_id": str(revision["workspace_id"]),
            "revision_id": str(revision["id"]),
            "revision_version": int(revision["version"]),
            "node_key": "",
        }
        definition, error = _parse_snapshot(revision["definition_json"])
        if error is not None:
            items.append(_snapshot_result(ref, error))
        elif definition is not None:
            legacy_nodes(definition, ref, None)

    for run_id in sorted(run_pins):
        run = run_pins[run_id]
        ref = {
            "source": "agent_version_pin",
            "workspace_id": run.workspace_id,
            "run_id": run_id,
            "node_key": "",
        }
        if run.error is not None:
            items.append(_result(ref, "pin_mismatch", run.error, pin=None))
            continue
        for node_key in sorted(run.pins):
            pin = run.pins[node_key]
            attached = jobs_by_run.get(run_id, [])
            if not attached:
                error, _target, projected = _validate_pin(
                    catalogs.get(run.workspace_id, _Catalog([])), pin
                )
                items.append(
                    _result(
                        {**ref, "node_key": node_key},
                        "pin_mismatch",
                        error or "no_readable_job",
                        pin=projected,
                    )
                )
                continue
            for job in attached:
                job_ref = {**_job_ref("agent_version_pin", job, node_key)}
                definition, snapshot_error = job_definition(job)
                if snapshot_error is not None:
                    items.append(_snapshot_result(job_ref, snapshot_error, pin))
                    continue
                assert definition is not None
                node = definition.nodes.get(node_key)
                if node is None or frozen_node_execution_kind(node) != FROZEN_NODE_LEGACY_AGENT:
                    items.append(
                        _result(
                            job_ref,
                            "pin_mismatch",
                            "pin_node_missing" if node is None else "pin_node_not_legacy",
                            pin=_pin_projection(pin),
                        )
                    )
                    continue
                items.append(
                    _classify(
                        job_ref,
                        node.capability,
                        routes.get((run.workspace_id, node_key)),
                        catalogs.get(run.workspace_id, _Catalog([])),
                        pin,
                    )
                )

    for request in data["queued_requests"]:
        if request["kind"] != "agent" or request["profile_source"] != "agent_definition":
            continue
        request_job = jobs_by_id.get(str(request["job_id"]))
        ref = {
            "source": "queued_request",
            "workspace_id": str(request["workspace_id"]),
            "job_id": str(request["job_id"]),
            "execution_id": str(request["execution_id"]),
            "node_key": str(request["node_key"]),
        }
        if request_job is None:
            items.append(_snapshot_result(ref, "missing_snapshot"))
            continue
        if str(request_job["workspace_id"]) != str(request["workspace_id"]):
            items.append(_result(ref, "pin_mismatch", "request_workspace_mismatch"))
            continue
        node_key = str(request["node_key"])
        pin = pin_for(request_job, node_key)
        definition, snapshot_error = job_definition(request_job)
        if snapshot_error is not None:
            items.append(_snapshot_result(ref, snapshot_error, pin))
            continue
        assert definition is not None
        node = definition.nodes.get(node_key)
        catalog = catalogs.get(str(request["workspace_id"]), _Catalog([]))
        if node is None or frozen_node_execution_kind(node) != FROZEN_NODE_LEGACY_AGENT:
            items.append(
                _result(ref, "pin_mismatch", "request_node_not_legacy", pin=_pin_projection(pin))
            )
            continue
        item = _classify(
            ref,
            node.capability,
            routes.get((str(request["workspace_id"]), node_key)),
            catalog,
            pin,
        )
        request_error = _request_error(item, request, pin, catalog)
        if request_error is not None:
            item = {**item, "category": "pin_mismatch", "reason": request_error, "blocking": True}
        items.append(item)

    source_counts = Counter(str(item["source"]) for item in items)
    category_counts = Counter(str(item["category"]) for item in items)
    blocking = sum(1 for item in items if item["blocking"])
    items.sort(
        key=lambda item: (
            str(item["source"]),
            str(item["workspace_id"]),
            str(item.get("job_id") or item.get("revision_id") or item.get("run_id") or ""),
            str(item["node_key"]),
            str(item.get("execution_id") or ""),
        )
    )
    return {
        "report": REPORT_KIND,
        "schema_version": 1,
        "mode": "read-only-local",
        "route_retirement_blocked": blocking > 0,
        "summary": {
            "items": len(items),
            "safe_items": len(items) - blocking,
            "blocking_items": blocking,
            "by_category": {name: category_counts[name] for name in CATEGORIES},
            "by_source": {name: source_counts[name] for name in SOURCES},
        },
        "coverage": {
            "jobs": len(jobs),
            "jobs_without_snapshot": sum(
                1 for job in jobs if not job["workflow_definition_snapshot_json"]
            ),
            "malformed_job_snapshots": sum(
                1 for error in parsed.values() if error[1] == "malformed_snapshot"
            ),
            "active_revisions": len(data["active_revisions"]),
            "route_rows": len(data["routes"]),
            "agent_version_rows": len(data["agent_versions"]),
            "agent_version_pins": sum(len(run.pins) for run in run_pins.values()),
            "runs_with_invalid_agent_pins": sum(
                1 for run in run_pins.values() if run.error is not None
            ),
            "queued_requests": len(data["queued_requests"]),
            "queued_legacy_agent_requests": sum(
                1
                for row in data["queued_requests"]
                if row["kind"] == "agent" and row["profile_source"] == "agent_definition"
            ),
        },
        "items": items,
    }
