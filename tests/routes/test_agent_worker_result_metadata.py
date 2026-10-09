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
            "agent_stderr_tail": "h" * 1000 + "y" * 4000,
        }
    )
    outcome_over, _ = parse_result_metadata(oversized)
    # #755 终审 P2-2：Host 兜底截断同样保尾（崩溃栈在流末尾）——满 tail
    # 时头截会把崩溃头整体丢掉。
    assert outcome_over.agent_stderr_tail == "y" * 4000


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
def test_artifacts_in_archive_marker_normalized_into_record() -> None:
    """#755 codex P1：清单走归档成员的标记——is True 归一进 record 审计面；
    AgentOutcome 不加字段（commit 层从 record 读标记、从归档读清单）。
    缺席与非 True 值（旧 Worker / 畸形载荷）一律 False。"""
    marked = json.loads(_payload({}))
    marked["output_artifacts_in_archive"] = True
    _, record = parse_result_metadata(json.dumps(marked))
    assert record["output_artifacts_in_archive"] is True

    _, record = parse_result_metadata(_payload({}))
    assert record["output_artifacts_in_archive"] is False

    truthy_but_not_bool = json.loads(_payload({}))
    truthy_but_not_bool["output_artifacts_in_archive"] = 1
    _, record = parse_result_metadata(json.dumps(truthy_but_not_bool))
    assert record["output_artifacts_in_archive"] is False


@_parse_only
def test_artifact_truncation_roundtrip_worker_header_to_host_parse() -> None:
    """#748 R3（codex review P1）roundtrip 的 v1 读侧钉子：128 条 CAS 字符串
    ref（~12KB）经真实传输形态（UTF-8 字节 → latin-1 视图 →
    _recover_result_header 反解）被 Host 读进 outcome/record：全量引用逐项
    还原、无截断标记。（#843 PR-2 起 Worker 写侧不再产 v1 头——本用例钉
    Host 对旧 Worker 头形态的读侧兼容窗，头载荷按旧 Worker 传输形态本地
    构造。）"""
    from server.app.routes.agent_worker_results import _recover_result_header

    # CAS 形态：直传失败换轨后 prepare 重备、或旧通道任务的
    # output_artifacts 形态。
    artifacts = {f"out-{i:03d}.json": f"sha256:{_HASH}" for i in range(128)}
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "任务完成",
        "command": ["pi"],
        "output_artifacts": artifacts,
        "run_dir": "runs/node_a/worker",
    }
    header = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    assert len(header) > 11 * 1024  # 真实大头场景（旧头预算形态的载荷）
    outcome, record = parse_result_metadata(_recover_result_header(header.decode("latin-1")))
    kept = outcome.output_artifacts
    # 全量 128 条 CAS 引用逐项还原——不截断、无标记。
    assert len(kept) == 128
    assert list(kept) == [f"out-{i:03d}.json" for i in range(128)]
    assert all(kept[name] == f"sha256:{_HASH}" for name in kept)
    assert outcome.output_artifacts_truncated is False
    assert outcome.output_artifacts_total == 0
    assert record["output_artifacts_truncated"] is False
    assert record["output_artifacts_total"] == 0
    # CJK error_message 同链路原样（非 ASCII 头不受回退影响）。
    assert outcome.error_message == "任务完成"


@_parse_only
def test_artifact_truncation_markers_parse_for_last_resort_shape() -> None:
    """最后手段形态（#755 时代旧 Worker 序列化器的截断输出——超长产物名的
    CAS 清单）经真实传输形态被 Host 读进 outcome/record：清单为空 +
    truncated/total 标记如实记录。标记是完成契约的一部分：Host 见
    truncated 跳过空清单改判、从归档暂存视图判定产物（#755 P2-1a），但仍
    不用它恢复直传 ref。（#843 PR-2 起新 Worker 不再产生该形态——读侧
    防御窗口按旧 Worker 线形态钉住，载荷本地构造。）"""
    from server.app.routes.agent_worker_results import _recover_result_header

    artifacts = {f"outputs/{i:03d}/" + "n" * 80 + ".json": f"sha256:{_HASH}" for i in range(128)}
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "任务完成",
        "command": [],
        # 旧序列化器的最后手段输出：清单整体降级为空 + 一次性 total 标记。
        "output_artifacts": {},
        "output_artifacts_truncated": True,
        "output_artifacts_total": len(artifacts),
        "run_dir": "runs/node_a/worker",
    }
    header = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    outcome, record = parse_result_metadata(_recover_result_header(header.decode("latin-1")))
    assert outcome.output_artifacts == {}
    assert outcome.output_artifacts_truncated is True
    assert outcome.output_artifacts_total == 128
    assert record["output_artifacts_truncated"] is True
    assert record["output_artifacts_total"] == 128


def _require_output_argv(count: int) -> list[str]:
    # agent argv 形态：每个 expected output 以 --require-output <name> 重复。
    argv = ["/usr/bin/velites", "run", "--provider", "p", "--model", "m"]
    for i in range(count):
        argv += ["--require-output", f"output-{i:03d}.json"]
    return argv


@_parse_only
def test_command_over_part_cap_is_truncated_not_rejected() -> None:
    """#822：旧 Worker（无序列化侧收缩）把 40+ 产物的 argv 原样送来——Host
    截断保前缀而非 ValueError（路由层即 400 → Worker 丢结果 → 重排队死循环）。"""
    from shared.code_contract import MAX_RESULT_COMMAND_PARTS

    argv = _require_output_argv(45)
    assert len(argv) > MAX_RESULT_COMMAND_PARTS
    outcome, record = parse_result_metadata(
        json.dumps(
            {
                "status": "completed",
                "exit_code": 0,
                "command": argv,
                "output_artifacts": {"out.json": dict(_REMOTE_REF)},
            }
        )
    )
    assert outcome.command == tuple(argv[:MAX_RESULT_COMMAND_PARTS])
    assert outcome.output_artifacts == {"out.json": _REMOTE_REF}
    assert record["status"] == "completed"


@_parse_only
@pytest.mark.parametrize("command", ["pi --x", {"0": "pi"}, 7])
def test_command_wrong_shape_still_rejected(command) -> None:
    """#822 只放宽段数：形态错误（非 list/tuple）照旧拒收。"""
    with pytest.raises(ValueError, match="invalid command"):
        parse_result_metadata(
            json.dumps(
                {"status": "failed", "exit_code": 1, "command": command, "output_artifacts": {}}
            )
        )


@pytest.mark.postgres
def test_legacy_worker_oversized_argv_result_is_committed(tmp_path) -> None:
    """#822 路由级：旧 Worker 形态（未收缩的 40+ 产物 argv，未撞头预算）直接
    投递——Host 204 落库、请求不再停留在 claimed（不进租约过期重排队）。"""
    from fastapi.testclient import TestClient

    from shared.code_contract import MAX_RESULT_COMMAND_PARTS
    from tests.helpers.agent_worker_api import claim as _claim
    from tests.helpers.agent_worker_api import empty_archive as _empty_archive
    from tests.helpers.agent_worker_api import make_app as _make_app
    from tests.helpers.agent_worker_api import register as _register
    from tests.helpers.agent_worker_api import seed_request as _seed_request

    argv = _require_output_argv(45)
    assert len(argv) > MAX_RESULT_COMMAND_PARTS
    metadata = {
        "status": "failed",
        "exit_code": 1,
        "error_message": "Agent process exited 1",
        "command": argv,
        "output_artifacts": {},
        "run_dir": "runs/node_a/worker",
    }
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-argv", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                # 旧 Worker 不经 _result_header_value：原样 JSON。
                "X-Agent-Result": json.dumps(metadata),
            },
            content=_empty_archive(),
        )
        assert response.status_code == 204, response.text

        with app.state.job_db.connect() as conn:
            row = conn.execute(
                "select state, outcome_json from agent_execution_requests where execution_id=%s",
                (claimed["execution_id"],),
            ).fetchone()
        assert row is not None
        assert row["state"] == "done"  # 已提交终态，不留 claimed 等租约过期
        stored = json.loads(row["outcome_json"])
        assert stored["status"] == "failed"
        assert stored["error_message"] == "Agent process exited 1"


@pytest.mark.postgres
def test_cjk_result_header_lands_in_database_intact(tmp_path) -> None:
    """#748 review P2 路由级验证（v1 读侧兼容窗）：旧 Worker 按「UTF-8 字节
    头」投递 CJK metadata，路由经 _recover_result_header 反解后 204 落库
    ——outcome_json 里的 CJK error_message / agent_stderr_tail 逐字符原样，
    latin-1 mojibake 不入库。（#843 PR-2 起新 Worker 不再用头携带元数据，
    头载荷按旧 Worker 传输形态本地构造。）"""
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
    header_bytes = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
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
