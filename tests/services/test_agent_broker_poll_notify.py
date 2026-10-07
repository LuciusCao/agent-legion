"""#957: claim 轮询的状态面板镜像——worker 行与全 workspace 列表一次查询取回。

不受限 Worker（allowed_workspaces_json = []）登记到每个 workspace，受限
Worker 只登记白名单内的；两种形态都只发一条 SQL。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from server.app.agent_broker import broker as broker_module
from server.app.agent_control.registry import AgentWorkerRegistry
from tests.helpers.agent_worker_api import broker
from tests.postgres_support import TEST_DATABASE_URL


class _Panel:
    def __init__(self) -> None:
        self.ensured: list[tuple[str, str, int, str]] = []
        self.busy: list[tuple[str, str]] = []

    def ensure_workspace_agent(
        self, agent_id: str, workspace_id: str, *, max_tasks: int = 1, name: str = ""
    ) -> None:
        self.ensured.append((agent_id, workspace_id, max_tasks, name))

    def set_busy(self, agent_id: str, *, workspace_id: str = "") -> None:
        self.busy.append((agent_id, workspace_id))


def _count_statements(monkeypatch) -> list[str]:
    log: list[str] = []
    real = broker_module.read_connection

    class _Conn:
        def __init__(self, conn: Any) -> None:
            self._conn = conn

        def execute(self, sql: Any, *args: Any, **kwargs: Any) -> Any:
            log.append(str(sql))
            return self._conn.execute(sql, *args, **kwargs)

    @contextmanager
    def counting(dsn: str):
        with real(dsn) as conn:
            yield _Conn(conn)

    monkeypatch.setattr(broker_module, "read_connection", counting)
    return log


def _seed(job_db, allowed: list[str]) -> None:
    with job_db.connect() as conn:
        for ws in ("ws-b", "ws-a", "ws-c"):
            conn.execute("insert into workspaces(id, name) values (%s, %s)", (ws, ws))
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-1",
        name="Worker One",
        runtimes=["pi"],
        max_concurrency=3,
        labels={"arch": "arm64"},
    )
    with job_db.connect() as conn:
        conn.execute(
            "update agent_workers set allowed_workspaces_json=%s where worker_id='worker-1'",
            (json.dumps(allowed),),
        )


def test_unrestricted_worker_mirrors_every_workspace_in_one_query(job_db, monkeypatch) -> None:
    _seed(job_db, [])
    panel = _Panel()
    instance = broker(job_db.jobs_dir.parent)
    instance.agent_status = panel
    log = _count_statements(monkeypatch)

    instance._notify_worker_poll("worker-1", None)

    assert len(log) == 1
    assert [ws for _, ws, _, _ in panel.ensured] == ["ws-a", "ws-b", "ws-c"]
    assert {(max_tasks, name) for _, _, max_tasks, name in panel.ensured} == {(3, "Worker One")}
    assert panel.busy == []


def test_restricted_worker_mirrors_only_allowed_workspaces(job_db, monkeypatch) -> None:
    _seed(job_db, ["ws-c", "ws-a"])
    panel = _Panel()
    instance = broker(job_db.jobs_dir.parent)
    instance.agent_status = panel
    log = _count_statements(monkeypatch)

    instance._notify_worker_poll("worker-1", None)

    assert len(log) == 1
    assert [ws for _, ws, _, _ in panel.ensured] == ["ws-a", "ws-c"]


def test_unknown_worker_mirrors_nothing(job_db) -> None:
    panel = _Panel()
    instance = broker(job_db.jobs_dir.parent)
    instance.agent_status = panel

    instance._notify_worker_poll("ghost", None)

    assert panel.ensured == []
