"""parse_result_metadata 的产物引用双形态校验（#160 D12）。

旧形态 ``"sha256:<64 hex>"`` 与对象存储形态
``{"storage_key", "size_bytes", "content_hash"}`` 并存；新形态严格校验
（jobs-staging/ 暂存前缀、无 ..、非负 int size、hash 为空或 64 位小写
hex）。
"""

from __future__ import annotations

import json

import pytest

from server.app.routes.agent_worker_results import parse_result_metadata

# 上方纯 parse 测试不触库，但模块里的 test_cjk_result_header_lands_in_database_intact
# 需要真实 app + 数据库——不能挂模块级 no_db（#748 R2：R1 的模块级 no_db 屏蔽了
# 该测试的 TRUNCATE 隔离，job-cjk 行跨 run 残留、二次运行即 UniqueViolation）。
# per-test no_db 保持纯 parse 测试跳过 DB 隔离的开销语义不变。
_parse_only = pytest.mark.no_db

_HASH = "a" * 64
_REMOTE_REF = {
    "storage_key": "jobs-staging/ws-1/job-1/exec-1/out.json",
    "size_bytes": 3,
    "content_hash": _HASH,
}


def _payload(artifacts: dict) -> str:
    return json.dumps(
        {"status": "completed", "exit_code": 0, "command": [], "output_artifacts": artifacts}
    )


@_parse_only
def test_legacy_string_ref_still_accepted() -> None:
    outcome, record = parse_result_metadata(_payload({"out.json": f"sha256:{_HASH}"}))
    assert outcome.output_artifacts == {"out.json": f"sha256:{_HASH}"}
    assert record["output_artifacts"] == outcome.output_artifacts


@_parse_only
def test_remote_dict_ref_accepted() -> None:
    outcome, _ = parse_result_metadata(_payload({"out.json": dict(_REMOTE_REF)}))
    assert outcome.output_artifacts == {"out.json": _REMOTE_REF}


@_parse_only
def test_remote_dict_ref_allows_empty_hash() -> None:
    ref = {**_REMOTE_REF, "content_hash": ""}
    outcome, _ = parse_result_metadata(_payload({"out.json": ref}))
    assert outcome.output_artifacts["out.json"] == ref


@_parse_only
def test_mixed_ref_forms_accepted() -> None:
    outcome, _ = parse_result_metadata(
        _payload({"a.json": f"sha256:{_HASH}", "b.json": dict(_REMOTE_REF)})
    )
    assert outcome.output_artifacts["a.json"] == f"sha256:{_HASH}"
    assert outcome.output_artifacts["b.json"] == _REMOTE_REF


@_parse_only
@pytest.mark.parametrize(
    "ref",
    [
        "sha256:nothex",  # 旧形态 hash 非法
        123,  # 非 str/dict
        ["jobs-staging/ws/job/out.json"],  # list 非法
        {**_REMOTE_REF, "storage_key": "other/ws/job/out.json"},  # 前缀必须 jobs-staging/
        {**_REMOTE_REF, "storage_key": "jobs/ws-1/job-1/out.json"},  # 权威 key 不收
        {**_REMOTE_REF, "storage_key": "jobs-staging/ws/../out.json"},  # 禁止 ..
        {**_REMOTE_REF, "storage_key": "/jobs-staging/ws/job/out.json"},  # 绝对路径
        {**_REMOTE_REF, "storage_key": ""},
        {**_REMOTE_REF, "size_bytes": -1},
        {**_REMOTE_REF, "size_bytes": True},  # bool 不是 int
        {**_REMOTE_REF, "size_bytes": "3"},
        {**_REMOTE_REF, "content_hash": "A" * 64},  # 必须小写
        {**_REMOTE_REF, "content_hash": "abc"},
        {**_REMOTE_REF, "content_hash": 7},
    ],
)
def test_invalid_refs_rejected(ref: object) -> None:
    with pytest.raises(ValueError):
        parse_result_metadata(_payload({"out.json": ref}))


@_parse_only
def test_artifact_count_cap_unchanged() -> None:
    artifacts = {f"out-{i}.json": f"sha256:{_HASH}" for i in range(129)}
    with pytest.raises(ValueError, match="invalid output artifacts"):
        parse_result_metadata(_payload(artifacts))


@_parse_only
def test_agent_stderr_tail_accepted_and_bounded() -> None:
    """#748: crash 结果可选携带 agent_stderr_tail——读进 outcome/record，超限
    防御性截断（写侧已截，读侧兜底老/异构 Worker）。"""
    payload = json.dumps(
        {
            "status": "failed",
            "exit_code": 3,
            "error_message": "Agent process exited 3: ValueError: boom",
            "command": [],
            "output_artifacts": {},
            "agent_stderr_tail": "Traceback (most recent call last):\nValueError: boom",
        }
    )
    outcome, record = parse_result_metadata(payload)
    assert outcome.agent_stderr_tail.startswith("Traceback")
    assert outcome.agent_stderr_tail == record["agent_stderr_tail"]

    oversized = json.dumps(
        {
            "status": "failed",
            "exit_code": 3,
            "error_message": "x",
            "command": [],
            "output_artifacts": {},
            "agent_stderr_tail": "y" * 5000,
        }
    )
    outcome_over, _ = parse_result_metadata(oversized)
    assert len(outcome_over.agent_stderr_tail) == 4000


@_parse_only
def test_agent_stderr_tail_absent_defaults_empty() -> None:
    """旧 Worker / 非崩溃结果不带该键：outcome 与 record 均为空串，不报错。"""
    outcome, record = parse_result_metadata(_payload({}))
    assert outcome.agent_stderr_tail == ""
    assert record["agent_stderr_tail"] == ""


@_parse_only
def test_artifact_truncation_markers_accepted_absent_defaults() -> None:
    """#748 R2 P2-1：无截断标记的载荷（旧 Worker / 未触发预算）默认
    truncated=False、total=0——与 agent_stderr_tail 的接法一致。"""
    outcome, record = parse_result_metadata(_payload({}))
    assert outcome.output_artifacts_truncated is False
    assert outcome.output_artifacts_total == 0
    assert record["output_artifacts_truncated"] is False
    assert record["output_artifacts_total"] == 0


@_parse_only
def test_artifact_truncation_roundtrip_worker_header_to_host_parse() -> None:
    """#748 R2 P2-1 roundtrip：Worker 侧 128 直传 ref 头收缩（前缀 + 标记）
    经真实传输形态（UTF-8 字节 → latin-1 视图 → _recover_result_header 反解）
    被 Host 读进 outcome/record——截断标记与保留前缀逐项还原。"""
    from server.app.routes.agent_worker_results import _recover_result_header
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    ref = {
        "storage_key": "jobs-staging/ws-1/job-1/exec-1/out.json",
        "size_bytes": 3,
        "content_hash": _HASH,
    }
    artifacts = {f"out-{i:03d}.json": dict(ref) for i in range(128)}
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "任务完成",
        "command": ["pi"],
        "output_artifacts": artifacts,
        "run_dir": "runs/node_a/worker",
    }
    header = _result_header_value(metadata)
    assert len(header) > _RESULT_HEADER_BUDGET * 0.8  # 真实大头场景
    outcome, record = parse_result_metadata(_recover_result_header(header.decode("latin-1")))
    kept = outcome.output_artifacts
    assert 0 < len(kept) < 128
    assert list(kept) == [f"out-{i:03d}.json" for i in range(len(kept))]
    assert all(kept[name] == ref for name in kept)
    assert outcome.output_artifacts_truncated is True
    assert outcome.output_artifacts_total == 128
    assert record["output_artifacts_truncated"] is True
    assert record["output_artifacts_total"] == 128
    # CJK error_message 同链路原样（非 ASCII 头不被截断标记破坏）。
    assert outcome.error_message == "任务完成"


@pytest.mark.postgres
def test_cjk_result_header_lands_in_database_intact(tmp_path) -> None:
    """#748 review P2 路由级验证：Worker 按「UTF-8 字节头」投递 CJK metadata
    （worker.host.transfer._result_header_value 的形态），路由经
    _recover_result_header 反解后 204 落库——outcome_json 里的 CJK
    error_message / agent_stderr_tail 逐字符原样，latin-1 mojibake 不入库。"""
    from fastapi.testclient import TestClient

    from tests.helpers.agent_worker_api import (
        claim as _claim,
    )
    from tests.helpers.agent_worker_api import (
        empty_archive as _empty_archive,
    )
    from tests.helpers.agent_worker_api import (
        make_app as _make_app,
    )
    from tests.helpers.agent_worker_api import (
        register as _register,
    )
    from tests.helpers.agent_worker_api import (
        seed_request as _seed_request,
    )
    from worker.host.transfer import _result_header_value

    tail = "追踪" * 500  # 1000 个 CJK 字符（3 字节/字）
    metadata = {
        "status": "failed",
        "exit_code": 3,
        "error_message": "Agent process exited 3: 任务执行失败",
        "command": ["pi"],
        "output_artifacts": {},
        "run_dir": "runs/node_a/worker",
        "agent_stderr_tail": tail,
    }
    header_bytes = _result_header_value(metadata)
    assert len(header_bytes) > 3 * 1024  # 真实的 CJK 头场景，非 ASCII 转义

    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-cjk", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": header_bytes,
            },
            content=_empty_archive(),
        )
        assert response.status_code == 204, response.text

        with app.state.job_db.connect() as conn:
            row = conn.execute(
                "select outcome_json from agent_execution_requests where execution_id=%s",
                (claimed["execution_id"],),
            ).fetchone()
        assert row is not None
        stored = json.loads(row["outcome_json"])
        assert stored["error_message"] == "Agent process exited 3: 任务执行失败"
        assert stored["agent_stderr_tail"] == tail
        # latin-1 mojibake 形态绝不能入库（反解失败时的透传会留下痕迹）。
        assert "\\u" not in row["outcome_json"]
