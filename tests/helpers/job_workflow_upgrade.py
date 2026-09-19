"""Shared seeding helpers for the inherit-mode upgrade test family (#645).

The sibling test modules under ``tests/services/`` (inherit / codex3 / codex4)
must not import each other (boundary: test modules are rename-isolated —
``test_test_modules_do_not_import_each_other``), so the implementation-identity
seeding scaffolding lives here instead, next to ``tests.helpers.seed``.
"""

from __future__ import annotations

import json
import uuid
from contextlib import closing

from server.app.db.connection import connect_database
from server.app.jobs import JobQueries


def seed_done_execution(
    queries: JobQueries,
    workspace_id: str,
    job_id: str,
    node_key: str,
    *,
    kind: str,
    impl_hash: str,
    skill: str = "",
    skill_version: str = "",
    skill_commit: str = "",
) -> None:
    """播种该节点的一次完成执行：node_run(completed) + done 请求行。

    请求行携带执行时实现身份（``agent_definition_hash``：agent 行是
    Agent 定义哈希、code 行是 code 文本 sha256），与真实 dispatch 链
    （``CodeDispatchService.enqueue`` / ``AgentDispatchService.enqueue``）
    的落库形状一致。skill 三件（codex 五轮 P1-A）镜像 dispatch 的
    ``SkillCheckout.manifest_pins()``：请求行 manifest 携带完整
    ``skill_commit``（mark_done trim 保留该键），node_runs 行携带
    ``skill_version``（``ref@commit12``，v75 列）；默认空串 = 无 skill
    记录（既有用例零改动）。
    """
    run = queries.start_node_run(
        job_id, node_key, ["pi"], "", skill_version=skill_version, skill=skill
    )
    assert run is not None
    queries.finish_node_run(int(run["id"]), "completed", 0, "")
    execution_id = str(uuid.uuid4())
    manifest = {"kind": kind, "node_key": node_key}
    if skill:
        manifest.update(
            {"skill": skill, "skill_version": skill_version, "skill_commit": skill_commit}
        )
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, node_key, kind, agent_id,
              agent_definition_hash, node_concurrency_limit, state,
              queued_at, claimed_at, finished_at, node_run_id, manifest_json)
            values (%s, %s, %s, %s, %s, %s, %s, 1, 'done',
                    current_timestamp, current_timestamp, current_timestamp, %s, %s)
            """,
            (
                execution_id,
                workspace_id,
                job_id,
                node_key,
                kind,
                f"cap_{node_key}",
                impl_hash,
                int(run["id"]),
                json.dumps(manifest),
            ),
        )


def publish_node_code(queries: JobQueries, workspace_id: str, node_key: str, code: str) -> str:
    """发布 node_code 并返回其 code_hash（与 NodeCodeService 同款）。"""

    from server.app.services.node_codes import NodeCodeService

    service = NodeCodeService(queries, custom_nodes_enabled=True)
    service.save_draft(workspace_id, "wfchain", node_key, code, "test")
    row = service.publish(workspace_id, "wfchain", node_key)
    return str(row["code_hash"])


def seed_local_pool_execution(
    queries: JobQueries, job_id: str, node_key: str, impl_hash: str
) -> None:
    """播种本地池形态的完成执行：node_run(completed) 带身份列、无请求行。

    本地 code 池从不写 agent_execution_requests——v85 起身份记录落在
    node_runs（claim_lease 的 insert），这是该路径的播种镜像。
    """
    run = queries.start_node_run(
        job_id, node_key, ["python", "run.py"], "", agent_definition_hash=impl_hash
    )
    assert run is not None
    queries.finish_node_run(int(run["id"]), "completed", 0, "")


def seed_impl_identity(queries, workspace, job_id: str, node_keys) -> None:
    """给拟继承节点播种可证明的实现身份（codex 四轮 P1-1 后的测试基准）。

    普通继承用例的 completed 节点现在还要求「执行时身份 == 当前
    published 身份」：published node_code + 最新完成请求携带同一
    code_hash。不播种的节点按「实现不可证明」保守重跑（P1-1 语义，
    判别用例见 codex4 姊妹文件）。
    """
    for node_key in node_keys:
        code_hash = publish_node_code(
            queries, workspace["id"], node_key, f"def run(ctx):\n    return {{{node_key!r}}}\n"
        )
        queries.update_job_node(job_id, node_key, status="pending")
        seed_done_execution(
            queries, workspace["id"], job_id, node_key, kind="code", impl_hash=code_hash
        )
