import json
from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.jobs.queries import JobQueries
from server.app.main import create_app
from server.app.services.node_codes import NodeCodeService
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.services.workflow_drafts import (
    validate_workflow_definition,
    validate_workflow_for_publish,
    workflow_definition_from_yaml_string,
)
from server.app.services.workflow_revision_format import (
    definition_hash,
    definition_to_yaml,
    serialize_definition,
    workflow_definition_to_response_payload,
)
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.schema import WorkflowReduceSpec, WorkflowShardSpec
from tests.helpers import (
    load_builtin_definition,
    replace_agent_catalog,
    seed_workspace_agent_definitions,
)
from tests.helpers.auth import authenticate_client
from tests.postgres_support import TEST_DATABASE_URL


def test_publish_and_get_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    # Agent definitions are workspace-scoped (schema v46): seed the demo
    # templates into this workspace so its routes resolve.
    seed_workspace_agent_definitions(workspace["id"])
    definition = load_builtin_definition("education_video_problems_generation")
    service = WorkflowRevisionService(queries)

    revision = service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    assert active["id"] == revision["id"]
    assert active["workspace_id"] == workspace["id"]
    assert active["version"] == 1
    assert active["status"] == "active"
    assert active["definition_hash"]
    assert active["definition_json"]
    with queries._connect_read() as conn:
        route = conn.execute(
            "select target_kind, target_id from workspace_node_routes"
            " where workspace_id=%s and node_key='write_script'",
            (workspace["id"],),
        ).fetchone()
        capacity = conn.execute(
            "select max_concurrency, source_revision_id from workspace_node_capacities"
            " where workspace_id=%s and node_key='write_script'",
            (workspace["id"],),
        ).fetchone()
    assert route is not None
    assert dict(route) == {"target_kind": "agent", "target_id": "example-write-script-v1"}
    # Agent capacity is workspace-level now; publish no longer writes per-node rows.
    assert capacity is None


def test_runtime_only_save_updates_active_revision_without_new_version(
    tmp_path: Path,
) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("runtime-ws", default_workflow_key="runtime-flow")
    service = WorkflowRevisionService(queries)
    original = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json"],
                    "execution": {"provider": "gateway", "model": "old-model"},
                }
            },
        }
    )
    first = service.publish_workspace_revision(workspace["id"], original)
    updated = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json"],
                    "execution": {"provider": "gateway", "model": "new-model"},
                }
            },
        }
    )

    same = service.save_workspace_revision(workspace["id"], updated)

    assert same["id"] == first["id"]
    assert same["version"] == 1
    assert same["definition_hash"] != first["definition_hash"]
    assert '"new-model"' in same["definition_json"]

    structural = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json", "metadata.json"],
                }
            },
        }
    )
    second = service.save_workspace_revision(workspace["id"], structural)
    assert second["version"] == 2
    assert second["id"] != first["id"]


def test_config_only_save_creates_new_revision(tmp_path: Path) -> None:
    """Issue #418: node ``config`` / ``config_schema`` are structural — a
    config-only save publishes a new revision (the same fact compare now
    reports via creates_revision; the two must stay aligned)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("runtime-ws", default_workflow_key="runtime-flow")
    service = WorkflowRevisionService(queries)
    original = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json"],
                }
            },
        }
    )
    first = service.publish_workspace_revision(workspace["id"], original)

    # config-only change (e.g. a code node gaining sandbox_network).
    config_only = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json"],
                    "config": {"sandbox_network": True},
                }
            },
        }
    )
    second = service.save_workspace_revision(workspace["id"], config_only)
    assert second["version"] == 2
    assert second["id"] != first["id"]
    assert json.loads(second["definition_json"])["nodes"]["generate"]["config"] == {
        "sandbox_network": True
    }

    # config_schema-only change (declare a tunable property with a default).
    schema_only = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json"],
                    "config": {"sandbox_network": True},
                    "config_schema": {
                        "type": "object",
                        "properties": {
                            "max_words": {"type": "integer", "default": 800},
                        },
                    },
                }
            },
        }
    )
    third = service.save_workspace_revision(workspace["id"], schema_only)
    assert third["version"] == 3
    assert third["id"] != second["id"]


def _agent_nodes_definition(*, review_as_local: bool) -> WorkflowDefinition:
    # Explicit node types (#284): write_script always runs as an Agent node;
    # review_as_local flips the review node to a code node with a capability
    # no published Agent implements.
    return workflow_definition_from_mapping(
        {
            "key": "agent_nodes_flow",
            "label": "Agent Nodes Flow",
            "nodes": {
                "write_script": {
                    "type": "agent",
                    "capability": "write_script",
                },
                "review_script": {
                    "type": "code" if review_as_local else "agent",
                    "capability": "local_review" if review_as_local else "review_script",
                    "after": ["write_script"],
                },
            },
        }
    )


def _route_and_capacity_rows(queries: JobQueries, workspace_id: str) -> dict:
    with queries._connect_read() as conn:
        routes = conn.execute(
            "select node_key, target_kind, target_id from workspace_node_routes"
            " where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
        capacities = conn.execute(
            "select node_key, max_concurrency from workspace_node_capacities where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
    return {
        "routes": {row["node_key"]: (row["target_kind"], row["target_id"]) for row in routes},
        "capacities": {row["node_key"]: row["max_concurrency"] for row in capacities},
    }


def test_republish_deletes_stale_agent_route_and_capacity_rows(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    seed_workspace_agent_definitions(workspace["id"])
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=False)
    )
    demo_definition = load_builtin_definition("education_video_problems_generation")
    service.publish_workspace_revision(workspace["id"], demo_definition)
    # Legacy projection: a stale per-node capacity row must be pruned by the
    # next publish even though publish no longer writes such rows.
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_node_capacities(workspace_id, node_key, max_concurrency)"
            " values (%s, 'write_script', 20)",
            (workspace["id"],),
        )

    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=True)
    )

    # #211 Phase 3 (M1): the projection is per-WORKSPACE now (v62 binding —
    # one workspace, one workflow). The last publish (review_as_local=True)
    # owns the projection: write_script stays Agent-routed, review_script is
    # a code node (no route), and every earlier row — the demo workflow's and
    # the first publish's — was pruned by that same publish.
    flow_rows = _route_and_capacity_rows(queries, workspace["id"])
    assert set(flow_rows["routes"]) == {"write_script"}
    assert flow_rows["routes"]["write_script"] == ("agent", "example-write-script-v1")
    assert flow_rows["capacities"] == {}


def test_archived_agent_does_not_rewrite_routes_until_next_publish(tmp_path: Path) -> None:
    """Explicit types (#284): Agent publish/archive never rewrites routes —
    they only change when a new revision is published (the startup reconcile
    was retired with the explicit-type cutover)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    seed_workspace_agent_definitions(workspace["id"])
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=False)
    )

    # Archive every published Agent: the materialized rows stay untouched.
    replace_agent_catalog(workspace["id"], {})
    rows = _route_and_capacity_rows(queries, workspace["id"])
    assert rows["routes"]["write_script"] == ("agent", "example-write-script-v1")

    # The next publish re-derives routes from the (now empty) catalog and
    # prunes the stale agent rows.
    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=False)
    )
    rows = _route_and_capacity_rows(queries, workspace["id"])
    assert rows["routes"] == {}


def test_publish_rejects_ambiguous_agent_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent-typed node whose capability has >1 published Agent fails
    publish validation. The DB partial unique index makes this
    unrepresentable via real rows, so stub the catalog read (the guard is
    defense in depth for catalogs produced before the index existed)."""
    from server.app.agent_catalog import AgentDefinition

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    ambiguous = {
        "write-script-v1": AgentDefinition(
            capability="write_script", runtime="velites", skill="example/write-script"
        ),
        "write-script-v2": AgentDefinition(
            capability="write_script", runtime="velites", skill="example/write-script-v2"
        ),
    }
    monkeypatch.setattr(
        "server.app.services.workflow_draft_publish_gates.published_agent_definitions",
        lambda _dsn, _workspace_id: ambiguous,
    )

    errors = validate_workflow_for_publish(
        definition=_agent_nodes_definition(review_as_local=True),
        workspace_id=workspace["id"],
        job_db=queries,
        custom_nodes_enabled=True,
    )

    assert any(
        "write_script must resolve to exactly one published Agent" in error for error in errors
    )


def test_create_job_stores_workflow_revision_snapshot(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    service = WorkflowRevisionService(queries)
    revision = service.publish_workspace_revision(workspace["id"], definition)

    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace["id"],
        workflow_revision_id=revision["id"],
        workflow_version=revision["version"],
        workflow_definition_hash=revision["definition_hash"],
        workflow_definition_snapshot_json=revision["definition_json"],
    )

    assert job["workflow_revision_id"] == revision["id"]
    assert job["workflow_version"] == revision["version"]
    assert job["workflow_definition_hash"] == revision["definition_hash"]
    assert "intake_knowledge_points" in job["workflow_definition_snapshot_json"]


def test_validate_workflow_definition_reports_malformed_yaml() -> None:
    errors = validate_workflow_definition("nodes: [broken")

    assert len(errors) == 1
    assert "not valid YAML" in errors[0]


def test_workflow_definition_from_yaml_string_raises_definition_error_on_bad_yaml() -> None:
    with pytest.raises(WorkflowDefinitionError, match="not valid YAML"):
        workflow_definition_from_yaml_string("nodes: [broken")


def test_validate_workflow_definition_rejects_terminal_without_outcome(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
key: bad
label: Bad
schema_version: 2
nodes:
  a:
    label: A
    capability: a
    terminal: {}
edges: []
""",
        encoding="utf-8",
    )

    errors = validate_workflow_definition(path.read_text(encoding="utf-8"))

    assert any("terminal.outcome" in error for error in errors)


def test_publish_validation_reports_missing_node_code(tmp_path: Path) -> None:
    """P-0.5: a code node without resolvable code fails publish."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")

    errors = validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace["id"],
        job_db=queries,
        custom_nodes_enabled=True,
    )

    # Bare JobQueries seeds no Agent definitions and no node code: the demo's
    # agent-typed nodes miss their published Agent and its code nodes miss
    # their published code, so both error kinds are reported.
    assert any("no published node code" in error for error in errors)
    assert any("must resolve to exactly one published Agent" in error for error in errors)


def test_failed_publish_validation_preserves_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    service = WorkflowRevisionService(queries)
    active = service.publish_workspace_revision(workspace["id"], definition)

    errors = validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace["id"],
        job_db=queries,
        custom_nodes_enabled=True,
    )

    assert errors
    assert (
        queries.get_active_workflow_revision(workspace["id"], definition.key)["id"] == active["id"]
    )


def test_mid_publish_projection_failure_rolls_back_revision_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """publish 中途失败（投影写入抛错）必须连同 revision insert 一起回滚。

    #287 拆分把投影 helper 抽到 workflow_revision_projection.py，事务边界
    契约（投影与 revision insert 同事务、绝不自行提交）此前只被「publish 前
    validation 失败」的用例间接背书——那条路径根本不进事务。本用例在
    create_workflow_revision 事务内部（archive/insert 之后、投影写入时）
    注入失败，断言新 revision 行与 active 指针都不落库。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    service = WorkflowRevisionService(queries)
    first = service.publish_workspace_revision(workspace["id"], definition)

    from server.app.jobs.queries import workflow_revisions as revision_writes

    def _boom(conn: object, **_kwargs: object) -> dict:
        raise RuntimeError("projection exploded mid-transaction")

    # 事务体是 projection 模块的 create_workflow_revision_with_projection
    # （queries 侧 from-import 该名字）——patch 消费模块的绑定才拦得住
    # publish 事务的 insert 之后、投影写入之前的窗口。
    monkeypatch.setattr(revision_writes, "create_workflow_revision_with_projection", _boom)

    with pytest.raises(RuntimeError, match="projection exploded"):
        service.publish_workspace_revision(workspace["id"], definition)

    # 新 revision 行未落库：仍只有第一次发布的 1 行。
    with queries.connect() as conn:
        rows = conn.execute(
            "select count(*) as n from workflow_revisions where workspace_id = %s",
            (workspace["id"],),
        ).fetchone()
    assert rows["n"] == 1
    # active 指针未移动。
    assert (
        queries.get_active_workflow_revision(workspace["id"], definition.key)["id"] == first["id"]
    )


def test_get_active_workflow_revision_returns_definition_and_yaml(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    with authenticate_client(TestClient(app)) as client:
        response = client.post(
            "/api/workspaces",
            json={"id": "education_video_problems_generation", "name": "Studio"},
        )
        assert response.status_code == 200
        workspace_id = response.json()["workspace"]["id"]
        # v62: creation seeds nothing; publish the demo revision explicitly.
        from tests.helpers import publish_builtin_revision

        publish_builtin_revision(app.state.job_db, workspace_id)

        active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")

    assert active.status_code == 200
    payload = active.json()
    assert payload["revision"]["status"] == "active"
    assert payload["revision"]["version"] == 1
    assert payload["workflow"]["key"] == "education_video_problems_generation"
    assert payload["workflow"]["nodes"]
    assert "key: education_video_problems_generation" in payload["definition_yaml"]

    definition = workflow_definition_from_yaml_string(payload["definition_yaml"])
    assert definition.key == "education_video_problems_generation"
    assert definition.nodes
    assert definition.edges


def test_get_workflow_revision_detail_returns_definition_and_yaml(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    with authenticate_client(TestClient(app)) as client:
        response = client.post(
            "/api/workspaces",
            json={"id": "education_video_problems_generation", "name": "Studio"},
        )
        assert response.status_code == 200
        workspace_id = response.json()["workspace"]["id"]
        # v62: creation seeds nothing; publish the demo revision explicitly.
        from tests.helpers import publish_builtin_revision

        publish_builtin_revision(app.state.job_db, workspace_id)

        active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
        assert active.status_code == 200
        revision_id = active.json()["revision"]["id"]

        detail = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/{revision_id}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["revision"]["id"] == revision_id
    assert payload["revision"]["status"] == "active"
    assert payload["workflow"]["key"] == "education_video_problems_generation"
    assert payload["workflow"]["nodes"]
    assert "key: education_video_problems_generation" in payload["definition_yaml"]


def test_get_workflow_revision_detail_returns_404_for_unknown_revision(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    workspace = app.state.job_db.create_workspace(
        "Studio",
        default_workflow_key="education_video_problems_generation",
    )
    with authenticate_client(TestClient(app)) as client:
        response = client.get(f"/api/workspaces/{workspace['id']}/workflow-revisions/missing-rev")

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow revision not found"


def test_get_workflow_revision_detail_rejects_other_workspace_revision(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    with authenticate_client(TestClient(app)) as client:
        first = client.post(
            "/api/workspaces",
            json={"id": "first_ws", "name": "First"},
        )
        second = client.post(
            "/api/workspaces",
            json={"id": "second_ws", "name": "Second"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_id = first.json()["workspace"]["id"]
        second_id = second.json()["workspace"]["id"]
        # v62: creation seeds nothing; publish into the first workspace. The
        # /active lookup resolves default_workflow_key, which equals the
        # workspace id here — so the built-in definition is published with
        # its key rewritten to the id (the JobQueries-level key rewrite the
        # publish guard would demand of an HTTP draft anyway).
        from dataclasses import replace

        definition = load_builtin_definition("education_video_problems_generation")
        if definition.key != first_id:
            definition = replace(definition, key=first_id)
        WorkflowRevisionService(app.state.job_db).publish_workspace_revision(first_id, definition)
        active = client.get(f"/api/workspaces/{first_id}/workflow-revisions/active")
        assert active.status_code == 200
        first_revision_id = active.json()["revision"]["id"]

        response = client.get(f"/api/workspaces/{second_id}/workflow-revisions/{first_revision_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow revision not found"


def test_get_active_workflow_revision_returns_404_for_workspace_without_revision(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    workspace = app.state.job_db.create_workspace(
        "No Revision",
        default_workflow_key="education_video_problems_generation",
    )
    with authenticate_client(TestClient(app)) as client:
        response = client.get(f"/api/workspaces/{workspace['id']}/workflow-revisions/active")

    assert response.status_code == 404
    assert response.json()["detail"] == "No active workflow revision"


def test_definition_to_yaml_upgrades_v1_to_schema_version_2(tmp_path: Path) -> None:
    definition = load_builtin_definition("education_video_problems_generation")

    yaml_text = definition_to_yaml(definition)

    assert "schema_version: 2" in yaml_text
    parsed = workflow_definition_from_yaml_string(yaml_text)
    assert parsed.schema_version == 2
    assert parsed.edges


def _sharded_reduce_definition() -> WorkflowDefinition:
    """Demo DAG + review_questions 分片、publish_content 聚合（合法配对）。"""
    definition = load_builtin_definition("education_video_problems_generation")
    return dc_replace(
        definition,
        nodes={
            **definition.nodes,
            "review_questions": dc_replace(
                definition.nodes["review_questions"], shard=WorkflowShardSpec(count=4)
            ),
            "publish_content": dc_replace(
                definition.nodes["publish_content"],
                reduce=WorkflowReduceSpec(from_node="review_questions"),
            ),
        },
    )


def test_sharded_revision_snapshot_round_trip(tmp_path: Path) -> None:
    """Issue #458：含 shard/reduce 基线的 definition_json 能被 loader 重读。

    asdict 快照把 ``WorkflowReduceSpec.from_node`` 存成 ``from_node``，而
    loader 只认 yaml 拼写 ``from``——修复前含 reduce 的快照过
    ``workflow_definition_from_dict`` 必报 ``reduce.from is required``，
    ``GET /workflow-revisions/active`` 与 compare 基线解析臂全部失效。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    revision = WorkflowRevisionService(queries).publish_workspace_revision(
        workspace["id"], _sharded_reduce_definition()
    )

    restored = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
    assert restored.nodes["review_questions"].shard == WorkflowShardSpec(count=4)
    assert restored.nodes["publish_content"].reduce == WorkflowReduceSpec(
        from_node="review_questions"
    )


def test_definition_to_yaml_echoes_shard_and_reduce() -> None:
    """Issue #458：回显 YAML 落 shard/reduce 键且回读等价（studio 初始草稿即此回显）。

    修复前含 shard 基线的工作区一打开就有幽灵变更（compare 报 shard
    modified）、reset 清不掉，照此发布会静默删掉分片/聚合声明。
    """
    definition = _sharded_reduce_definition()

    yaml_text = definition_to_yaml(definition)

    assert "shard:" in yaml_text
    assert "reduce:" in yaml_text
    parsed = workflow_definition_from_yaml_string(yaml_text)
    assert parsed.nodes["review_questions"].shard == WorkflowShardSpec(count=4)
    assert parsed.nodes["publish_content"].reduce == WorkflowReduceSpec(
        from_node="review_questions"
    )


def test_response_payload_includes_terminal_outcome(tmp_path: Path) -> None:
    definition = load_builtin_definition("education_video_problems_generation")

    payload = workflow_definition_to_response_payload(definition)

    terminal_nodes = [node for node in payload["nodes"] if node.get("terminal")]
    assert terminal_nodes
    assert all(node["terminal"]["outcome"] for node in terminal_nodes)


def test_publish_revision_records_node_code_pins(tmp_path: Path) -> None:
    """Publish snapshots published custom code versions as node_code_pins."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws-pins", default_workflow_key="education_video_problems_generation"
    )
    codes = NodeCodeService(queries.dsn_identity)
    codes.save_draft(
        workspace["id"],
        "education_video_problems_generation",
        "intake_knowledge_points",
        "def run(job, job_dir, runtime):\n    return None\n",
        "user:u1",
    )
    codes.publish(workspace["id"], "education_video_problems_generation", "intake_knowledge_points")
    definition = load_builtin_definition("education_video_problems_generation")
    service = WorkflowRevisionService(queries)

    service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    payload = json.loads(active["definition_json"])
    pins = payload["node_code_pins"]
    assert pins["intake_knowledge_points"]["version"] == 1
    assert len(pins["intake_knowledge_points"]["code_hash"]) == 64
    assert "write_script" not in pins
    # Pins are publish-moment state, not part of the definition: the hash
    # covers the pure definition only.
    assert active["definition_hash"] == definition_hash(serialize_definition(definition))
    # The definition round-trip ignores the sibling pins key.
    workflow_definition_from_dict(payload)


def test_publish_revision_pins_workspace_factory_seed_codes(tmp_path: Path, settings) -> None:
    """Workspace factory seeds are pinned into revision publishes."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws-no-pins", default_workflow_key="education_video_problems_generation"
    )
    from server.app.services.demo_node_seed import seed_demo_workspace_node_codes

    seed_demo_workspace_node_codes(settings, workspace["id"])
    definition = load_builtin_definition("education_video_problems_generation")
    service = WorkflowRevisionService(queries)

    service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    pins = json.loads(active["definition_json"])["node_code_pins"]
    assert set(pins) == {"intake_knowledge_points", "publish_content"}
    for pin in pins.values():
        assert pin["version"] == 1
        assert len(pin["code_hash"]) == 64


def test_publish_revision_skips_pins_when_gate_disabled(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws-gated-pins", default_workflow_key="education_video_problems_generation"
    )
    codes = NodeCodeService(queries.dsn_identity)
    codes.save_draft(
        workspace["id"],
        "education_video_problems_generation",
        "intake_knowledge_points",
        "def run(job, job_dir, runtime):\n    return None\n",
        "user:u1",
    )
    codes.publish(workspace["id"], "education_video_problems_generation", "intake_knowledge_points")
    definition = load_builtin_definition("education_video_problems_generation")
    service = WorkflowRevisionService(queries, custom_nodes_enabled=False)

    service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    assert "node_code_pins" not in json.loads(active["definition_json"])


def test_runtime_only_update_preserves_node_code_pins(tmp_path: Path) -> None:
    """In-place (runtime settings only) revision updates keep node_code_pins."""
    from server.app.workflows.schema import WorkflowNodeExecution

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws-pins-keep", default_workflow_key="education_video_problems_generation"
    )
    codes = NodeCodeService(queries.dsn_identity)
    codes.save_draft(
        workspace["id"],
        "education_video_problems_generation",
        "intake_knowledge_points",
        "def run(job, job_dir, runtime):\n    return None\n",
        "user:u1",
    )
    codes.publish(workspace["id"], "education_video_problems_generation", "intake_knowledge_points")
    definition = load_builtin_definition("education_video_problems_generation")
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(workspace["id"], definition)

    # Runtime-only change: same structure, different execution settings.
    node = definition.nodes["write_script"]
    updated = dc_replace(
        definition,
        nodes={
            **definition.nodes,
            "write_script": dc_replace(
                node, execution=WorkflowNodeExecution(provider="deepseek", model="m2")
            ),
        },
    )
    service.save_workspace_revision(workspace["id"], updated)

    active = service.get_active(workspace["id"], definition.key)
    payload = json.loads(active["definition_json"])
    assert payload["node_code_pins"]["intake_knowledge_points"]["version"] == 1
    # The runtime change did land, and no new revision was created.
    assert payload["nodes"]["write_script"]["execution"]["model"] == "m2"
    assert active["version"] == 1


def test_publish_validation_skips_approval_gates(tmp_path: Path) -> None:
    """Approval gates never dispatch (EXEC-APPROVAL-001): the publish gate
    must not demand Agents or node code for them."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws-approval", default_workflow_key="gated")
    definition = workflow_definition_from_mapping(
        {
            "key": "gated",
            "label": "Gated",
            "schema_version": 2,
            "nodes": {
                "entry": {"type": "start", "label": "入口"},
                "write": {"label": "写稿", "capability": "write_script"},
                "gate": {
                    "type": "approval",
                    "label": "审批",
                    "config": {"rework_target": "write"},
                },
            },
            "edges": [
                {"from": "entry", "to": "write"},
                {"from": "write", "to": "gate"},
            ],
        }
    )

    errors = validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace["id"],
        job_db=queries,
        custom_nodes_enabled=True,
    )

    # Only the code node is reported; the gate needs neither Agents nor code.
    assert all("gated.gate" not in error for error in errors)
    assert any("gated.write" in error for error in errors)


def _publish_with_node_schema(
    queries: JobQueries,
    workspace_id: str,
    properties: dict,
) -> WorkflowDefinition:
    """Publish the demo DAG with intake_knowledge_points declaring a schema."""
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


def test_publish_prunes_stale_override_keys(tmp_path: Path) -> None:
    """#418 二轮复审 P2-1: publish must prune workspace overrides the new
    revision no longer accepts. Without the prune, a renamed/removed schema
    property leaves stale keys that fail every later intake at the whitelist
    validation, and the override card's PATCH-everything save 400s."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-ws", default_workflow_key="education_video_problems_generation"
    )

    _publish_with_node_schema(
        queries,
        workspace["id"],
        {"old_key": {"type": "integer", "default": 1}, "kept": {"type": "string"}},
    )
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {
                "intake_knowledge_points": {"old_key": 5, "kept": "v"}
            }
        },
    )

    # v2 renames old_key → new_key (the studio rename path).
    v2 = _publish_with_node_schema(
        queries,
        workspace["id"],
        {"new_key": {"type": "integer", "default": 2}, "kept": {"type": "string"}},
    )

    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {"kept": "v"}
    # And the frozen intake resolution that previously raised now succeeds.
    resolved = resolve_workflow_node_configs(v2, {}, queries.get_workspace(workspace["id"]))
    assert resolved["intake_knowledge_points"]["new_key"] == 2


def test_publish_prunes_type_mismatched_override_values(tmp_path: Path) -> None:
    """A type flip (integer → string) leaves the old value behind: the intake
    type check raises on it exactly like an unknown key, so publish prunes it
    too (#418 二轮复审 P2-1)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-type-ws", default_workflow_key="education_video_problems_generation"
    )

    _publish_with_node_schema(queries, workspace["id"], {"count": {"type": "integer"}})
    queries.update_workspace(
        workspace["id"],
        node_config={
            "education_video_problems_generation": {"intake_knowledge_points": {"count": 42}}
        },
    )

    v2 = _publish_with_node_schema(queries, workspace["id"], {"count": {"type": "string"}})

    # The node's whole override entry is gone (same shape the settings PATCH
    # leaves behind when a node's values empty out).
    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert "intake_knowledge_points" not in stored["education_video_problems_generation"]
    resolved = resolve_workflow_node_configs(v2, {}, queries.get_workspace(workspace["id"]))
    assert "count" not in resolved["intake_knowledge_points"]


def test_publish_keeps_valid_and_secret_overrides(tmp_path: Path) -> None:
    """Legitimate overrides survive the prune, secret_ref markers included."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-keep-ws", default_workflow_key="education_video_problems_generation"
    )

    schema = {"count": {"type": "integer"}, "token": {"type": "string", "secret": True}}
    _publish_with_node_schema(queries, workspace["id"], schema)
    overrides = {
        "education_video_problems_generation": {
            "intake_knowledge_points": {
                "count": 7,
                "token": {"secret_ref": "node:wf:intake_knowledge_points:token"},
            }
        }
    }
    queries.update_workspace(workspace["id"], node_config=overrides)

    _publish_with_node_schema(queries, workspace["id"], schema)

    stored = queries.get_workspace(workspace["id"])["node_config"]
    assert stored["education_video_problems_generation"]["intake_knowledge_points"] == {
        "count": 7,
        "token": {"secret_ref": "node:wf:intake_knowledge_points:token"},
    }


def test_publish_without_overrides_is_a_noop_prune(tmp_path: Path) -> None:
    """No stored overrides → publish must not write the workspace row."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "prune-empty-ws", default_workflow_key="education_video_problems_generation"
    )

    _publish_with_node_schema(queries, workspace["id"], {"count": {"type": "integer"}})

    assert queries.get_workspace(workspace["id"])["node_config"] == {}
