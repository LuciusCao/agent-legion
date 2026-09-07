from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.jobs.queries import JobQueries
from server.app.services.job_errors import DraftWorkflowKeyMismatchError, NotFoundError
from server.app.services.node_codes import NodeCodeService
from server.app.services.workflow_draft_key import require_draft_workflow_key_match
from server.app.services.workflow_draft_publish import (
    publish_workflow_draft,
    validate_workflow_draft_for_publish,
)
from tests.postgres_support import TEST_DATABASE_URL


def _make_skill_repo(repo_dir: Path) -> None:
    """Minimal in-place skill repo (the publish gate only requires .git)."""
    import os
    import subprocess

    repo_dir.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q"], check=True, env=env)


_DRAFT_YAML = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    capability: do_thing
"""

_DRAFT_YAML_WITH_SKILL = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    capability: do_thing
    skill:
      key: education-video-problems-generation/review-questions
      ref: v1.1.0
"""

# Agent 路由形态（#284 显式 type）：skill 门禁的三个用例使用。
_DRAFT_YAML_AGENT = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    type: agent
    capability: do_thing
"""

_DRAFT_YAML_AGENT_WITH_SKILL = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    type: agent
    capability: do_thing
    skill:
      key: education-video-problems-generation/review-questions
      ref: v1.1.0
"""

_DRAFT_YAML_WITH_START = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  _start:
    type: start
    accepted_item_types: [material]
  do_thing:
    capability: do_thing
    after: [_start]
"""


def _workspace(queries: JobQueries) -> dict:
    return queries.create_workspace("draft-publish-ws", default_workflow_key="test_publish_flow")


def _seed_node_code(workspace_id: str) -> None:
    """Publish a no-op workspace node code so the draft node is runnable."""
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(
        workspace_id,
        "test_publish_flow",
        "do_thing",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace_id, "test_publish_flow", "do_thing")


def _patch_agent_catalog(
    monkeypatch: pytest.MonkeyPatch, agents: dict[str, SimpleNamespace]
) -> None:
    """Stage a published Agent catalog without versioned_entities rows.

    The publish gate only reads ``capability``/``skill`` off each definition,
    and a skill-less Agent cannot be staged through AgentDefinition (skill is
    min_length=1 there) — exactly the legacy shape the gate must still reject.
    """
    monkeypatch.setattr(
        "server.app.services.workflow_draft_publish_gates.published_agent_definitions",
        lambda job_db, workspace_id: agents,
    )


def test_publish_rejects_invalid_definition(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    ok, errors = publish_workflow_draft(queries, workspace["id"], "key: only-key\n")

    assert ok is False
    assert errors
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_publish_rejects_unresolvable_capability(tmp_path: Path) -> None:
    """P-0.5: a non-Agent-routed node without published code cannot publish."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)

    assert ok is False
    assert any("do_thing" in error for error in errors)
    assert any("no published node code" in error for error in errors)
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_publish_creates_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)

    assert (ok, errors) == (True, [])
    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert active is not None
    assert active["status"] == "active"


def test_validate_matches_publish_error_set(tmp_path: Path) -> None:
    """validate returns exactly the errors publish would report (前置一致)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    validate_errors = validate_workflow_draft_for_publish(
        queries, workspace["id"], _DRAFT_YAML, True
    )
    ok, publish_errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)

    assert ok is False
    assert validate_errors == publish_errors
    assert any("no published node code" in error for error in validate_errors)
    # Validation is read-only: no revision materialized.
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_validate_clean_with_published_node_code(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])

    assert validate_workflow_draft_for_publish(queries, workspace["id"], _DRAFT_YAML, True) == []


def test_publish_skips_code_resolution_for_start_node(tmp_path: Path) -> None:
    """Start nodes never execute (EXEC-WORKFLOW-START-001): publish validation must
    not demand published node code or a unique Agent for them."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML_WITH_START)

    assert (ok, errors) == (True, [])
    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert active is not None


_DRAFT_YAML_WITH_APPROVAL = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  _start:
    type: start
    accepted_item_types: [material]
  do_thing:
    type: code
    capability: do_thing
    after: [_start]
    outputs: [result.json]
  gate:
    type: approval
    label: 审批
    after: [do_thing]
    inputs: [result.json]
edges:
  - {from: _start, to: do_thing}
  - {from: do_thing, to: gate}
"""


def test_publish_skips_agent_and_code_resolution_for_approval_gates(
    tmp_path: Path,
) -> None:
    """Approval gates never dispatch (EXEC-APPROVAL-001): publish validation
    demands neither a published Agent nor node code for them."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML_WITH_APPROVAL)

    assert (ok, errors) == (True, [])


def test_validate_reports_structural_errors_before_bindings(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    errors = validate_workflow_draft_for_publish(queries, workspace["id"], "key: only-key\n", True)

    assert errors


def test_key_match_guard_passes_for_matching_key(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    require_draft_workflow_key_match(queries, workspace["id"], _DRAFT_YAML)


def test_key_match_guard_rejects_foreign_key(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    with pytest.raises(DraftWorkflowKeyMismatchError):
        require_draft_workflow_key_match(
            queries, workspace["id"], _DRAFT_YAML.replace("test_publish_flow", "foreign_flow")
        )


def test_key_match_guard_ignores_unparseable_yaml(tmp_path: Path) -> None:
    """Structural errors stay with the publish validation set, not the guard."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    require_draft_workflow_key_match(queries, workspace["id"], "key: [unclosed\n")


def test_key_match_guard_unknown_workspace_raises_not_found(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")

    with pytest.raises(NotFoundError):
        require_draft_workflow_key_match(queries, "no_such_workspace", _DRAFT_YAML)


def test_publish_rejects_agent_node_without_any_skill_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #76: an Agent-routed node with no node skill whose published
    Agent also names none cannot publish (neither side binds the content)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {"agent-1": SimpleNamespace(capability="do_thing", skill="")})

    errors = validate_workflow_draft_for_publish(queries, workspace["id"], _DRAFT_YAML_AGENT, True)

    assert any("declares no skill" in error for error in errors)
    assert any("do_thing" in error for error in errors)


def test_publish_accepts_agent_node_with_node_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node-level skill binding satisfies the gate even when the published
    Agent definition names no skill. #322: the in-place repo must exist."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {"agent-1": SimpleNamespace(capability="do_thing", skill="")})
    skill_base = tmp_path / "skills"
    _make_skill_repo(skill_base / "education-video-problems-generation" / "review-questions")

    assert (
        validate_workflow_draft_for_publish(
            queries,
            workspace["id"],
            _DRAFT_YAML_AGENT_WITH_SKILL,
            True,
            skill_base_dir=skill_base,
        )
        == []
    )


def test_publish_rejects_agent_node_whose_skill_repo_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#322 publish gate: a mistyped/unimported skill key fails at publish
    time instead of at first dispatch."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {"agent-1": SimpleNamespace(capability="do_thing", skill="")})

    errors = validate_workflow_draft_for_publish(
        queries,
        workspace["id"],
        _DRAFT_YAML_AGENT_WITH_SKILL,
        True,
        skill_base_dir=tmp_path / "skills",
    )

    assert any("no in-place git repository" in error for error in errors)
    assert any("review-questions" in error for error in errors)


def test_publish_rejects_agent_node_whose_skill_dir_is_not_a_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory at the right path but without .git is not a skill repo."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {"agent-1": SimpleNamespace(capability="do_thing", skill="")})
    skill_base = tmp_path / "skills"
    (skill_base / "education-video-problems-generation" / "review-questions").mkdir(parents=True)

    errors = validate_workflow_draft_for_publish(
        queries,
        workspace["id"],
        _DRAFT_YAML_AGENT_WITH_SKILL,
        True,
        skill_base_dir=skill_base,
    )

    assert any("no in-place git repository" in error for error in errors)


def test_publish_accepts_agent_node_without_skill_when_agent_binds_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy fallback: existing revisions declare no node skill; the Agent
    definition's skill keeps them publishable."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(
        monkeypatch,
        {
            "agent-1": SimpleNamespace(
                capability="do_thing",
                skill="education-video-problems-generation/review-questions",
            )
        },
    )
    skill_base = tmp_path / "skills"
    _make_skill_repo(skill_base / "education-video-problems-generation" / "review-questions")

    assert (
        validate_workflow_draft_for_publish(
            queries, workspace["id"], _DRAFT_YAML_AGENT, True, skill_base_dir=skill_base
        )
        == []
    )


def test_publish_rejects_skill_on_code_routed_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skill declaration is meaningless when the capability resolves to no
    published Agent (the node runs on the code pool)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])
    _patch_agent_catalog(monkeypatch, {})

    errors = validate_workflow_draft_for_publish(
        queries, workspace["id"], _DRAFT_YAML_WITH_SKILL, True
    )

    assert errors
    assert any("only applies to Agent-routed nodes" in error for error in errors)


# --- #432: secret values in draft YAML node.config must fail at publish ---


def _node_schema_yaml(config_block: str) -> str:
    """Draft with a code node declaring a secret field in its config_schema."""
    return f"""
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    type: code
    capability: do_thing
    config_schema:
      type: object
      properties:
        api_key:
          type: string
          secret: true
        kept:
          type: string
    config:
{config_block}
"""


def _seed_secret_schema_node(queries: JobQueries, workspace_id: str) -> None:
    """Publish a node whose schema marks api_key secret (no config values)."""
    _seed_node_code(workspace_id)
    ok, errors = publish_workflow_draft(queries, workspace_id, _node_schema_yaml("      kept: v"))
    assert (ok, errors) == (True, [])


def test_publish_rejects_plaintext_secret_in_node_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#432: a plaintext secret in draft YAML node.config must fail at
    publish — never land in the revision and the intake freeze (the draft
    channel has no vault diversion; the settings nodeConfig PATCH is the
    only channel that diverts, VAULT-SECRET-001)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {})
    _seed_secret_schema_node(queries, workspace["id"])

    ok, errors = publish_workflow_draft(
        queries,
        workspace["id"],
        _node_schema_yaml("      kept: v\n      api_key: sk-live-123"),
    )

    assert ok is False
    assert any(
        "nodes.do_thing.config.api_key" in error and "Secret values" in error for error in errors
    )
    # The error must never echo the submitted value back.
    assert all("sk-live-123" not in error for error in errors)
    # The active revision keeps the last clean publish.
    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert "sk-live-123" not in str(active["definition_json"])


def test_publish_rejects_secret_set_echo_in_node_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#432: the ``{"secret_set": true}`` echo shape under a secret field is
    rejected the same way — it is a settings-API write-only marker, dead
    config if frozen (dispatch sends the dict literal to node code)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {})
    _seed_secret_schema_node(queries, workspace["id"])

    ok, errors = publish_workflow_draft(
        queries,
        workspace["id"],
        _node_schema_yaml("      kept: v\n      api_key: {secret_set: true}"),
    )

    assert ok is False
    assert any(
        "nodes.do_thing.config.api_key" in error and "Secret values" in error for error in errors
    )


def test_publish_rejects_lookalike_secret_ref_markers_in_node_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the exact single-key ``{"secret_ref": "node:..."}`` vault marker
    passes; multi-key lookalikes and non-node ref names are rejected."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {})
    _seed_secret_schema_node(queries, workspace["id"])

    for bad in ("{secret_ref: n, extra: 1}", '{secret_ref: "conn:cms:token"}'):
        ok, errors = publish_workflow_draft(
            queries,
            workspace["id"],
            _node_schema_yaml(f"      kept: v\n      api_key: {bad}"),
        )
        assert ok is False, bad
        assert any("nodes.do_thing.config.api_key" in error for error in errors), bad


def test_validate_surfaces_secret_error_same_as_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validate endpoint (Studio's pre-publish check) reports the same
    error, so the user is pointed at the vault channel before publishing."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {})
    _seed_secret_schema_node(queries, workspace["id"])

    errors = validate_workflow_draft_for_publish(
        queries,
        workspace["id"],
        _node_schema_yaml("      kept: v\n      api_key: sk-live-123"),
        True,
    )

    assert any(
        "nodes.do_thing.config.api_key" in error and "Secret values" in error for error in errors
    )


def test_publish_accepts_exact_vault_marker_in_node_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact vault marker written by the settings PATCH round-trips
    through draft YAML revisions without false positives (a user copying
    the published revision YAML keeps the vault wiring)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {})
    _seed_secret_schema_node(queries, workspace["id"])

    ok, errors = publish_workflow_draft(
        queries,
        workspace["id"],
        _node_schema_yaml(
            '      kept: v\n      api_key: {secret_ref: "node:test_publish_flow:do_thing:api_key"}'
        ),
    )

    assert (ok, errors) == (True, [])


# --- codex P1 on #432: plaintext secret schema defaults must fail at publish ---


def _secret_default_yaml(*, with_config: bool) -> str:
    """Draft whose node declares api_key secret WITH a plaintext default."""
    config_block = "      kept: v\n" if with_config else ""
    return f"""
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    type: code
    capability: do_thing
    config_schema:
      type: object
      properties:
        api_key:
          type: string
          secret: true
          default: schema-default-cred
        kept:
          type: string
    config:
{config_block}      kept: v
"""


def test_publish_rejects_secret_schema_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex P1 on #432: a secret property with a plaintext schema default
    fails at publish/validate — the value gate cannot see it (node config
    may not set the field), but the schema-default merge would freeze the
    credential verbatim into every intake snapshot."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _patch_agent_catalog(monkeypatch, {})
    _seed_node_code(workspace["id"])

    for with_config in (False, True):
        errors = validate_workflow_draft_for_publish(
            queries, workspace["id"], _secret_default_yaml(with_config=with_config), True
        )
        # Either gate may fire first: the parse-time channel check
        # (``Node do_thing.config_schema: …`` — every declaration channel
        # rejects secret+default) or the publish-gate copy carrying the
        # vault-channel guide. Both name the field and never echo the value.
        assert any(
            "config_schema.properties.api_key" in error and "cannot declare a default" in error
            for error in errors
        ), with_config

    ok, errors = publish_workflow_draft(
        queries, workspace["id"], _secret_default_yaml(with_config=False)
    )
    assert ok is False
    # The error never echoes the default's value; nothing was published.
    assert all("schema-default-cred" not in error for error in errors)
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None
