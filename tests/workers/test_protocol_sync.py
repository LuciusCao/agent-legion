"""Cross-side protocol constant synchronization.

The registration protocol versions live once in shared/protocol.py (the
worker image ships shared/); both the Worker declaration and the Host
contract default must derive from that single copy. Before this module the
two sides carried independent literals with "bump both together" comments
and no test noticed drift.

#282 extends the same discipline to the mirrors that cannot collapse into
one import: the auth-failure marker path (node_sdk must stay
import-self-contained — the code bundle ships only the workspace_libs
snapshot into the sandbox) and the kind='code' result-metadata key set
(shared.CODE_RESULT_METADATA_KEYS: written by the Worker's
prepare_code_result, read by the Host's parse_result_metadata).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from shared.code_sandbox import CODE_RESULT_METADATA_KEYS
from shared.protocol import (
    CODE_PROTOCOL_VERSION,
    MODEL_RUNTIME_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
)
from worker.host.client import Client

pytestmark = pytest.mark.no_db


def test_worker_declared_version_is_latest_shared() -> None:
    assert Client.__module__  # import sanity
    from worker.host.client import PROTOCOL_VERSION as worker_declared

    assert worker_declared is PROTOCOL_VERSION
    assert PROTOCOL_VERSION == MODEL_RUNTIME_PROTOCOL_VERSION


def test_host_contract_default_matches_shared() -> None:
    from server.app.routes.agent_workers_contracts import RegisterAgentWorkerResponse

    field = RegisterAgentWorkerResponse.model_fields["host_protocol_version"]
    assert field.default == MODEL_RUNTIME_PROTOCOL_VERSION


def test_server_registry_constants_match_shared() -> None:
    from server.app.agent_control.registry import (
        CODE_PROTOCOL_VERSION as server_code,
    )
    from server.app.agent_control.registry import (
        MODEL_RUNTIME_PROTOCOL_VERSION as server_model_runtime,
    )

    assert server_code is CODE_PROTOCOL_VERSION
    assert server_model_runtime is MODEL_RUNTIME_PROTOCOL_VERSION


def test_pydantic_default_binding_is_stable() -> None:
    # The contract default binds the shared constant at class-creation time;
    # a plain literal regression (host_protocol_version: int = 3) would keep
    # passing the value check above only until the shared constant bumps —
    # assert identity of the annotation source instead.
    from server.app.routes.agent_workers_contracts import (
        RegisterAgentWorkerResponse as Response,
    )

    assert issubclass(Response, BaseModel)
    assert Response.model_fields["host_protocol_version"].default is MODEL_RUNTIME_PROTOCOL_VERSION


# --- #282: 无守卫的镜像契约 -------------------------------------------------
#
# 两处「镜像常量 + keep-in-sync 注释」此前没有测试断言两侧相等，一旦
# str 版漂移，auth 失败上报静默失效；metadata 键漂移则跨进程字段消失。


def test_auth_failure_marker_path_mirrors_node_sdk() -> None:
    """shared 的 marker 路径（str）必须与 node_sdk 的 Path 拼接结果一致。

    node_sdk 不能 import shared（代码 bundle 只带 workspace_libs 快照进沙箱，
    SDK 必须自包含），所以镜像只能靠这个测试守：shared 是相对路径 str、
    node_sdk 是 ``Path(NODE_RUNTIME_DIR) / AUTH_FAILURE_MARKER``，断言的是
    「拼出来的最终路径一致」——Worker 侧 code_runner / Host 侧
    _code_runtime 各自拿自己那份定义 join 到 job_dir 上，路径不等就互相
    读不到对方的 marker（unlink 预清 + 读取两侧都要成立）。"""
    from shared.code_sandbox import AUTH_FAILURE_MARKER_PATH as shared_marker
    from workspace_libs.node_sdk import (
        AUTH_FAILURE_MARKER_PATH as sdk_marker,
    )
    from workspace_libs.node_sdk import NODE_RUNTIME_DIR

    # shared 侧：纯相对路径 str，供 Path / str 两种 join 用。
    assert not Path(shared_marker).is_absolute()
    assert Path(shared_marker) == sdk_marker
    # join 语义也要一致（code_runner: job_dir / AUTH_FAILURE_MARKER_PATH）。
    job_dir = Path("/work/exec-1/job")
    assert job_dir / shared_marker == job_dir / NODE_RUNTIME_DIR / "auth_failure"


def test_code_result_metadata_keys_match_prepare_code_result(tmp_path: Path) -> None:
    """worker 的 prepare_code_result 产出的键集 == shared 契约常量。

    直接按函数结构断言（不起真实沙箱）：一条无 auth 上报的完整路径产出
    恒在键；带 auth_failure_connection 的产出补上可选键。code_result 为空
    dict 的降级路径（prepare 失败兜底）同样落在契约内。"""
    from shared.code_sandbox import CODE_RESULT_METADATA_KEYS as keys
    from worker.result_archive import prepare_code_result
    from worker.upload.queue import UploadTask

    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir()

    def _task(code_result: dict[str, Any] | None) -> UploadTask:
        return UploadTask(
            execution_id="exec-1",
            lease_id="lease-1",
            execution_dir=execution_dir,
            node_key="node_a",
            status_fields={"node_key": "node_a"},
            kind="process",
            exec_kind="code",
            code_result=code_result,
            exit_code=0,
            expected_outputs=("output.json",),
            command=("/usr/bin/velites", "sandbox", "wrap"),
        )

    always_present = keys - {"auth_failure_connection"}
    metadata, _, _ = prepare_code_result(_task({"status": "completed", "error_message": ""}))
    assert set(metadata) == always_present

    metadata_auth, _, _ = prepare_code_result(
        _task({"status": "completed", "error_message": "", "auth_failure_connection": "cms-main"})
    )
    assert set(metadata_auth) == keys
    assert metadata_auth["auth_failure_connection"] == "cms-main"

    metadata_empty, _, _ = prepare_code_result(_task(None))
    assert set(metadata_empty) == always_present
    assert metadata_empty["status"] == "failed"


def test_host_metadata_reader_consumes_all_code_result_keys() -> None:
    """Host 读取方（parse_result_metadata）认得契约全集：worker 按常量写出的
    每个键都能被解析进 outcome/record，不静默丢字段。反向（host 收一个
    缺恒在键的载荷走默认值）不在本守卫范围——契约是「worker 写什么」。"""
    from server.app.routes.agent_worker_results import parse_result_metadata

    payload = {
        "status": "failed",
        "exit_code": 124,
        "error_message": "code node timed out",
        "command": ["/usr/bin/velites", "sandbox", "wrap"],
        "output_artifacts": {},
        "auth_failure_connection": "cms-main",
    }
    assert set(payload) == set(CODE_RESULT_METADATA_KEYS)
    outcome, record = parse_result_metadata(json.dumps(payload))
    assert outcome.status == "failed"
    assert outcome.exit_code == 124
    assert outcome.error_message == "code node timed out"
    assert outcome.command == ("/usr/bin/velites", "sandbox", "wrap")
    assert outcome.output_artifacts == {}
    assert outcome.auth_failure_connection == "cms-main"
    assert record["auth_failure_connection"] == "cms-main"
