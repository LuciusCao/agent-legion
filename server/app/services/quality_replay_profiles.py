"""Quality replay execution-profile selection (#1079, #440 D6).

Since #440 P3 an agent node's execution profile versions with the workflow
revision, so a replay compares *revisions* (or the current Studio draft)
instead of Agent versions. This module owns that half of a replay:

- **resolve** — the profile a new replay runs with and its frozen pin
  ``{revision_id, node_key, profile_hash}`` (``node_profile_pins``):
  * no choice + a self-contained snapshot node → the original job's own
    profile (pin names the job's revision, the snapshot is reused as-is);
  * a revision / the draft → that source's node profile is transplanted
    into the copy job's snapshot (only :data:`PROFILE_FIELDS` change; the
    node's inputs/outputs and every other node stay the original's), and
    the rewritten snapshot gets its own ``definition_hash`` so the worker's
    hash-keyed definition cache never mixes it with the original;
  * no choice + a legacy (not inlined) or code node → ``None``: the caller
    keeps the pre-#1079 path (read-only ``agent_versions`` compatibility).
- **options** — the revisions / draft a sample item can be replayed with.
- **describe** — the pin a replay's copy run froze, for the replay views.

Reads go through the JobQueries facade only (BOUNDARY-DATA-001).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from server.app.db.rowmap import parse_object
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.node_profile_pins import (
    PIN_KEY,
    PROFILE_FIELDS,
    node_profile_fields,
    node_profile_hash,
)
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowNode,
    workflow_definition_from_dict,
)
from server.app.workflows.loader import workflow_definition_from_mapping
from server.app.workflows.revision_format import definition_from_job_snapshot, definition_hash
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


@dataclass(frozen=True)
class ReplayProfile:
    """The profile a replay runs with: its frozen pin and the copy snapshot."""

    pin: dict[str, Any]
    #: Rewritten copy-job snapshot (+ its hash); None = reuse the original's.
    snapshot_json: str | None = None
    snapshot_hash: str = ""


def _pin(revision_id: str | None, node: WorkflowNode) -> dict[str, Any]:
    return {
        "revision_id": revision_id,
        "node_key": node.key,
        "profile_hash": node_profile_hash(node),
    }


def _profile_summary(node: WorkflowNode) -> dict[str, Any]:
    return {
        "runtime": node.execution.runtime,
        "provider": node.execution.provider,
        "model": node.execution.model,
        "profile_hash": node_profile_hash(node),
    }


class ReplayProfileResolver:
    def __init__(self, job_db: JobQueries) -> None:
        self.job_db = job_db

    def resolve(
        self,
        workspace_id: str,
        job: dict[str, Any],
        node: WorkflowNode,
        *,
        revision_id: str | None,
        use_draft: bool,
    ) -> ReplayProfile | None:
        if revision_id is None and not use_draft:
            if not is_self_contained_agent_node(node):
                return None
            return ReplayProfile(pin=_pin(str(job["workflow_revision_id"] or "") or None, node))
        if revision_id is not None and use_draft:
            raise InvalidOperationError("choose either a workflow revision or the draft, not both")
        if node.node_type != "agent":
            raise InvalidOperationError(
                f"node {node.key!r} is not an agent node; only agent nodes replay with a"
                " workflow revision's execution profile"
            )
        source = self._source_node(workspace_id, node.key, revision_id)
        snapshot = self._transplant(str(job["workflow_definition_snapshot_json"] or ""), source)
        return ReplayProfile(
            pin=_pin(revision_id, source),
            snapshot_json=snapshot,
            snapshot_hash=definition_hash(snapshot),
        )

    def options(
        self, workspace_id: str, node: WorkflowNode, original_revision_id: str
    ) -> list[dict[str, Any]]:
        """Revisions (newest first) then the draft whose node is self-contained."""
        original_hash = node_profile_hash(node) if is_self_contained_agent_node(node) else ""
        options: list[dict[str, Any]] = []
        for row in self.job_db.list_workflow_revisions(workspace_id, workspace_id):
            source = _self_contained_node(_load_stored(row.get("definition_json")), node.key)
            if source is None:
                continue
            options.append(
                {
                    "source": "revision",
                    "revision_id": str(row["id"]),
                    "revision_version": int(row["version"]),
                    "revision_status": str(row["status"]),
                    "is_original": str(row["id"]) == original_revision_id
                    and _profile_summary(source)["profile_hash"] == original_hash,
                    **_profile_summary(source),
                }
            )
        draft = _self_contained_node(self._draft_definition(workspace_id), node.key)
        if draft is not None:
            options.append(
                {
                    "source": "draft",
                    "revision_id": None,
                    "revision_version": None,
                    "revision_status": "draft",
                    "is_original": False,
                    **_profile_summary(draft),
                }
            )
        return options

    def options_for_job(
        self, workspace_id: str, job: dict[str, Any], node_key: str
    ) -> list[dict[str, Any]]:
        """:meth:`options` for a sample's original job (none for non-agent nodes)."""
        definition = definition_from_job_snapshot(job)
        node = definition.nodes.get(node_key) if definition else None
        if node is None or node.node_type != "agent":
            return []
        return self.options(workspace_id, node, str(job["workflow_revision_id"] or ""))

    def annotate(self, replays: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """*replays* with their frozen profile pin merged in (replay views)."""
        return [{**replay, **self.describe(replay)} for replay in replays]

    def describe(self, replay: dict[str, Any]) -> dict[str, Any]:
        """The ``node_profiles`` pin the replay's copy run froze (empty = none)."""
        empty = {"revision_id": None, "revision_version": None, "profile_hash": ""}
        copy_job = self.job_db.get_job(str(replay.get("replay_job_id") or ""))
        run = self.job_db.get_run(str(copy_job.get("run_id") or "")) if copy_job else None
        pins = parse_object(run.get("frozen_pins_json")).get(PIN_KEY) if run else None
        pin = next(iter(pins.values()), None) if isinstance(pins, dict) and pins else None
        if not isinstance(pin, dict):
            return empty
        revision_id = pin.get("revision_id")
        version = None
        if isinstance(revision_id, str) and revision_id:
            workspace_id = str(copy_job["workspace_id"]) if copy_job else ""
            row = self.job_db.get_workflow_revision(workspace_id, workspace_id, revision_id)
            version = int(row["version"]) if row else None
        return {
            "revision_id": revision_id if isinstance(revision_id, str) else None,
            "revision_version": version,
            "profile_hash": str(pin.get("profile_hash") or ""),
        }

    # -- helpers ---------------------------------------------------------

    def _source_node(
        self, workspace_id: str, node_key: str, revision_id: str | None
    ) -> WorkflowNode:
        if revision_id is None:
            definition = self._draft_definition(workspace_id, strict=True)
            where = "the workflow draft"
        else:
            row = self.job_db.get_workflow_revision(workspace_id, workspace_id, revision_id)
            if row is None:
                raise NotFoundError(f"workflow revision {revision_id!r} not found")
            definition = _load_stored(row.get("definition_json"))
            where = f"workflow revision v{row['version']}"
        source = _self_contained_node(definition, node_key)
        if source is None:
            raise InvalidOperationError(
                f"{where} has no self-contained agent node {node_key!r}"
                " (it needs execution.runtime) to replay with"
            )
        return source

    def _draft_definition(
        self, workspace_id: str, *, strict: bool = False
    ) -> WorkflowDefinition | None:
        """The Studio draft as a definition; None when absent or unloadable.

        ``strict`` (replaying with the draft) reports the real reason instead:
        no draft at all, or the loader error of a draft that does not load.
        """
        draft = self.job_db.get_workspace_workflow_draft(workspace_id)
        if draft is None:
            if strict:
                raise InvalidOperationError("this workspace has no workflow draft to replay with")
            return None
        try:
            raw = yaml.safe_load(str(draft.get("definition_yaml") or ""))
            if not isinstance(raw, dict):
                raise ValueError("the draft YAML is not a mapping")
            return workflow_definition_from_mapping(raw)
        except (yaml.YAMLError, ValueError) as exc:
            if strict:
                raise InvalidOperationError(f"the workflow draft does not load: {exc}") from exc
            return None

    @staticmethod
    def _transplant(snapshot_json: str, source: WorkflowNode) -> str:
        """The copy snapshot with *source*'s profile fields on the target node."""
        try:
            payload = json.loads(snapshot_json)
            raw_node = payload["nodes"][source.key]
        except (TypeError, ValueError, KeyError) as exc:
            raise InvalidOperationError(
                "the original job's frozen snapshot cannot take a replay profile"
            ) from exc
        raw_node.update(node_profile_fields(source))
        raw_node["node_type"] = "agent"
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            reloaded = workflow_definition_from_dict(json.loads(text)).nodes[source.key]
        except (ValueError, KeyError) as exc:
            raise InvalidOperationError(f"replay profile does not fit the snapshot: {exc}") from exc
        # Top-level execution defaults of the original snapshot must not leak
        # into the transplanted node: the reloaded profile has to equal the
        # source's exactly, or the replay would not run what was chosen.
        if node_profile_hash(reloaded) != node_profile_hash(source):
            raise InvalidOperationError(
                f"the chosen execution profile of {source.key!r} cannot be reproduced on the"
                f" original snapshot ({', '.join(PROFILE_FIELDS)} differ after reload)"
            )
        return text


def sampled_agent_version(node: WorkflowNode, item: dict[str, Any]) -> int | None:
    """The Agent version a legacy node's sampled run ran (None = not recorded).

    Quality sampling records it by definition hash (``quality_sample_items``);
    a self-contained or code node never pins an Agent version.
    """
    if node.node_type != "agent" or is_self_contained_agent_node(node):
        return None
    recorded = item.get("agent_version")
    return int(recorded) if recorded is not None else None


def _load_stored(definition_json: Any) -> WorkflowDefinition | None:
    try:
        return workflow_definition_from_dict(json.loads(str(definition_json)))
    except (TypeError, ValueError):
        return None


def _self_contained_node(
    definition: WorkflowDefinition | None, node_key: str
) -> WorkflowNode | None:
    node = definition.nodes.get(node_key) if definition is not None else None
    return node if node is not None and is_self_contained_agent_node(node) else None
