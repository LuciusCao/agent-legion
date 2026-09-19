"""node_runs 实现身份记录的写入路径单测（issue #645，schema v85）。

三条 insert node_runs 路径各验一条（设计 §2.3）：

- 本地池：``LeaseClaimRequest.agent_definition_hash`` → ``claim_lease``
  事务内 insert 带列（executor_claim 构造请求时算 sha256(code_text)）；
- Worker/Agent：``broker.claim`` → ``promote_claim`` 从 scan 的
  ``select r.*`` 取请求行 ``agent_definition_hash`` 落列；
- 服务/测试：``JobQueries.start_node_run`` 可选参数。

播种端到端的主/漂移用例见
``tests/services/test_job_workflow_upgrade_inherit_codex4.py``（v85 段）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import closing

import pytest

from server.app.agent_broker import AgentExecutionRequest
from server.app.db.connection import connect_database
from server.app.executors.models import LeaseClaimRequest
from server.app.jobs import JobQueries
from shared.protocol import PROTOCOL_VERSION
from tests.executors.leases.helpers import _claim_request, _setup_workspace
from tests.helpers.agent_worker_api import (
    broker,
    insert_code_job_rows,
    seed_request,
)
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def queries(tmp_path) -> JobQueries:
    return JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")


def _latest_run_identity(job_db: JobQueries, job_id: str, node_key: str) -> str | None:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select agent_definition_hash from node_runs"
            " where job_id=%s and node_key=%s order by id desc limit 1",
            (job_id, node_key),
        ).fetchone()
    return None if row is None else str(row["agent_definition_hash"])


def test_local_pool_claim_persists_impl_hash(queries: JobQueries) -> None:
    """本地池路径：claim_lease 的 insert 落 node_runs.agent_definition_hash。

    LeaseClaimRequest 携带 dispatch 解析的 code hash（executor_claim 构造
    时 sha256(code_text)），claim 事务内的 node_runs insert 带列——本地
    code 池执行从此有可证明的实现身份记录（v85 前恒「不可证明」）。
    """
    from server.app.db.transaction import write_transaction
    from server.app.executors._lease_claims import claim_lease

    workspace_id, job_id = _setup_workspace(queries, "ws-impl-local", workspace_limit=2)
    code_text = "def run(ctx):\n    return {'v': 1}\n"
    impl_hash = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
    request = LeaseClaimRequest(
        **{**_claim_request(workspace_id, job_id).__dict__, "agent_definition_hash": impl_hash}
    )

    with write_transaction(TEST_DATABASE_URL) as conn:
        claimed = claim_lease(conn, request, data_dir=queries.jobs_dir.parent)
    assert claimed is not None

    assert _latest_run_identity(queries, job_id, "review_keywords") == impl_hash


def test_local_pool_claim_without_hash_persists_empty(queries: JobQueries) -> None:
    """node_code 缺失（理论不可达角）→ 空串落列 = 不可证明，不误导判定。"""
    from server.app.db.transaction import write_transaction
    from server.app.executors._lease_claims import claim_lease

    workspace_id, job_id = _setup_workspace(queries, "ws-impl-empty", workspace_limit=2)
    request = _claim_request(workspace_id, job_id)  # agent_definition_hash 默认 ""

    with write_transaction(TEST_DATABASE_URL) as conn:
        claimed = claim_lease(conn, request, data_dir=queries.jobs_dir.parent)
    assert claimed is not None

    assert _latest_run_identity(queries, job_id, "review_keywords") == ""


def _register_worker(worker_id: str, *, max_code_concurrency: int = 0) -> None:
    from server.app.agent_control.registry import AgentWorkerRegistry

    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        max_concurrency=10,
        max_code_concurrency=max_code_concurrency,
        labels={"arch": "arm64"},
        protocol_version=PROTOCOL_VERSION,
    )


def test_worker_promote_claim_persists_request_hash(job_db) -> None:
    """Worker/Agent 路径：promote_claim 把请求行的身份镜像到 node_runs。

    scan 的 ``select r.*`` 已含 ``agent_definition_hash``；promote 事务内
    的 node_runs insert 带同值——enqueue 时刻与 claim 时刻身份同源。
    """
    pool = broker(job_db.jobs_dir.parent)
    seed_request(job_db, job_id="impl-worker-job")
    # 请求行的身份（seed_request 用 definition_hash() 填）：
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select agent_definition_hash from agent_execution_requests"
            " where job_id='impl-worker-job' and state='queued'"
        ).fetchone()
    expected = str(row["agent_definition_hash"])
    assert expected

    _register_worker("worker-impl")
    claimed = pool.claim("worker-impl")
    assert claimed is not None

    assert _latest_run_identity(job_db, "impl-worker-job", "generate") == expected


def test_worker_promote_claim_code_row_persists_hash(job_db) -> None:
    """kind='code' 请求行同镜像：code 行的 agent_definition_hash 是 code
    文本 sha256（CodeDispatchService.enqueue 同口径）。"""
    pool = broker(job_db.jobs_dir.parent)
    insert_code_job_rows(job_db, job_id="impl-code-job")
    code_hash = hashlib.sha256(b"print('hello')").hexdigest()
    pool.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id="impl-code-job",
            workflow_key="questions",
            node_key="package",
            agent_id="package",
            agent_definition_hash=code_hash,
            manifest={
                "kind": "code",
                "capability": "package",
                "code_hash": code_hash,
                "job_id": "impl-code-job",
                "log_path": "logs/impl-code-job.log",
                "config": {},
            },
            kind="code",
        )
    )

    _register_worker("worker-impl-code", max_code_concurrency=10)
    claimed = pool.claim("worker-impl-code")
    assert claimed is not None

    assert _latest_run_identity(job_db, "impl-code-job", "package") == code_hash


def test_start_node_run_optional_hash_param(queries: JobQueries) -> None:
    """服务/测试路径：start_node_run 可选参数带身份，缺省空串零改动。"""
    workspace_id, job_id = _setup_workspace(queries, "ws-impl-service", workspace_limit=2)
    impl_hash = hashlib.sha256(b"service-path-code").hexdigest()

    with_hash = queries.start_node_run(
        job_id, "review_keywords", ["python", "run.py"], "", agent_definition_hash=impl_hash
    )
    assert with_hash is not None
    assert str(with_hash["agent_definition_hash"]) == impl_hash

    # 同一节点的第二次 start 会被 pending/ready/stale 守卫拒绝（返回
    # None）——复位后取第二次，验证缺省空串路径。
    queries.update_job_node(job_id, "review_keywords", status="pending")
    without_hash = queries.start_node_run(job_id, "review_keywords", ["python"], "")
    assert without_hash is not None
    assert str(without_hash["agent_definition_hash"]) == ""


def test_latest_done_request_identities_node_runs_first(job_db) -> None:
    """读取端两段合并：node_runs 段优先，请求行只补未覆盖 node_key。

    直接驱动 JobQueries 门面（BOUNDARY-DATA-001 的数据层入口）：a 只有
    请求行（历史 Worker 作业形态）→ fallback；b 有 node_runs 非空身份
    → 段 1 命中，即使另有更旧的请求行也不覆盖；c 无任何记录 → 缺席。
    """
    workspace_id = "test-workspace"
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Test', 'demo_workflow') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('impl-read-job', %s, 'question', 'q')",
            (workspace_id,),
        )
        for key in ("a", "b"):
            conn.execute(
                "insert into job_nodes(job_id, node_key) values ('impl-read-job', %s)", (key,)
            )

    def _seed(node_key: str, *, kind: str, impl_hash: str, run_hash: str = "") -> None:
        run = job_db.start_node_run(
            "impl-read-job", node_key, ["pi"], "", agent_definition_hash=run_hash
        )
        assert run is not None
        job_db.finish_node_run(int(run["id"]), "completed", 0, "")
        with closing(connect_database(job_db.dsn_identity)) as conn, conn:
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
                    str(uuid.uuid4()),
                    workspace_id,
                    "impl-read-job",
                    node_key,
                    kind,
                    f"cap_{node_key}",
                    impl_hash,
                    int(run["id"]),
                    "{}",
                ),
            )

    # a：请求行 V1（历史形态）；b：请求行 V1 + node_runs V2。
    _seed("a", kind="code", impl_hash="hash-a-request")
    _seed("b", kind="code", impl_hash="hash-b-request")
    job_db.update_job_node("impl-read-job", "b", status="pending")
    run = job_db.start_node_run(
        "impl-read-job", "b", ["python"], "", agent_definition_hash="hash-b-run"
    )
    assert run is not None
    job_db.finish_node_run(int(run["id"]), "completed", 0, "")

    identities = job_db.latest_done_request_identities("impl-read-job", ["a", "b", "c"])

    # a 走请求行 fallback（kind 保留，skill 面空——manifest 无 skill 键）；
    # b 取 node_runs 段（kind/skill_commit 空哨兵，skill_version 空串——
    # 播种未带）；c 缺席（不可证明）。codex 五轮 P1-A 起记录面是 4 元组
    # ``(kind, hash, skill_commit, skill_version)``。
    assert identities == {"a": ("code", "hash-a-request", "", ""), "b": ("", "hash-b-run", "", "")}


def test_latest_done_request_identities_empty_run_hash_falls_back(job_db) -> None:
    """node_runs 身份为空串（v85 前的 run 行）→ 段 1 不命中，请求行兜底。"""
    workspace_id = "test-workspace"
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Test', 'demo_workflow') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('impl-read-empty', %s, 'question', 'q')",
            (workspace_id,),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values ('impl-read-empty', 'a')")

    # run 行不带身份（v85 前形态），请求行有 done 记录。
    run = job_db.start_node_run("impl-read-empty", "a", ["pi"], "")
    assert run is not None
    job_db.finish_node_run(int(run["id"]), "completed", 0, "")
    with closing(connect_database(job_db.dsn_identity)) as conn, conn:
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
                str(uuid.uuid4()),
                workspace_id,
                "impl-read-empty",
                "a",
                "agent",
                "cap_a",
                "hash-a-request",
                int(run["id"]),
                "{}",
            ),
        )

    identities = job_db.latest_done_request_identities("impl-read-empty", ["a"])
    assert identities == {"a": ("agent", "hash-a-request", "", "")}


def test_latest_done_request_identities_never_borrows_older_run_identity(job_db) -> None:
    """最新 completed run 不可证明时，不得越过它借用旧 run 的身份。

    当前产物由最新 run 产生；若读取 SQL 先过滤空 hash，再按 id 倒序，
    就会把更老执行的可证明身份冒充当前产物证据，错误允许 inherit。
    """
    workspace_id = "test-workspace"
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Test', 'demo_workflow') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('impl-read-latest-empty', %s, 'question', 'q')",
            (workspace_id,),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key) values ('impl-read-latest-empty', 'a')"
        )

    old_run = job_db.start_node_run(
        "impl-read-latest-empty", "a", ["pi"], "", agent_definition_hash="old-hash"
    )
    assert old_run is not None
    job_db.finish_node_run(int(old_run["id"]), "completed", 0, "")

    job_db.update_job_node("impl-read-latest-empty", "a", status="pending")
    latest_run = job_db.start_node_run("impl-read-latest-empty", "a", ["pi"], "")
    assert latest_run is not None
    job_db.finish_node_run(int(latest_run["id"]), "completed", 0, "")

    identities = job_db.latest_done_request_identities("impl-read-latest-empty", ["a"])

    assert identities == {"a": ("", "", "", "")}


def test_latest_done_request_identities_projects_skill_face(job_db) -> None:
    """codex 五轮 P1-A：skill 身份投影（段 2 manifest / 段 1 skill_version）。

    请求行 manifest 携带完整 ``skill_commit``（mark_done trim 保留该键）
    → 段 2 返回完整 sha 与 version；node_runs 段只有 v75 的
    ``skill_version``（ref@commit12）→ 段 1 的 skill_commit 是空串哨兵、
    version 尾段是 12 位前缀（服务层按前缀比较）。
    """
    workspace_id = "test-workspace"
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Test', 'demo_workflow') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('impl-read-skill', %s, 'question', 'q')",
            (workspace_id,),
        )
        for key in ("a", "b"):
            conn.execute(
                "insert into job_nodes(job_id, node_key) values ('impl-read-skill', %s)", (key,)
            )

    # a：请求行形态（manifest 带 skill 四件，模拟真实 dispatch 的
    # SkillCheckout.manifest_pins()）。
    run = job_db.start_node_run("impl-read-skill", "a", ["pi"], "")
    assert run is not None
    job_db.finish_node_run(int(run["id"]), "completed", 0, "")
    with closing(connect_database(job_db.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, node_key, kind, agent_id,
              agent_definition_hash, node_concurrency_limit, state,
              queued_at, claimed_at, finished_at, node_run_id, manifest_json)
            values (%s, %s, %s, %s, 'agent', 'cap_a', 'hash-a', 1, 'done',
                    current_timestamp, current_timestamp, current_timestamp, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                workspace_id,
                "impl-read-skill",
                "a",
                int(run["id"]),
                json.dumps(
                    {
                        "skill": "g/n",
                        "skill_ref": "latest",
                        "skill_version": "latest@0123456789ab",
                        "skill_commit": "0" * 40,
                    }
                ),
            ),
        )
    # b：node_runs 形态（v75 skill_version 列；无请求行）。
    run_b = job_db.start_node_run(
        "impl-read-skill",
        "b",
        ["pi"],
        "",
        skill_version="v1@abcdef123456",
        skill="g/n",
        agent_definition_hash="hash-b-run",
    )
    assert run_b is not None
    job_db.finish_node_run(int(run_b["id"]), "completed", 0, "")

    identities = job_db.latest_done_request_identities("impl-read-skill", ["a", "b"])

    assert identities["a"] == ("agent", "hash-a", "0" * 40, "latest@0123456789ab")
    # b：node_runs 段（impl hash 非空才进段 1；skill_commit 空哨兵 +
    # skill_version 前缀，服务层从尾段恢复 12 位前缀比较）。
    assert identities["b"] == ("", "hash-b-run", "", "v1@abcdef123456")
