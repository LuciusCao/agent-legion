"""issue #759 复审 P1/P1-A：输入保护计划的跨名证据与 fail-closed 语义。

「保证先行」判定里经由的隐式消费边所跨名字必须会在本次清理中三面缺席
（本地文件/清单行/权威对象）——经 RMW 名（不暂存，旧文件存活）的隐式
边不构成因果序。#759 复审 P1-A 起判定改由
``job_workflow_upgrade_protection.input_protection_plan`` 承担（reset-aware
liveness/freshness 双判定，表驱动纯函数用例见
``test_job_workflow_upgrade_protection.py``），本文件钉住三个服务级形态：

- 跨名互证反例（双 RMW 互借对方隐式边 + 无显式入边的纯 consumer）→
  两方向均不可证明（留：纯 consumer 经存活旧文件吃旧字节；删：RMW 面
  不暂存文件同样吃到）⇒ upgrade fail closed（skipped），不是「保守保留」
  ——保留在这里同样是静默错误，没有保守方向可选；
- 链式 RMW（纯 consumer c 对 x 无排序证据）⇒ 同样 fail closed；
- 显式边链的「producer 真先行」对照 ⇒ 仍放行清理（freshness 可证）。

与 ``test_job_workflow_upgrade_759_boundaries.py`` 同源（800 行纪律拆出
的姊妹文件），helper 形状保持一致。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL


def _node(
    key: str,
    capability: str | None = None,
    *,
    after: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> WorkflowNode:
    return WorkflowNode(
        key=key,
        label=key.upper(),
        capability=capability or f"cap_{key}",
        after=after or [],
        inputs=inputs or [],
        outputs=outputs or [],
    )


def _definition(nodes: dict[str, WorkflowNode]) -> WorkflowDefinition:
    return WorkflowDefinition(key="wfchain", label="Wf Chain", intake=WorkflowIntake(), nodes=nodes)


def _setup(tmp_path: Path, definition: WorkflowDefinition):
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wschain", default_workflow_key="wfchain")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    return queries, workspace, revisions, original


def _seed_job(queries, workspace, original, node_keys) -> str:
    job = queries.create_job(
        workflow_key="wfchain",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=node_keys,
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    return str(job["id"])


def _make_service(tmp_path: Path, queries: JobQueries) -> JobWorkflowUpgradeService:
    return JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )


def _job_dir(queries: JobQueries, job_id: str) -> Path:
    from server.app.storage_paths import resolve_job_dir

    return resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)


def _insert_manifest_row(queries: JobQueries, job_id: str, node_key: str, name: str) -> None:
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, %s, %s, %s, 1, 'hash')
            """,
            (job_id, node_key, name, f"jobs/wschain/{job_id}/{name}"),
        )


def _manifest_names(queries: JobQueries, job_id: str, node_keys: set[str]) -> set[tuple[str, str]]:
    return queries.job_artifact_manifest_names_for_nodes(job_id, node_keys)


def _seed_clean_job(
    tmp_path: Path,
    old: WorkflowDefinition,
    new: WorkflowDefinition,
    node_keys: list[str],
    manifest_rows: dict[str, list[str]],
) -> tuple[JobQueries, str, Path, dict[str, Any]]:
    """播种旧 revision 的 completed job（全部节点 completed + 清单行 +
    本地文件），发布新 revision，执行 clean 升级并返回结果。"""
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, node_keys)
    for key in node_keys:
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    for node_key, names in manifest_rows.items():
        for name in names:
            (job_dir / name).write_text(f"old-{name}")
            _insert_manifest_row(queries, job_id, node_key, name)
    result = _make_service(tmp_path, queries).upgrade(workspace["id"], job_id, mode="clean")
    return queries, job_id, job_dir, result


def test_cross_name_mutual_proof_fails_closed(tmp_path: Path) -> None:
    """复审 P1 互证反例（P1-A 起 fail closed）：双 RMW 互借对方隐式边。

    p 纯产 x/w；q 是 x 的 RMW（p→q）；m 是 w 的 RMW（p→m）；c 声明
    inputs [x, w]、无显式入边。判 x 时 c 只能经 m→c（w 的隐式边）可达、
    判 w 时 c 只能经 q→c（x 的隐式边）可达——两个名互相借对方的边
    「证明」对方缺席，但 RMW 名不暂存、旧文件存活，经由它们的隐式边不
    构成因果序。旧语义把两名留在保护集（保守保留），但保留同样是静默
    错误：c 无显式入边、旧 x/w 文件在场 ⇒ ready gate 立刻放行，c 在
    p/q/m 重跑前消费旧 revision 字节。两方向均不可证明 ⇒ upgrade 必须
    fail closed（skipped/protection_unprovable），零副作用。
    """
    old = _definition(
        {
            "p": _node("p", outputs=["x.json", "w.json"]),
            "q": _node("q", after=["p"], inputs=["x.json"], outputs=["x.json"]),
            "m": _node("m", after=["p"], inputs=["w.json"], outputs=["w.json"]),
        }
    )
    new = _definition(
        {
            "p": _node("p", outputs=["x.json", "w.json"]),
            "q": _node("q", "cap_q_new", after=["p"], inputs=["x.json"], outputs=["x.json"]),
            "m": _node("m", "cap_m_new", after=["p"], inputs=["w.json"], outputs=["w.json"]),
            "c": _node("c", inputs=["x.json", "w.json"]),
        }
    )
    queries, job_id, job_dir, result = _seed_clean_job(
        tmp_path,
        old,
        new,
        ["p", "q", "m"],
        {"q": ["x.json"], "m": ["w.json"]},
    )

    assert result["status"] == "skipped"
    assert result["reason_code"] == "protection_unprovable"
    assert "x.json" in result["message"] and "w.json" in result["message"]
    # 零副作用：节点状态、清单行、本地文件全部保持升级前原样。
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert set(statuses.values()) == {"completed"}
    assert ("q", "x.json") in _manifest_names(queries, job_id, {"q"})
    assert ("m", "w.json") in _manifest_names(queries, job_id, {"m"})
    assert (job_dir / "x.json").read_text() == "old-x.json"
    assert (job_dir / "w.json").read_text() == "old-w.json"
    assert not (job_dir / ".staged").exists()


def test_explicit_chain_producer_still_unprotects_startup_row(tmp_path: Path) -> None:
    """放行对照：p→q→c 显式边链的「producer 真先行」不依赖任何隐式边。

    p 纯产 x，q 是 x 的 RMW（p→q），c 消费 x（q→c）——c 被显式边硬阻塞
    到链重跑完成，x 的全部 consumer 都有排序证据（freshness 可证）⇒
    放行清理；fail-closed 收紧不得误伤这类真先行场景。
    """
    old = _definition(
        {
            "p": _node("p", outputs=["x.json"]),
            "q": _node("q", after=["p"], inputs=["x.json"], outputs=["x.json"]),
        }
    )
    new = _definition(
        {
            "p": _node("p", outputs=["x.json"]),
            "q": _node("q", "cap_q_new", after=["p"], inputs=["x.json"], outputs=["x.json"]),
            "c": _node("c", after=["q"], inputs=["x.json"]),
        }
    )
    queries, job_id, job_dir, result = _seed_clean_job(
        tmp_path,
        old,
        new,
        ["p", "q"],
        {"q": ["x.json"]},
    )

    assert result["status"] == "succeeded"
    # x 失去保护：清单行清理（p 重跑重新产出 x 后 q/c 才解锁）。
    assert _manifest_names(queries, job_id, {"p", "q"}) == set()
    # RMW 名的本地文件不进暂存面（#114），清理的是清单行/权威对象。
    assert (job_dir / "x.json").read_text() == "old-x.json"


def test_chained_rmw_without_pure_consumer_evidence_fails_closed(tmp_path: Path) -> None:
    """链式 RMW 反例（P1-A 起 fail closed）：c 对 x 没有任何排序证据。

    px 纯产 x、p 纯产 w（互无显式边）；m 是 w 的 RMW（p→m、px→m）；q 是
    x 的 RMW（m→q）；c 消费 [x, w]，显式 after=[p]。c 对 x 的唯一可达
    路径要借 w 的隐式边 m→c——但 w 是 RMW 名（不暂存、旧文件存活），该
    边不构成「c 在 m 之后」的因果序；且 c 只显式等 p，px 尚未重跑时旧
    x 文件已在场 ⇒ c 可能消费旧 revision 的 x。w 自身的全部 consumer
    （m 经 p→m、c 经 p→c 显式边）都有排序证据 ⇒ w 可清；x 的纯 consumer
    c 无证据 ⇒ 整体 fail closed（不留「半清理」的猜测态）。
    """
    old = _definition(
        {
            "px": _node("px", outputs=["x.json"]),
            "p": _node("p", outputs=["w.json"]),
            "m": _node("m", after=["p", "px"], inputs=["w.json"], outputs=["w.json"]),
            "q": _node("q", after=["m"], inputs=["x.json"], outputs=["x.json"]),
        }
    )
    new = _definition(
        {
            "px": _node("px", outputs=["x.json"]),
            "p": _node("p", outputs=["w.json"]),
            "m": _node("m", "cap_m_new", after=["p", "px"], inputs=["w.json"], outputs=["w.json"]),
            "q": _node("q", "cap_q_new", after=["m"], inputs=["x.json"], outputs=["x.json"]),
            "c": _node("c", after=["p"], inputs=["x.json", "w.json"]),
        }
    )
    queries, job_id, job_dir, result = _seed_clean_job(
        tmp_path,
        old,
        new,
        ["px", "p", "m", "q"],
        {"q": ["x.json"], "m": ["w.json"]},
    )

    assert result["status"] == "skipped"
    assert result["reason_code"] == "protection_unprovable"
    assert "x.json" in result["message"]
    # 零副作用：w 虽单独可证 clean，unprovable 非空 ⇒ 整体不应用。
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert set(statuses.values()) == {"completed"}
    assert ("q", "x.json") in _manifest_names(queries, job_id, {"q"})
    assert ("m", "w.json") in _manifest_names(queries, job_id, {"m"})
    assert (job_dir / "x.json").read_text() == "old-x.json"
    assert (job_dir / "w.json").read_text() == "old-w.json"
    assert not (job_dir / ".staged").exists()
