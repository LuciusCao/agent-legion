"""``upgrade_job_workflow_inherit`` mutation 层测试（issue #645）。

直接针对 mutation SQL（经 write_transaction 连接），验证 clean 模式行为
与既有全量重置一致、inherit 模式只重置变更子图且继承行原样保留。
"""

from __future__ import annotations

from pathlib import Path

from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.jobs.workflow_upgrade_mutation import (
    upgrade_job_workflow,
    upgrade_job_workflow_inherit,
)
from tests.postgres_support import TEST_DATABASE_URL


def _setup(tmp_path: Path):
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wsmut", default_workflow_key="wfmut")
    job = queries.create_job(
        workflow_key="wfmut",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["a", "b", "c"],
        workspace_id=workspace["id"],
    )
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")
    return queries, job


def _mutation_conn(queries: JobQueries):
    return write_transaction(queries.dsn_identity)


def test_clean_mode_resets_all_nodes_to_pending(tmp_path: Path) -> None:
    queries, job = _setup(tmp_path)

    with _mutation_conn(queries) as conn:
        stats = upgrade_job_workflow_inherit(
            conn,
            job["id"],
            workflow_revision_id="rev-2",
            workflow_version=2,
            workflow_definition_hash="hash-2",
            workflow_definition_snapshot_json='{"key": "wfmut"}',
            node_keys=["a", "b", "c"],
            frozen_config_json=None,
        )

    nodes = {n["node_key"]: n["status"] for n in queries.list_job_nodes(job["id"])}
    assert stats == {"kept": 0, "rerun": 3}
    assert set(nodes.values()) == {"pending"}
    updated = queries.get_job(job["id"])
    assert updated["workflow_revision_id"] == "rev-2"
    assert updated["status"] == "queued"


def test_legacy_clean_signature_matches_clean_mode(tmp_path: Path) -> None:
    queries, job = _setup(tmp_path)

    with _mutation_conn(queries) as conn:
        upgrade_job_workflow(
            conn,
            job["id"],
            workflow_revision_id="rev-3",
            workflow_version=3,
            workflow_definition_hash="hash-3",
            workflow_definition_snapshot_json='{"key": "wfmut"}',
            node_keys=["a", "b"],
        )

    nodes = {n["node_key"]: n["status"] for n in queries.list_job_nodes(job["id"])}
    # 旧签名：不传 inherit_nodes → 全 pending；不在 node_keys 的 c 连行删除。
    assert nodes == {"a": "pending", "b": "pending"}


def test_inherit_mode_keeps_completed_rows_verbatim(tmp_path: Path) -> None:
    queries, job = _setup(tmp_path)
    before = {n["node_key"]: n for n in queries.list_job_nodes(job["id"])}
    finished_at = before["a"]["finished_at"]

    with _mutation_conn(queries) as conn:
        stats = upgrade_job_workflow_inherit(
            conn,
            job["id"],
            workflow_revision_id="rev-4",
            workflow_version=4,
            workflow_definition_hash="hash-4",
            workflow_definition_snapshot_json='{"key": "wfmut"}',
            node_keys=["a", "b", "c"],
            frozen_config_json=None,
            inherit_nodes=frozenset({"a"}),
        )

    nodes = {n["node_key"]: n for n in queries.list_job_nodes(job["id"])}
    assert stats == {"kept": 1, "rerun": 2}
    assert nodes["a"]["status"] == "completed"
    assert nodes["b"]["status"] == "pending"
    assert nodes["c"]["status"] == "pending"
    # 继承行的时间戳原样保留（产物继承的可见凭据）。
    assert nodes["a"]["finished_at"] == finished_at


def test_inherit_mode_uncompleted_candidate_resets(tmp_path: Path) -> None:
    queries, job = _setup(tmp_path)
    queries.update_job_node(job["id"], "a", status="failed")

    with _mutation_conn(queries) as conn:
        stats = upgrade_job_workflow_inherit(
            conn,
            job["id"],
            workflow_revision_id="rev-5",
            workflow_version=5,
            workflow_definition_hash="hash-5",
            workflow_definition_snapshot_json='{"key": "wfmut"}',
            node_keys=["a", "b", "c"],
            frozen_config_json=None,
            inherit_nodes=frozenset({"a", "b"}),
        )

    nodes = {n["node_key"]: n["status"] for n in queries.list_job_nodes(job["id"])}
    # 未完成的继承候选（a=failed）没有产物可继承 → 重置 pending。
    assert stats == {"kept": 1, "rerun": 2}
    assert nodes == {"a": "pending", "b": "completed", "c": "pending"}


def test_inherit_mode_drops_removed_nodes_and_clears_run_dirs(tmp_path: Path) -> None:
    queries, job = _setup(tmp_path)
    with _mutation_conn(queries) as conn:
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, run_dir)
            values (%s, 'b', 'done', 'old/dir')
            """,
            (job["id"],),
        )

    with _mutation_conn(queries) as conn:
        stats = upgrade_job_workflow_inherit(
            conn,
            job["id"],
            workflow_revision_id="rev-6",
            workflow_version=6,
            workflow_definition_hash="hash-6",
            workflow_definition_snapshot_json='{"key": "wfmut"}',
            node_keys=["a", "b"],
            frozen_config_json=None,
            inherit_nodes=frozenset({"a"}),
        )

    nodes = {n["node_key"] for n in queries.list_job_nodes(job["id"])}
    assert stats == {"kept": 1, "rerun": 1}
    # 新定义之外的节点（c）行删除；保留的节点里只有重置的 b。
    assert nodes == {"a", "b"}
    # 重置节点 b 的 run_dir 清空（历史日志不再指向将被覆盖的目录）。
    run_b = next((r for r in queries.list_node_runs(job["id"]) if r["node_key"] == "b"), None)
    assert run_b is not None
    assert run_b["run_dir"] == ""
