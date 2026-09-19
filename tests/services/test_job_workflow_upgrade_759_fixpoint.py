"""issue #759 复审 P1：``unprotected_input_names`` 的名集合不动点语义。

「保证先行」判定里经由的隐式消费边所跨名字必须会在本次清理中缺席：
- 跨名互证反例（双 RMW 互借对方隐式边）→ 两个名都保留保护；
- 显式边链的「producer 真先行」对照 → 仍放行清理；
- 链式 RMW（X 的 RMW 依赖 W 的 RMW）→ W 先落地、X 第二轮经 W 的隐式
  边放行，钉住多轮迭代表达力（单轮实现会把 X 留在保护集）。

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
    本地文件），发布新 revision，返回 clean 升级前的现场。"""
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
    assert result["status"] == "succeeded"
    return queries, job_id, job_dir, result


def test_cross_name_mutual_proof_keeps_both_rmw_startup_rows(tmp_path: Path) -> None:
    """复审 P1 互证反例：双 RMW 互借对方隐式边不得完成「保证先行」证明。

    p 纯产 x/w；q 是 x 的 RMW（p→q）；m 是 w 的 RMW（p→m）；c 声明
    inputs [x, w]、无显式入边。旧判定：判 x 时 c 经 m→c（w 的隐式边）
    可达、判 w 时 c 经 q→c（x 的隐式边）可达——两个名互相借对方的边
    「证明」对方缺席，但 RMW 名不暂存、旧文件存活，经由它们的隐式边不
    构成因果序。最小不动点下双方都无不依赖对方的证据链 → 都保留保护
    （清单行是 RMW 首跑的启动输入/hydration 回填来源，#114 语义）。
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
    queries, job_id, job_dir, _ = _seed_clean_job(
        tmp_path,
        old,
        new,
        ["p", "q", "m"],
        {"q": ["x.json"], "m": ["w.json"]},
    )

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert set(statuses.values()) == {"pending"}
    # x 与 w 都保留保护：清单行不删（RMW 名的本地文件本就不进暂存面）。
    assert ("q", "x.json") in _manifest_names(queries, job_id, {"q"})
    assert ("m", "w.json") in _manifest_names(queries, job_id, {"m"})
    assert (job_dir / "x.json").read_text() == "old-x.json"
    assert (job_dir / "w.json").read_text() == "old-w.json"


def test_explicit_chain_producer_still_unprotects_startup_row(tmp_path: Path) -> None:
    """放行对照：p→q→c 显式边链的「producer 真先行」不依赖任何隐式边。

    p 纯产 x，q 是 x 的 RMW（p→q），c 消费 x（q→c）——c 被显式边硬阻塞
    到链重跑完成，x 的清理证据在第一轮（空缺席集）就成立，不动点收紧
    不得误伤这类真先行场景。
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
    queries, job_id, job_dir, _ = _seed_clean_job(
        tmp_path,
        old,
        new,
        ["p", "q"],
        {"q": ["x.json"]},
    )

    # x 失去保护：清单行清理（p 重跑重新产出 x 后 q/c 才解锁）。
    assert _manifest_names(queries, job_id, {"p", "q"}) == set()
    # RMW 名的本地文件不进暂存面（#114），清理的是清单行/权威对象。
    assert (job_dir / "x.json").read_text() == "old-x.json"


def test_chained_rmw_unprotects_second_round_via_proven_absent_name(tmp_path: Path) -> None:
    """链式 RMW：x 的 RMW（q）依赖 w 的 RMW（m），x 的证明第二轮才成立。

    px 纯产 x、p 纯产 w（互无显式边）；m 是 w 的 RMW（p→m、px→m）；q 是
    x 的 RMW（m→q）；c 消费 [x, w]，显式 after=[p]。w 的第一轮证明走纯
    显式边（m、c 都是 p 的显式下游）→ w 缺席落地；x 的证明必须借 w 的
    隐式边 m→c（c 无 px 侧显式路径）→ 第二轮才成立。单轮实现会把 x 留
    在保护集（突变锚点）；互证反例里的双向借边则永远不落地。
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
    queries, job_id, _, _ = _seed_clean_job(
        tmp_path,
        old,
        new,
        ["px", "p", "m", "q"],
        {"q": ["x.json"], "m": ["w.json"]},
    )

    # w 第一轮、x 第二轮相继失去保护：两个名的清单行都清理。
    assert _manifest_names(queries, job_id, {"px", "p", "m", "q"}) == set()
