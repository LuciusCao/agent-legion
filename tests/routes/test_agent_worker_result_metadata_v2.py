"""#843 PR-1：结果元数据 v2 双形态读（归档成员 result.json，Host 读侧）。

v2 形态（请求头 ``X-Agent-Result-Format: 2`` + 归档成员 ``result.json``）：
roundtrip 落库、与 v1 头路径的同校验（截断口径一致）、成员缺失/非 JSON/
非 dict 的 400 语义、16KiB+ 大 metadata 通过（头预算不复存在——本 PR 的
存在意义），以及 v1 回归钉子（无 format 头 / 值非 2 → 现行头路径，
零行为变化）。
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from server.app.agent_broker.result_metadata_reader import read_archived_result_metadata
from server.app.routes.agent_worker_result_shapes import (
    header_is_v2,
    prespool_metadata,
    read_member,
)
from server.app.routes.agent_worker_results import parse_result_metadata
from shared.code_contract import (
    RESULT_METADATA_FORMAT_V2,
    RESULT_METADATA_MEMBER,
    RESULT_OUTPUT_ARTIFACTS_MEMBER,
)
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

# 纯 parse / reader 层测试不触库（与 test_agent_worker_result_metadata.py 的
# per-test no_db 纪律一致）；路由级测试走真实 app + 数据库。
_parse_only = pytest.mark.no_db

_HASH = "a" * 64


def _v2_archive(metadata: dict | bytes | None) -> bytes:
    """结果归档：可选携带 v2 元数据成员 result.json（UTF-8 JSON 文本）。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        if metadata is not None:
            payload = (
                metadata if isinstance(metadata, bytes) else json.dumps(metadata).encode("utf-8")
            )
            info = tarfile.TarInfo(RESULT_METADATA_MEMBER)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _v2_archive_with_manifest(metadata: dict, manifest: dict) -> bytes:
    """评审 P3-2 的畸形形态：归档同时带 v1 换轨清单成员与 v2 元数据成员。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, payload_obj in (
            (RESULT_OUTPUT_ARTIFACTS_MEMBER, manifest),
            (RESULT_METADATA_MEMBER, metadata),
        ):
            payload = json.dumps(payload_obj).encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(scope={"type": "http", "method": "POST", "path": "/", "headers": headers})


def _report(
    client: TestClient, token: str, claimed: dict, body: bytes, **extra_headers: str
) -> object:
    return client.post(
        f"/api/agent-executions/{claimed['execution_id']}/result",
        headers={
            "X-Agent-Worker-Token": token,
            "X-Agent-Lease-Id": claimed["lease_id"],
            **extra_headers,
        },
        content=body,
    )


def _outcome_row(app, execution_id: str) -> tuple[str, dict]:
    with app.state.job_db.connect() as conn:
        row = conn.execute(
            "select state, outcome_json from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row is not None
    return str(row["state"]), json.loads(row["outcome_json"])


def _bundle_dir_clean(app) -> bool:
    bundle_dir = Path(app.state.agent_broker.bundle_dir)
    return not list(bundle_dir.glob(".result-*")) and not list(bundle_dir.glob("*.result.tar.gz"))


# --- reader：归档成员读回面 -------------------------------------------------


@_parse_only
def test_reader_returns_member_text_and_parses_like_v1(tmp_path: Path) -> None:
    """v2 读回面 + 同一校验链：同一份 metadata 经 v1 头文本与 v2 归档成员
    两条通道解析，outcome/record 逐字段等价（双形态同校验的结构性证明）。"""
    metadata = {
        "status": "failed",
        "exit_code": 3,
        "error_message": "Agent process exited 3: 任务执行失败",
        "command": ["pi", "--provider", "gateway", "--require-output"] * 30,  # 超段数上限
        "output_artifacts": {"out.json": f"sha256:{_HASH}"},
        "run_dir": "runs/node_a/worker",
        "agent_stderr_tail": "Traceback … ValueError: boom",
    }
    from shared.code_contract import MAX_RESULT_COMMAND_PARTS

    archive = tmp_path / "result.tar.gz"
    archive.write_bytes(_v2_archive(metadata))
    v2_outcome, v2_record = parse_result_metadata(read_archived_result_metadata(archive))
    v1_outcome, v1_record = parse_result_metadata(json.dumps(metadata, ensure_ascii=False))
    assert v2_outcome == v1_outcome
    assert v2_record == v1_record
    assert v2_outcome.error_message == "Agent process exited 3: 任务执行失败"
    # command 段数超限：两形态同一截断（保前缀，Host 防御性口径）。
    assert v2_outcome.command == tuple(metadata["command"][:MAX_RESULT_COMMAND_PARTS])


@_parse_only
def test_reader_missing_member_raises(tmp_path: Path) -> None:
    archive = tmp_path / "empty.tar.gz"
    archive.write_bytes(_empty_archive())
    with pytest.raises(ValueError, match="missing"):
        read_archived_result_metadata(archive)


@_parse_only
def test_reader_non_json_member_raises(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    archive.write_bytes(_v2_archive(b"not-json{"))
    with pytest.raises(ValueError, match="not valid JSON"):
        read_archived_result_metadata(archive)


@_parse_only
def test_reader_non_utf8_member_raises(tmp_path: Path) -> None:
    archive = tmp_path / "bad-enc.tar.gz"
    archive.write_bytes(_v2_archive(b"\xff\xfe\xfa"))
    with pytest.raises(ValueError, match="not valid UTF-8"):
        read_archived_result_metadata(archive)


@_parse_only
def test_reader_oversized_member_rejected(tmp_path: Path) -> None:
    """超 1 MiB 上限的成员是畸形归档：拒绝而非截断（半截 JSON 无法解析）。"""
    archive = tmp_path / "huge.tar.gz"
    archive.write_bytes(_v2_archive(b"x" * (1024 * 1024 + 1)))
    with pytest.raises(ValueError, match="too large"):
        read_archived_result_metadata(archive)


@_parse_only
def test_reader_directory_member_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(RESULT_METADATA_MEMBER)
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
    archive = tmp_path / "dir.tar.gz"
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(ValueError, match="not a file"):
        read_archived_result_metadata(archive)


@_parse_only
def test_reader_corrupt_archive_converted_to_value_error(tmp_path: Path) -> None:
    """坏归档（非 gzip/tar）转 ValueError → 路由 400：v2 的契约违约判决
    （承诺的 result.json 成员不可读），对齐 v1 头形态非法 JSON 的 4xx 语义。
    v1 形态的毒归档不经 400——completion 层的解包宽捕获把它转成诚实判败
    （204，failed + "failed to unpack Agent result: …"，租约终结、无重跑）。"""
    archive = tmp_path / "garbage.bin"
    archive.write_bytes(b"not-a-gzip-at-all")
    with pytest.raises(ValueError, match="unreadable"):
        read_archived_result_metadata(archive)


# --- 形态分派 / v1 保持 ----------------------------------------------------


@_parse_only
@pytest.mark.parametrize(
    ("value", "expected"),
    [("2", True), ("1", False), ("v2", False), ("0", False), ("", False), (None, False)],
)
def test_header_shape_detection(value: str | None, expected: bool) -> None:
    """只有精确值 ``2`` 才是 v2；其余（含缺席）一律 v1——旧 Worker 不发
    format 头，行为不变。"""
    headers = [] if value is None else [(b"x-agent-result-format", value.encode())]
    assert header_is_v2(_request(headers)) is expected


@_parse_only
def test_prespool_v1_invalid_header_is_400_with_legacy_detail() -> None:
    """v1 头非法 JSON：400 + 现行 detail（零行为变化，含 detail 逐字保留）。"""
    request = _request([(b"x-agent-result", b"not-json")])
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        prespool_metadata(request)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "invalid Agent result metadata"


@_parse_only
def test_prespool_v2_returns_stub_record() -> None:
    """v2 在 spool 前只回占位（outcome 不存在、record 供 precheck 审计）。"""
    metadata_v2, outcome, record = prespool_metadata(_request([(b"x-agent-result-format", b"2")]))
    assert metadata_v2 is True
    assert outcome is None
    assert record == {"status": None}


@_parse_only
def test_prespool_v1_parses_header_in_place() -> None:
    request = _request(
        [(b"x-agent-result", json.dumps({"status": "failed", "exit_code": 1}).encode())]
    )
    metadata_v2, outcome, record = prespool_metadata(request)
    assert metadata_v2 is False
    assert outcome.status == "failed"
    assert record["exit_code"] == 1


# --- 路由级：v2 roundtrip / 异常 / 大 metadata -----------------------------


def test_v2_roundtrip_commits_and_stores_metadata(tmp_path: Path) -> None:
    """v2 roundtrip：带 result.json 成员的归档 + format 头 → 204 落库，
    outcome_json 的元数据（CJK error_message / stderr tail / command /
    run_dir）与 v1 头路径等价，判定链收同一 record。"""
    tail = "追踪" * 300  # CJK tail，非 ASCII 面原样
    metadata = {
        "status": "failed",
        "exit_code": 3,
        "error_message": "Agent process exited 3: 任务执行失败",
        "command": ["pi", "--provider", "gateway"],
        "output_artifacts": {},
        "run_dir": "runs/node_a/worker",
        "agent_stderr_tail": tail,
    }
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-roundtrip", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(metadata),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 204, response.text
    assert _bundle_dir_clean(app)
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    assert stored["status"] == "failed"
    assert stored["exit_code"] == 3
    assert stored["error_message"] == "Agent process exited 3: 任务执行失败"
    assert stored["agent_stderr_tail"] == tail
    assert stored["run_dir"] == "runs/node_a/worker"


def test_v2_ignores_stale_x_agent_result_header(tmp_path: Path) -> None:
    """v2 下头里的 X-Agent-Result（若仍出现）忽略——权威在归档成员：v1 语义
    里这份非法 JSON 头会 400，v2 形态下原样通过。"""
    metadata = {"status": "completed", "exit_code": 0, "command": [], "output_artifacts": {}}
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-ignore-header", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(metadata),
            **{
                "X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2,
                "X-Agent-Result": "not-json",
            },
        )
        assert response.status_code == 204, response.text
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    assert stored["status"] == "completed"


def test_v2_missing_member_is_400_and_keeps_claim_alive(tmp_path: Path) -> None:
    """v2 标记在但 result.json 缺失 → 400（detail 可定位）；staging 文件回
    收、claim 存活——同请求重投合法形态成功（400 不进 4xx 终态丢弃语义，
    因为它是同一 Worker 的可修正输错）。"""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-missing", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _empty_archive(),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 400, response.text
        assert "invalid Agent result metadata" in response.json()["detail"]
        assert _bundle_dir_clean(app)
        retry = _report(
            client,
            token,
            claimed,
            _v2_archive({"status": "failed", "exit_code": 1, "command": []}),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert retry.status_code == 204, retry.text
    state, _ = _outcome_row(app, claimed["execution_id"])
    assert state == "done"


def test_v2_non_json_member_is_400(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-badjson", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(b"not-json{"),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 400, response.text
        assert "not valid JSON" in response.json()["detail"]
    assert _bundle_dir_clean(app)


def test_v2_corrupt_archive_is_400_not_500(tmp_path: Path) -> None:
    """坏归档（解不开的 tar.gz）→ 400 而非 500：staging 回收、claim 存活。"""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-corrupt", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            b"not-a-gzip-at-all",
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 400, response.text
        assert "unreadable" in response.json()["detail"]
        assert _bundle_dir_clean(app)


def test_v2_non_dict_member_is_400(tmp_path: Path) -> None:
    """成员是合法 JSON 但非对象：parse 链「metadata must be a JSON object」
    拒收 → 400（与 v1 头非 dict 的语义一致）。"""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-nondict", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(b"[1, 2]"),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 400, response.text
        assert "JSON object" in response.json()["detail"]
    assert _bundle_dir_clean(app)


def test_v2_invalid_metadata_field_is_400(tmp_path: Path) -> None:
    """成员 JSON 合法但字段非法（status 越界）：同一 parse 链拒收 → 400。"""
    metadata = {"status": "exploded", "exit_code": 1, "command": [], "output_artifacts": {}}
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-badfield", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(metadata),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 400, response.text
    assert _bundle_dir_clean(app)


def test_v2_large_metadata_over_header_budget_passes(tmp_path: Path) -> None:
    """本 PR 的存在意义：16KiB+ 的 metadata 经 v2 正常通过（头预算不复
    存在）。构造 128 条直传 dict ref 清单（~25KB 序列化）——v1 头形态下
    该载荷撞破 14KiB 头预算（#748/#755 降级链的存在理由），v2 形态 204
    落库、清单全量入库、无截断标记（PR-2 起 Worker 写侧同样直收：清单
    整体留在 result.json，v1 序列化器已退役）。"""
    artifacts = {
        f"out-{i:03d}.json": {
            "storage_key": f"jobs-staging/ws-1/job-1/exec-1/out-{i:03d}.json",
            "size_bytes": 3,
            "content_hash": _HASH,
        }
        for i in range(128)
    }
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "任务完成",
        "command": ["pi"],
        "output_artifacts": artifacts,
        "run_dir": "runs/node_a/worker",
    }
    serialized = json.dumps(metadata, ensure_ascii=False)
    assert len(serialized.encode("utf-8")) > 16 * 1024  # 真超头预算（h11 事件上限）

    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-large", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(metadata),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 204, response.text
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    kept = stored["output_artifacts"]
    assert len(kept) == 128
    assert list(kept) == [f"out-{i:03d}.json" for i in range(128)]
    assert kept["out-000.json"]["storage_key"].startswith("jobs-staging/")
    assert stored["output_artifacts_truncated"] is False
    assert stored["output_artifacts_in_archive"] is False


def test_v2_truncation_semantics_match_v1_chain(tmp_path: Path) -> None:
    """两形态同校验：v2 载荷经同一 parse 链的防御性截断——command 段数超
    限保前缀、agent_stderr_tail 超限保尾、error_message 超限保头，与 v1
    头路径的截断口径一致（v1 侧已有 parse 测试钉住口径，此处钉 v2 路由
    落库形态）。"""
    argv = ["/usr/bin/velites", "run"] + ["--require-output"] * 80
    metadata = {
        "status": "failed",
        "exit_code": 1,
        "error_message": "e" * 6000,
        "command": argv,
        "output_artifacts": {},
        "agent_stderr_tail": "h" * 1000 + "y" * 4000,
    }
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-truncate", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(metadata),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 204, response.text
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    # command 只进 AgentOutcome（node_runs 观测面），不进 record——其两形态
    # 同截断由 reader 等价测试钉住；此处钉 record 面的三个截断字段。
    assert stored["error_message"] == "e" * 4000
    assert stored["agent_stderr_tail"] == "y" * 4000


def test_v2_over_cap_artifact_manifest_rejected_like_v1(tmp_path: Path) -> None:
    """两形态同校验：129 条产物清单超条目上限 → 400（形态错误拒收，不是
    截断面），与 v1 parse 链口径一致。"""
    artifacts = {f"out-{i:03d}.json": f"sha256:{_HASH}" for i in range(129)}
    metadata = {"status": "completed", "exit_code": 0, "command": [], "output_artifacts": artifacts}
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-overflow-list", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(metadata),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 400, response.text
        assert "invalid Agent result metadata" in response.json()["detail"]
    assert _bundle_dir_clean(app)


# --- v1 换轨标记在 v2 形态下显式忽略（#843 评审 P3-2）----------------------


def _seed_artifact_hash(app, digest: str) -> None:
    """CAS ref 的登记前置：artifact_refs.hash 外键引用 artifacts(hash)，路由
    级 completed + 字符串 ref 会经 register_reported_output_refs 登记引用。"""
    with app.state.job_db.connect() as conn:
        conn.execute(
            "insert into artifacts(hash, size) values (%s, 1) on conflict(hash) do nothing",
            (digest,),
        )


@_parse_only
def test_read_member_strips_v1_switch_track_flag(tmp_path: Path) -> None:
    """read_member 剥离 v1 换轨标记：payload 带旗标解析后 record 旗标为
    False——commit 层不会激活换轨（转读 result-output-artifacts.json /
    替换 output_artifacts），output_artifacts 按 result.json 原值保留。"""
    import asyncio

    metadata = {
        "status": "completed",
        "exit_code": 0,
        "command": [],
        "output_artifacts": {"out.json": f"sha256:{_HASH}"},
        "output_artifacts_in_archive": True,
    }
    archive = tmp_path / "flagged.tar.gz"
    archive.write_bytes(_v2_archive(metadata))
    outcome, record = asyncio.run(read_member(archive))
    assert record["output_artifacts_in_archive"] is False
    assert outcome.output_artifacts == {"out.json": f"sha256:{_HASH}"}


def test_v2_switch_track_flag_without_manifest_member_is_ignored(tmp_path: Path) -> None:
    """评审 P3-2 交互钉子：v2 payload 带 output_artifacts_in_archive=true 且
    归档无清单成员 → 204、completed 不翻转、output_artifacts 按 result.json
    原值入库。未剥离时的行为：commit 层见旗标转读清单 →「manifest member
    is missing」→ completed 诚实翻 failed。"""
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "command": [],
        "output_artifacts": {"out.json": f"sha256:{_HASH}"},
        "output_artifacts_in_archive": True,
    }
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-flag-nomember", limit=2)
    _seed_artifact_hash(app, _HASH)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive(metadata),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 204, response.text
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    assert stored["status"] == "completed"
    assert stored["output_artifacts"] == {"out.json": f"sha256:{_HASH}"}
    assert stored["output_artifacts_in_archive"] is False


def test_v2_switch_track_flag_with_manifest_member_keeps_result_json_values(
    tmp_path: Path,
) -> None:
    """评审 P3-2 交互钉子：v2 payload 带旗标且归档也带清单成员 → 仍以
    result.json 原值判定 output_artifacts（未剥离时 commit 层会用清单成员
    内容替换），completed 不翻转——两通道在 v2 形态下不串扰。"""
    other_hash = "b" * 64
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "command": [],
        "output_artifacts": {"out.json": f"sha256:{_HASH}"},
        "output_artifacts_in_archive": True,
    }
    manifest = {"evil.json": f"sha256:{other_hash}"}
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-flag-member", limit=2)
    _seed_artifact_hash(app, _HASH)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _v2_archive_with_manifest(metadata, manifest),
            **{"X-Agent-Result-Format": RESULT_METADATA_FORMAT_V2},
        )
        assert response.status_code == 204, response.text
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    assert stored["status"] == "completed"
    # result.json 原值获胜——清单成员内容不得替换 output_artifacts。
    assert stored["output_artifacts"] == {"out.json": f"sha256:{_HASH}"}
    assert "evil.json" not in stored["output_artifacts"]
    assert stored["output_artifacts_in_archive"] is False


# --- v1 回归钉子（零行为变化）----------------------------------------------


def test_v1_without_format_header_still_takes_header_path(tmp_path: Path) -> None:
    """无 format 头 → 现行 v1 头路径：原样 JSON 头 204 落库（既有测试已全
    覆盖头形态细节，此处钉「无标记仍走 v1」这一分派事实）。"""
    metadata = {
        "status": "failed",
        "exit_code": 1,
        "error_message": "Agent process exited 1",
        "command": [],
        "output_artifacts": {},
    }
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v1-plain", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client, token, claimed, _empty_archive(), **{"X-Agent-Result": json.dumps(metadata)}
        )
        assert response.status_code == 204, response.text
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    assert stored["error_message"] == "Agent process exited 1"


def test_v1_non_2_format_value_treated_as_v1(tmp_path: Path) -> None:
    """format 头存在但值非 2 → 按 v1 头路径处理（值非 2 不是 v2 标记）。"""
    metadata = {"status": "failed", "exit_code": 1, "command": [], "output_artifacts": {}}
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v1-fmt1", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client,
            token,
            claimed,
            _empty_archive(),
            **{"X-Agent-Result-Format": "1", "X-Agent-Result": json.dumps(metadata)},
        )
        assert response.status_code == 204, response.text
    state, _ = _outcome_row(app, claimed["execution_id"])
    assert state == "done"


def test_v1_invalid_header_still_400_before_spool(tmp_path: Path) -> None:
    """v1 头非法 JSON 的 400 先行次序保持：不落盘（无 staging 残留）、
    claim 存活可重投。"""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v1-badheader", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = _report(
            client, token, claimed, _empty_archive(), **{"X-Agent-Result": "not-json"}
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid Agent result metadata"
        assert _bundle_dir_clean(app)
        retry = _report(
            client,
            token,
            claimed,
            _empty_archive(),
            **{"X-Agent-Result": json.dumps({"status": "failed", "exit_code": 1})},
        )
        assert retry.status_code == 204, retry.text
