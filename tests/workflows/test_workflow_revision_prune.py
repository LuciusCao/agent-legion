"""Publish-time workspace override prune tests (codex 终轮 on #428).

Split from test_workflow_revisions.py to respect the 1000-line test-file
budget. The prune itself lives in
``server/app/services/node_config_prune.py``; these tests pin its three
terminal-review findings: full-constraint value checks (enum/minimum
tightening, P1-2), the delete-not-migrate semantics for fields that flip
secret (P1-1), and the prune's place INSIDE the publish transaction with a
section-scoped workspace write (P1-3).
"""

from dataclasses import replace as dc_replace
from pathlib import Path

import pytest

from server.app.jobs.queries import JobQueries
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import WorkflowDefinition
from tests.helpers import load_builtin_definition
from tests.postgres_support import TEST_DATABASE_URL


def _publish_with_node_schema(
    queries: JobQueries,
    workspace_id: str,
    properties: dict,
) -> WorkflowDefinition:
    """Publish the demo DAG with intake_knowledge_points declaring a schema.

    Same shape as the helper in test_workflow_revisions.py; duplicated rather
    than imported because that module is a test file, not a helper module.
    """
    definition = load_builtin_definition("education_video_problems_generation")
    patched = dc_replace(
        definition,
        nodes={
            **definition.nodes,
            "intake_knowledge_points": dc_replace(
                definition.nodes["intake_knowledge_points"],
                config_schema={"type": "object", "properties": properties},
            ),
        },
    )
    WorkflowRevisionService(queries).publish_workspace_revision(workspace_id, patched)
    return patched


def test_publish_prunes_override_values_outside_tightened_enum(tmp_path: Path) -> None:
    """codex 终轮 P1-2: a tightened enum (type unchanged) must prune the
    stale value too — only the type was checked before, so an override left
    at the removed enum member blocked every new job at intake's
    validate_config_values."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-enum-ws", default_workflow_key="education_video_problems_generation"
    )

    _publish_with_node_schema(
        queries,
        workspace["id"],
        {
            "subject": {"type": "string", "enum": ["v1", "v2"]},
            "kept": {"type": "string"},
        },
    )
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {
                "intake_knowledge_points": {"subject": "v1", "kept": "v"}
            }
        },
    )

    # v2 drops v1 from the enum; the type (string) is unchanged.
    v2 = _publish_with_node_schema(
        queries,
        workspace["id"],
        {
            "subject": {"type": "string", "enum": ["v2"], "default": "v2"},
            "kept": {"type": "string"},
        },
    )

    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {"kept": "v"}
    # The out-of-enum override is gone; the schema default resolves cleanly.
    resolved = resolve_workflow_node_configs(v2, {}, queries.get_workspace(workspace["id"]))
    assert resolved["intake_knowledge_points"]["subject"] == "v2"


def test_publish_prunes_override_values_below_tightened_minimum(tmp_path: Path) -> None:
    """codex 终轮 P1-2: a tightened minimum prunes now-out-of-range values
    the bare type check kept (integer stayed integer)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-min-ws", default_workflow_key="education_video_problems_generation"
    )

    _publish_with_node_schema(
        queries,
        workspace["id"],
        {"count": {"type": "integer", "minimum": 1}, "kept": {"type": "string"}},
    )
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {
                "intake_knowledge_points": {"count": 1, "kept": "v"}
            }
        },
    )

    v2 = _publish_with_node_schema(
        queries,
        workspace["id"],
        {"count": {"type": "integer", "minimum": 5}, "kept": {"type": "string"}},
    )

    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {"kept": "v"}
    resolved = resolve_workflow_node_configs(v2, {}, queries.get_workspace(workspace["id"]))
    # The default-less property simply falls out of the resolution.
    assert "count" not in resolved["intake_knowledge_points"]


def test_publish_prunes_plaintext_override_of_newly_secret_field(tmp_path: Path) -> None:
    """codex 终轮 P1-1: a field that flips to secret: true in the new
    revision keeps only genuine vault markers — the stored plaintext (from
    the pre-secret revision) is deleted, never migrated into the vault; the
    user re-enters it through the settings PATCH's vault channel."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-secret-ws", default_workflow_key="education_video_problems_generation"
    )

    # v1: token is a plain string field with a plaintext override.
    _publish_with_node_schema(
        queries,
        workspace["id"],
        {"token": {"type": "string"}, "kept": {"type": "string"}},
    )
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {
                "intake_knowledge_points": {"token": "plain-text", "kept": "v"}
            }
        },
    )

    # v2: token flips secret — the plaintext override must go while the
    # sibling key stays. A pre-existing vault marker would have stayed (the
    # settings PATCH stores {"secret_ref": ...} for secret fields — see the
    # companion test below).
    v2 = _publish_with_node_schema(
        queries,
        workspace["id"],
        {"token": {"type": "string", "secret": True}, "kept": {"type": "string"}},
    )

    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {"kept": "v"}
    resolved = resolve_workflow_node_configs(v2, {}, queries.get_workspace(workspace["id"]))
    assert "token" not in resolved["intake_knowledge_points"]


def test_publish_keeps_secret_ref_marker_on_unchanged_secret_field(tmp_path: Path) -> None:
    """codex 终轮 P1-1 的对照面: a field that was already secret keeps its
    stored {"secret_ref": ...} marker (the settings PATCH writes exactly
    that shape) — the prune must not mistake the marker for stale plaintext
    and drop the workspace's vault wiring."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-secret-keep-ws", default_workflow_key="education_video_problems_generation"
    )

    secret_schema = {"token": {"type": "string", "secret": True}, "kept": {"type": "string"}}
    _publish_with_node_schema(queries, workspace["id"], secret_schema)
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {
                "intake_knowledge_points": {
                    "token": {"secret_ref": "node:wf:intake_knowledge_points:token"},
                    "kept": "v",
                }
            }
        },
    )

    # Republish with the same secret schema (a constraint elsewhere changed).
    _publish_with_node_schema(queries, workspace["id"], secret_schema)

    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {
        "token": {"secret_ref": "node:wf:intake_knowledge_points:token"},
        "kept": "v",
    }


def test_publish_prune_failure_rolls_back_revision_and_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex 终轮 P1-3: a prune failure inside the publish transaction must
    roll the whole publish back — the new revision must not go active with
    the stale overrides still blocking intake, and the workspace overrides
    must keep their pre-publish values (no half-applied prune)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-tx-ws", default_workflow_key="education_video_problems_generation"
    )

    _publish_with_node_schema(
        queries,
        workspace["id"],
        {"old_key": {"type": "integer", "default": 1}, "kept": {"type": "string"}},
    )
    stale_overrides = {
        "education_video_problems_generation": {
            "intake_knowledge_points": {"old_key": 5, "kept": "v"}
        }
    }
    queries.update_workspace(workspace["id"], node_config=stale_overrides)

    active_before = queries.get_active_workflow_revision(
        workspace["id"], "education_video_problems_generation"
    )

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("prune exploded mid-transaction")

    monkeypatch.setattr(queries, "write_workspace_node_config_section", _boom)

    with pytest.raises(RuntimeError, match="prune exploded"):
        _publish_with_node_schema(
            queries,
            workspace["id"],
            {"new_key": {"type": "integer", "default": 2}, "kept": {"type": "string"}},
        )

    monkeypatch.undo()

    # The revision insert rolled back: still exactly the pre-publish active
    # revision, and no extra version row was allocated.
    assert (
        queries.get_active_workflow_revision(
            workspace["id"], "education_video_problems_generation"
        )["id"]
        == active_before["id"]
    )
    with queries.connect() as conn:
        rows = conn.execute(
            "select count(*) as n from workflow_revisions where workspace_id = %s",
            (workspace["id"],),
        ).fetchone()
    assert rows["n"] == 1
    # The workspace overrides keep their pre-publish values — the prune
    # (which would have dropped old_key) never landed.
    assert queries.get_workspace(workspace["id"])["node_config"] == stale_overrides


def test_publish_prune_preserves_sibling_workflow_sections(tmp_path: Path) -> None:
    """codex 终轮 P1-3 的并发面: the in-transaction prune rewrites only the
    publishing workflow's section — a sibling workflow's overrides in the
    same node_config_json column survive the section-scoped write."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-sibling-ws", default_workflow_key="education_video_problems_generation"
    )

    _publish_with_node_schema(queries, workspace["id"], {"old_key": {"type": "integer"}})
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {"intake_knowledge_points": {"old_key": 5}},
            "unrelated_workflow": {"some_node": {"other": "kept"}},
        },
    )

    _publish_with_node_schema(queries, workspace["id"], {"new_key": {"type": "integer"}})

    stored = queries.get_workspace(workspace["id"])["node_config"]
    # The pruned workflow's section dropped the stale key…
    assert "intake_knowledge_points" not in stored["education_video_problems_generation"]
    # …while the sibling workflow's section is untouched.
    assert stored["unrelated_workflow"] == {"some_node": {"other": "kept"}}


def test_locked_prune_recomputes_from_concurrent_patch_values(tmp_path: Path) -> None:
    """codex 终轮 P1-1: the in-transaction prune must RE-compute under the
    section write's row lock, not write the plan-era snapshot back. A
    settings PATCH that lands after the plan read but before the lock would
    be flattened by a snapshot write-back: its legal fresh key vanishes and
    its schema-violating fresh key survives to block intake. Recomputation
    judges the PATCH's committed values by the new schema: legal keys stay,
    violating keys go."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-race-ws", default_workflow_key="education_video_problems_generation"
    )
    # v1 gives the node a schema so a stale override exists to plan a prune.
    _publish_with_node_schema(
        queries,
        workspace["id"],
        {"stale_key": {"type": "integer"}, "kept": {"type": "string"}},
    )
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {
                "intake_knowledge_points": {"stale_key": 5, "kept": "v"}
            }
        },
    )

    # The concurrent PATCH commits between the plan read and the lock: the
    # plan has already decided a prune is needed, then the PATCH replaces
    # the section — stale_key gone, a legal fresh key plus a schema-violating
    # fresh key in. A snapshot write-back would restore stale_key AND drop
    # legal_key; recomputation keeps legal_key and drops bad_type_key.
    from server.app.services.node_config_prune import override_prune_commit_hook

    original_hook = override_prune_commit_hook

    def _hook_with_mid_flight_patch(job_db, workspace_id, definition, agent_definitions):
        hook = original_hook(job_db, workspace_id, definition, agent_definitions)
        queries.update_workspace(
            workspace["id"],
            node_config={
                "education_video_problems_generation": {
                    "intake_knowledge_points": {
                        "legal_key": "fresh",
                        "bad_type_key": {"not": "a string"},
                    }
                }
            },
        )
        return hook

    patched_definition = dc_replace(
        load_builtin_definition("education_video_problems_generation"),
        nodes={
            **load_builtin_definition("education_video_problems_generation").nodes,
            "intake_knowledge_points": dc_replace(
                load_builtin_definition("education_video_problems_generation").nodes[
                    "intake_knowledge_points"
                ],
                config_schema={
                    "type": "object",
                    "properties": {
                        "legal_key": {"type": "string"},
                        "bad_type_key": {"type": "string"},
                    },
                },
            ),
        },
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "server.app.services.workflow_revision_pipeline.override_prune_commit_hook",
            _hook_with_mid_flight_patch,
        )
        WorkflowRevisionService(queries).publish_workspace_revision(
            workspace["id"], patched_definition
        )

    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {
        "legal_key": "fresh"
    }


def test_publish_prunes_override_of_node_left_without_schema(tmp_path: Path) -> None:
    """codex 终轮 P1-2: a node that STAYS in the revision while its schema
    goes (published Agent drops its config_schema) must have its override
    cleared — the empty schema / non-empty override state is exactly what
    resolve_node_config rejects at intake. A node the revision DROPPED keeps
    its override: resolve skips it and a later re-add still finds the tuning."""
    from server.app.agent_catalog import AgentDefinition
    from tests.helpers import replace_agent_catalog

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-schemaless-ws", default_workflow_key="agent_nodes_flow"
    )
    agent = AgentDefinition(capability="review_script", runtime="velites")
    replace_agent_catalog(workspace["id"], {"review-script-v1": agent})

    def _definition(*, with_extra: bool) -> WorkflowDefinition:
        from server.app.workflows.definition import workflow_definition_from_mapping

        nodes: dict[str, dict] = {
            "write_script": {"type": "agent", "capability": "write_script"},
            "review_script": {"type": "agent", "capability": "review_script"},
        }
        if with_extra:
            nodes["temp_helper"] = {
                "type": "code",
                "capability": "temp_helper",
                "after": ["write_script"],
            }
        return workflow_definition_from_mapping(
            {
                "key": "agent_nodes_flow",
                "label": "Agent Nodes Flow",
                "nodes": nodes,
            }
        )

    # v1: the Agent carries a config_schema; overrides exist for the node and
    # (planted raw) for a node the next revision drops.
    agent_with_schema = AgentDefinition(
        capability="review_script",
        runtime="velites",
        config_schema={"type": "object", "properties": {"kept": {"type": "string"}}},
    )
    replace_agent_catalog(workspace["id"], {"review-script-v1": agent_with_schema})
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(workspace["id"], _definition(with_extra=True))
    queries.update_workspace(
        workspace["id"],
        node_config={
            "agent_nodes_flow": {
                "review_script": {"kept": "v"},
                "temp_helper": {"timeout_seconds": 60},
            }
        },
    )

    # v2: the Agent drops its config_schema (review_script loses its surface),
    # and temp_helper leaves the revision — the override must be cleared for
    # the former and kept for the latter.
    replace_agent_catalog(workspace["id"], {"review-script-v1": agent})
    service.publish_workspace_revision(workspace["id"], _definition(with_extra=False))

    stored = queries.get_workspace(workspace["id"])["node_config"]["agent_nodes_flow"]
    assert "review_script" not in stored
    assert stored["temp_helper"] == {"timeout_seconds": 60}


def test_publish_prunes_secret_ref_marker_when_field_flips_plain(tmp_path: Path) -> None:
    """codex 终轮 P1-3: the {"secret_ref": ...} marker exemption is bound to
    the field STILL being secret in the published schema. When the field
    flips back to a plain string, the marker is an ordinary value — the
    generic validation (which the secret strip no longer shields it from)
    rejects the dict-under-string, so the prune removes it there instead of
    leaving it to block intake."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-unsecret-ws", default_workflow_key="education_video_problems_generation"
    )
    marker = {"secret_ref": "node:wf:intake_knowledge_points:token"}
    secret_schema = {"token": {"type": "string", "secret": True}, "kept": {"type": "string"}}
    _publish_with_node_schema(queries, workspace["id"], secret_schema)
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {
                "intake_knowledge_points": {"token": marker, "kept": "v"}
            }
        },
    )

    # v2 drops the secret flag: the leftover marker must go while the sibling
    # key survives the same publish.
    v2 = _publish_with_node_schema(
        queries,
        workspace["id"],
        {"token": {"type": "string"}, "kept": {"type": "string"}},
    )

    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {"kept": "v"}
    resolved = resolve_workflow_node_configs(v2, {}, queries.get_workspace(workspace["id"]))
    assert "token" not in resolved["intake_knowledge_points"]

    # The companion face: still secret in v3 → the marker keeps its vault wiring.
    _publish_with_node_schema(queries, workspace["id"], secret_schema)
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {
                "intake_knowledge_points": {"token": marker, "kept": "v"}
            }
        },
    )
    _publish_with_node_schema(queries, workspace["id"], secret_schema)
    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {
        "token": marker,
        "kept": "v",
    }
