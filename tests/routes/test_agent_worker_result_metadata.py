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

pytestmark = pytest.mark.no_db

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


def test_legacy_string_ref_still_accepted() -> None:
    outcome, record = parse_result_metadata(_payload({"out.json": f"sha256:{_HASH}"}))
    assert outcome.output_artifacts == {"out.json": f"sha256:{_HASH}"}
    assert record["output_artifacts"] == outcome.output_artifacts


def test_remote_dict_ref_accepted() -> None:
    outcome, _ = parse_result_metadata(_payload({"out.json": dict(_REMOTE_REF)}))
    assert outcome.output_artifacts == {"out.json": _REMOTE_REF}


def test_remote_dict_ref_allows_empty_hash() -> None:
    ref = {**_REMOTE_REF, "content_hash": ""}
    outcome, _ = parse_result_metadata(_payload({"out.json": ref}))
    assert outcome.output_artifacts["out.json"] == ref


def test_mixed_ref_forms_accepted() -> None:
    outcome, _ = parse_result_metadata(
        _payload({"a.json": f"sha256:{_HASH}", "b.json": dict(_REMOTE_REF)})
    )
    assert outcome.output_artifacts["a.json"] == f"sha256:{_HASH}"
    assert outcome.output_artifacts["b.json"] == _REMOTE_REF


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


def test_artifact_count_cap_unchanged() -> None:
    artifacts = {f"out-{i}.json": f"sha256:{_HASH}" for i in range(129)}
    with pytest.raises(ValueError, match="invalid output artifacts"):
        parse_result_metadata(_payload(artifacts))
