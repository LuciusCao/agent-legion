"""#827：upgrade-workflow（inherit）+ 节点 rerun 组合后清单行悬挂回归。

权威对象键 ``jobs/<ws>/<job>/<name>`` 只按名字寻址，``job_artifacts`` 清单
行却按 ``(node_key, name)`` 一节点一行——同名多行共享同一个物理对象槽，
对象里只有「最后一个写者」的字节。修复前三条退役路径（rerun /
run-to / upgrade inherit）按 ``node_key ∈ 重置面`` 删行：同名、但
node_key 在重置面之外的行（被删节点的遗留行等）活下来，提交后的对象
清理把它们算作「仍被引用」而跳过删除（线上日志的 ``skipped N
re-registered object(s)``）。被删的恰是最新写者时，幸存的旧行成了
hydration 的「最新」行，而对象里装的是被删写者的字节 → content-hash
不符（或对象已被换形删除 → NoSuchKey）→ 恢复永远不全 → job 永久 defer。

自然复现链（本文件第一例，issue 时间线同构）：

1. v1：``p`` 与 ``d`` 都声明纯输出 ``shared.json``（d 后写，对象是 d 的字节），
   ``k`` 消费它；
2. inherit 升级到 v2（删除 d）：p/k 未变被继承，d 的同名行因保留节点
   声明面（A3 / keep_io）被刻意留下——成为不在任何节点声明面里的遗留行；
3. rerun p → p 重跑产出新字节（最新行 = p）；
4. rerun 入口 p（全量重跑）：按节点删行只删 (p, shared.json)，遗留
   (d, shared.json) 成为「最新」行，对象却是 p 的新字节 → 悬挂。

修复：退役按名字删除该 job 的全部同名行（对象槽按名字，行与对象同生共死）。
"""

from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from server.app.db.connection import connect_database
from server.app.db.transaction import write_transaction
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import mark_nodes_for_rerun
from server.app.jobs.run_to_mutation import apply_run_to
from server.app.jobs.workflow_upgrade_artifact_rows import delete_reset_artifact_rows
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_staged_cleanup import delete_rerun_artifact_objects
from server.app.settings import load_settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.fakes.storage import FakeObjectStorage
from tests.helpers.job_workflow_upgrade import (
    make_upgrade_service,
    seed_impl_identity,
    seed_wfchain_job,
    setup_wfchain_env,
)
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres

SHARED = "shared.json"


def _definition(*, with_d: bool) -> WorkflowDefinition:
    nodes = {
        "p": WorkflowNode(key="p", label="P", capability="cap_p", outputs=[SHARED]),
        "k": WorkflowNode(
            key="k",
            label="K",
            capability="cap_k",
            after=["p"],
            config_schema={},
            inputs=[SHARED],
            outputs=["k_out.json"],
        ),
    }
    if with_d:
        nodes["d"] = WorkflowNode(
            key="d",
            label="D",
            capability="cap_d",
            after=["p"],
            config_schema={},
            outputs=[SHARED],
        )
    return WorkflowDefinition(key="wfchain", label="Wf", intake=WorkflowIntake(), nodes=nodes)


def _produce(
    store: JobArtifactObjectStore, job: dict, jobs_dir: Path, node_key: str, name: str, body: str
) -> None:
    """节点产出一次：本地写 + 上传登记（与完成钩子同一 upload 入口）。"""
    job_dir = resolve_job_dir(job, jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / name).write_text(body)
    store.upload(
        workspace_id=str(job["workspace_id"]),
        job_id=str(job["id"]),
        node_key=node_key,
        name=name,
        local_path=job_dir / name,
    )
    # 记录 uploaded_at 的严格先后（同一事务时间戳下并列会让决胜序退到 node_key）。
    with closing(connect_database(TEST_DATABASE_URL)) as conn, conn:
        conn.execute("select pg_sleep(0.01)")


def _dangling_latest_rows(store: JobArtifactObjectStore, job_id: str) -> dict[str, str]:
    """hydration 口径（同名取最新行）下不能还原的名字 → 原因。"""
    latest = {str(row["name"]): row for row in store.rows_for_job(job_id)}
    storage = store.storage
    problems: dict[str, str] = {}
    for name, row in latest.items():
        payload = storage.objects.get(str(row["storage_key"]))  # type: ignore[union-attr]
        if payload is None:
            problems[name] = "object_missing"
        elif hashlib.sha256(payload).hexdigest() != row["content_hash"]:
            problems[name] = "hash_mismatch"
    return problems


def test_inherit_upgrade_then_reruns_leave_no_dangling_manifest_row(tmp_path: Path) -> None:
    """issue 时间线同构：inherit 升级（删节点）→ rerun → rerun 入口 → 无悬挂行。"""
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, _definition(with_d=True))
    current = revisions.publish_workspace_revision(workspace["id"], _definition(with_d=False))
    job_id = seed_wfchain_job(queries, workspace, original, ["p", "d", "k"])
    seed_impl_identity(queries, workspace, job_id, ["p", "k"])
    for key in ("p", "d", "k"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job = queries.get_job(job_id)
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    _produce(store, job, queries.jobs_dir, "p", SHARED, "p-v1")
    _produce(store, job, queries.jobs_dir, "d", SHARED, "d-v1")  # d 后写：对象槽里是 d 的字节
    _produce(store, job, queries.jobs_dir, "k", "k_out.json", "k-v1")

    upgrade = make_upgrade_service(tmp_path, queries, object_store=store)
    result = upgrade.upgrade(workspace["id"], job_id, mode="inherit")
    assert result["status"] == "succeeded"
    # p/k 未变被继承；d 的同名行被保留节点声明面挡在退役面之外（遗留行）。
    assert result["kept_node_count"] == 2
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]
    assert ("d", SHARED) in {(r["node_key"], r["name"]) for r in store.rows_for_job(job_id)}
    assert _dangling_latest_rows(store, job_id) == {}

    settings = load_settings(data_dir=tmp_path)
    assert settings.jobs_dir == queries.jobs_dir
    rerun = JobRerunService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        settings,
        JobArtifactMutationService(queries.jobs_dir),
        object_store=store,
    )
    # rerun p（k 随闭包 stale）→ p 重跑完成，产出新字节。
    rerun.rerun(workspace["id"], job_id, "p")
    _produce(store, job, queries.jobs_dir, "p", SHARED, "p-v2")
    queries.update_job_node(job_id, "p", status="completed")
    assert _dangling_latest_rows(store, job_id) == {}

    # rerun 入口（全量重跑）：shared.json 的全部行与对象必须同生共死。
    rerun.rerun(workspace["id"], job_id, "p")

    assert _dangling_latest_rows(store, job_id) == {}
    assert SHARED not in {str(row["name"]) for row in store.rows_for_job(job_id)}


# ---------------------------------------------------------------------------
# 三条退役 mutation 的直接钉子：遗留同名行（node_key 不在重置面）一并退役
# ---------------------------------------------------------------------------


def _seed_rows(queries: JobQueries, job_id: str, rows: list[tuple[str, str, str]]) -> None:
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key, name, storage_key in rows:
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, %s, %s, 1, 'h')
                """,
                (job_id, node_key, name, storage_key),
            )


def _chain_job(queries: JobQueries, workspace: dict) -> str:
    job = queries.create_job(
        workflow_key="wfchain",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["p", "k"],
        workspace_id=workspace["id"],
    )
    return str(job["id"])


def _names(queries: JobQueries, job_id: str) -> set[tuple[str, str]]:
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        rows = conn.execute(
            "select node_key, name from job_artifacts where job_id=%s", (job_id,)
        ).fetchall()
    return {(str(row["node_key"]), str(row["name"])) for row in rows}


@pytest.mark.parametrize("path", ["rerun", "run_to", "upgrade"])
def test_retire_deletes_same_name_rows_outside_reset_face(tmp_path: Path, path: str) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wfchain", default_workflow_key="wfchain")
    job_id = _chain_job(queries, workspace)
    key = f"jobs/{workspace['id']}/{job_id}/{SHARED}"
    # (gone, shared.json)：已不在定义里的遗留行；(k, k_out.json)：无关名不受波及。
    _seed_rows(
        queries,
        job_id,
        [("p", SHARED, key), ("gone", SHARED, key + ".gz"), ("k", "k_out.json", "kk")],
    )
    with write_transaction(TEST_DATABASE_URL) as conn:
        if path == "rerun":
            deleted = mark_nodes_for_rerun(
                conn, job_id, ["p"], {"p": []}, staged_artifact_names={SHARED}
            )
        elif path == "run_to":
            deleted = apply_run_to(
                conn,
                job_id,
                "p",
                frozenset({"p"}),
                reset_nodes=["p"],
                staged_artifact_names={SHARED},
            )
        else:
            deleted = delete_reset_artifact_rows(conn, job_id, ["p"], {SHARED})

    assert _names(queries, job_id) == {("k", "k_out.json")}
    assert {(row["node_key"], row["storage_key"]) for row in deleted} == {
        ("p", key),
        ("gone", key + ".gz"),
    }
    # 提交后对象清理：两种形态的对象都不再被任何行引用 → 都删除（同生共死）。
    storage = FakeObjectStorage(objects={key: b"x", key + ".gz": b"y", "kk": b"z"})
    store: Any = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    delete_rerun_artifact_objects(store, deleted, job_id, path)
    assert set(storage.objects) == {"kk"}
