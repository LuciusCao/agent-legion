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
