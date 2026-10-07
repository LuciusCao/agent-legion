"""写入侧节点日志命名（PR #1065 删除路径模型）：claim_submit / shards 写日志的
路径必须等于 ``job_log_dir(logs_dir) / job_node_log_name(...)``——job 删除按
同一命名函数反推已删 job 拥有的日志，写入方若再内联拼名，删除侧就会漏删或
误删。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.storage_paths import job_log_dir, job_node_log_name
from server.app.workflow_worker import claim_submit, shards
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.schema import WorkflowShardSpec

pytestmark = pytest.mark.no_db

# 含连字符与 ``-shard-`` 的 job id / 节点 key：命名碰撞最容易出错的形态。
_JOB = {"id": "ws_wf_Q030-extract-shard-0", "workspace_id": "ws"}


def _worker(tmp_path: Path) -> MagicMock:
    worker = MagicMock()
    worker.settings.logs_dir = tmp_path
    worker.code_stock.pass_budget.return_value = None
    return worker


def test_claim_submit_node_log_uses_naming_function(tmp_path: Path) -> None:
    node = WorkflowNode(key="split-shard-1", label="n", capability="c", outputs=["o.json"])
    captured: list[Path] = []

    def _capture(
        _worker: Any, _ws: Any, _job: Any, _wf: Any, _node: Any, log_path: Path, *_a: Any, **_k: Any
    ) -> bool:
        captured.append(log_path)
        return True

    route = MagicMock(kind="error", error_message="boom")
    with (
        patch.object(claim_submit, "resolve_node_route", return_value=route),
        patch.object(claim_submit, "fail_node_config", _capture),
    ):
        claim_submit.try_claim_and_submit(
            _worker(tmp_path),
            {"id": "ws"},
            MagicMock(key="wf"),
            _JOB,
            node,
            tmp_path / "job",
            None,
            None,
            CapacitySnapshot(),
        )

    assert captured == [job_log_dir(tmp_path) / job_node_log_name(_JOB["id"], node.key)]


def _shard_node() -> WorkflowNode:
    return WorkflowNode(
        key="fan-out",
        label="fan",
        capability="fan",
        outputs=["out.json"],
        shard=WorkflowShardSpec(count=3),
    )


def test_shard_node_failure_log_uses_naming_function(tmp_path: Path) -> None:
    node = _shard_node()
    captured: list[Path] = []

    def _capture(
        _worker: Any, _ws: Any, _job: Any, _wf: Any, _node: Any, log_path: Path, *_a: Any, **_k: Any
    ) -> None:
        captured.append(log_path)

    with (
        patch.object(shards, "get_local_node_limit", return_value=None),
        patch.object(shards, "_read_shard_rows", return_value=[]),
        patch.object(shards, "_resolve_shard_inputs", side_effect=ValueError("bad")),
        patch.object(shards, "_fail_node", _capture),
    ):
        shards.claim_shard_node(
            _worker(tmp_path), {"id": "ws"}, _JOB, node, tmp_path, None, None, CapacitySnapshot()
        )

    assert captured == [job_log_dir(tmp_path) / job_node_log_name(_JOB["id"], node.key)]


def test_shard_logs_use_naming_function(tmp_path: Path) -> None:
    node = _shard_node()
    rows = [{"shard_index": index, "status": "pending", "input_json": "{}"} for index in (0, 1, 10)]
    captured: list[Path] = []

    def _capture(
        _worker: Any,
        _ws: Any,
        _job: Any,
        _node: Any,
        _dir: Any,
        log_path: Path,
        *_a: Any,
        **_k: Any,
    ) -> bool:
        captured.append(log_path)
        return True

    with (
        patch.object(shards, "get_local_node_limit", return_value=None),
        patch.object(shards, "_read_shard_rows", return_value=rows),
        patch.object(shards, "try_claim_code_worker_node", _capture),
    ):
        shards.claim_shard_node(
            _worker(tmp_path), {"id": "ws"}, _JOB, node, tmp_path, None, None, CapacitySnapshot()
        )

    log_dir = job_log_dir(tmp_path)
    assert captured == [log_dir / job_node_log_name(_JOB["id"], node.key, i) for i in (0, 1, 10)]
