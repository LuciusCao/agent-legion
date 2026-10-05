"""Dual-track publish gate (#933, #440 P2).

Self-contained agent nodes (``execution.runtime``) publish in a workspace
with no Agent definitions and materialize no route; half-filled profiles
are rejected; legacy agent nodes keep requiring exactly one published
Agent. The scan-gate probe sees the self-contained active revision.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from server.app.jobs.queries import JobQueries
from server.app.jobs.queries.agent_profile_scan import has_self_contained_agent_nodes
from server.app.services.agent_node_profile_catalog import agent_profiles_may_exist
from server.app.services.workflow_draft_publish import validate_workflow_draft_for_publish
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping
from tests.postgres_support import TEST_DATABASE_URL

_SKILL = "education-video-problems-generation/review-questions"


def _yaml(node_extra: str, top: str = "") -> str:
    return f"""
key: profile_flow
label: Profile Flow
{top}
nodes:
  draft:
    type: agent
    capability: draft
    outputs: [draft.json]
    skill:
      key: {_SKILL}
{node_extra}
"""


_SELF_CONTAINED = _yaml(
    "    requires_labels: {gpu: 'yes'}\n",
    top="execution:\n  runtime: velites\n  provider: p\n  model: m",
)


def _skill_base(tmp_path: Path) -> Path:
    base = tmp_path / "skills"
    repo = base / _SKILL
    repo.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=env)
    return base


def _workspace(queries: JobQueries) -> str:
    return str(queries.create_workspace("profile-ws", workspace_id="profile_flow")["id"])


def _routes(queries: JobQueries, workspace_id: str) -> list[dict]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select node_key, target_kind from workspace_node_routes where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def test_self_contained_node_publishes_without_any_agent_definition(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace_id = _workspace(queries)

    errors = validate_workflow_draft_for_publish(
        queries, workspace_id, _SELF_CONTAINED, True, skill_base_dir=_skill_base(tmp_path)
    )

    assert errors == []


def test_half_filled_profile_is_rejected_at_publish(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace_id = _workspace(queries)

    errors = validate_workflow_draft_for_publish(
        queries,
        workspace_id,
        _yaml("    requires_labels: {gpu: 'yes'}\n"),
        True,
        skill_base_dir=_skill_base(tmp_path),
    )

    assert any("requires_labels but no execution.runtime" in error for error in errors)


def test_legacy_agent_node_still_needs_a_published_agent(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace_id = _workspace(queries)

    errors = validate_workflow_draft_for_publish(
        queries, workspace_id, _yaml(""), True, skill_base_dir=_skill_base(tmp_path)
    )

    assert any("must resolve to exactly one published Agent" in error for error in errors)


def test_self_contained_revision_materializes_no_route_and_opens_the_scan_gate(
    tmp_path: Path,
) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace_id = _workspace(queries)
    assert agent_profiles_may_exist(queries) is False

    raw = yaml.safe_load(_SELF_CONTAINED)
    raw["key"] = workspace_id
    WorkflowRevisionService(queries, True).save_workspace_revision(
        workspace_id, workflow_definition_from_mapping(raw)
    )

    assert _routes(queries, workspace_id) == []
    # The poll-loop scan gates (thread.py / agent_gate.py) must open with
    # zero published Agents anywhere (#933 high-risk gate).
    assert has_self_contained_agent_nodes(queries) is True
    assert agent_profiles_may_exist(queries) is True


def test_scan_probe_ignores_legacy_agent_revisions(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace_id = _workspace(queries)
    corrupt_ws = str(queries.create_workspace("corrupt-ws", workspace_id="corrupt-ws")["id"])
    with queries.connect() as conn:
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, version, status,"
            " definition_json, definition_hash)"
            " values ('legacy-rev', %s, 1, 'active', %s, 'h')",
            (
                workspace_id,
                '{"nodes": {"draft": {"node_type": "agent", "execution": {"provider": "p"}}}}',
            ),
        )
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, version, status,"
            " definition_json, definition_hash)"
            " values ('corrupt-rev', %s, 1, 'active', 'not json', 'h')",
            (corrupt_ws,),
        )

    assert has_self_contained_agent_nodes(queries) is False


def test_scan_probe_keeps_in_flight_self_contained_jobs_after_a_legacy_republish(
    tmp_path: Path,
) -> None:
    """A runnable job pinned to an older self-contained revision keeps the
    scan gate open after the workspace publishes a legacy-only revision
    (PR #1039 codex R1); once the job is terminal the gate closes."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace_id = _workspace(queries)
    self_contained = (
        '{"nodes": {"draft": {"node_type": "agent", "execution": {"runtime": "velites"}}}}'
    )
    legacy = '{"nodes": {"draft": {"node_type": "agent", "execution": {"runtime": ""}}}}'
    with queries.connect() as conn:
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, version, status,"
            " definition_json, definition_hash) values"
            " ('old-rev', %s, 1, 'archived', %s, 'h1'), ('new-rev', %s, 2, 'active', %s, 'h2')",
            (workspace_id, self_contained, workspace_id, legacy),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status,"
            " workflow_revision_id) values ('inflight', %s, 'question', 'q', 'running', 'old-rev')",
            (workspace_id,),
        )

    assert has_self_contained_agent_nodes(queries) is True

    with queries.connect() as conn:
        conn.execute("update jobs set status='completed' where id='inflight'")

    assert has_self_contained_agent_nodes(queries) is False


def test_runtime_change_publishes_a_new_revision_but_model_edits_stay_in_place(
    tmp_path: Path,
) -> None:
    """``execution.runtime`` picks the profile source and is frozen with the
    snapshot: changing it must publish a new revision so in-flight jobs keep
    the old one (PR #1039 codex R5); provider/model edits stay in place."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace_id = _workspace(queries)
    service = WorkflowRevisionService(queries, True)

    def _save(top_execution: dict) -> dict:
        raw = yaml.safe_load(_SELF_CONTAINED)
        raw["key"] = workspace_id
        raw["execution"] = top_execution
        raw["nodes"]["draft"].pop("requires_labels")
        return service.save_workspace_revision(workspace_id, workflow_definition_from_mapping(raw))

    first = _save({"runtime": "velites", "provider": "p", "model": "m"})
    in_place = _save({"runtime": "velites", "provider": "p", "model": "m2"})
    assert in_place["id"] == first["id"]

    switched = _save({"provider": "p", "model": "m2"})  # runtime removed
    assert switched["id"] != first["id"]
    with queries._connect_read() as conn:
        old = conn.execute(
            "select definition_json from workflow_revisions where id=%s", (first["id"],)
        ).fetchone()
    # The frozen revision still carries the self-contained profile.
    assert '"runtime":"velites"' in str(old["definition_json"])
