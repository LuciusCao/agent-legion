"""quality_artifact_contents：对象存储分支限量读流（不整对象读进内存）。

数 GB 级产物只允许读出 ``_ARTIFACT_CONTENT_LIMIT + 1`` 字节判定截断，
流必须关闭；无参全量 ``read()`` 是回归（直接断言失败）。
"""

from __future__ import annotations

from typing import Any

import pytest

from server.app.services.quality_artifact_contents import (
    _ARTIFACT_CONTENT_LIMIT,
    artifact_contents,
)

pytestmark = pytest.mark.no_db


class _RecordingStream:
    """记录 read 的 amt 参数；无参/负值全量 read 直接失败。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.read_amounts: list[int] = []
        self.closed = False

    def read(self, amt: int = -1) -> bytes:
        if amt is None or amt < 0:
            raise AssertionError("unbounded read() on artifact stream")
        self.read_amounts.append(amt)
        return self._payload[:amt]

    def close(self) -> None:
        self.closed = True


class _FakeObjectStore:
    """JobArtifactObjectStore 的最小替身（enabled/rows_for_job/open_stream）。"""

    def __init__(self, payload: bytes) -> None:
        self.enabled = True
        self.stream = _RecordingStream(payload)

    def rows_for_job(self, job_id: str) -> list[dict[str, Any]]:
        return [
            {
                "node_key": "node-a",
                "name": "out.json",
                "storage_key": "jobs/ws/job-1/out.json",
            }
        ]

    def open_stream(self, row: dict[str, Any]) -> _RecordingStream:
        return self.stream


def test_object_branch_reads_bounded_and_closes_stream() -> None:
    payload = b"x" * (_ARTIFACT_CONTENT_LIMIT + 100)
    store = _FakeObjectStore(payload)

    contents = artifact_contents(None, "job-1", "node-a", object_store=store)

    assert store.stream.read_amounts == [_ARTIFACT_CONTENT_LIMIT + 1]  # 限量读
    assert store.stream.closed
    (entry,) = contents
    assert entry["name"] == "out.json"
    assert entry["truncated"] is True
    assert entry["content"] == payload[:_ARTIFACT_CONTENT_LIMIT].decode()


def test_object_branch_small_object_not_truncated() -> None:
    store = _FakeObjectStore(b'{"ok": true}')

    contents = artifact_contents(None, "job-1", "node-a", object_store=store)

    (entry,) = contents
    assert entry["truncated"] is False
    assert entry["content"] == '{"ok": true}'
    assert store.stream.closed
