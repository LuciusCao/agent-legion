"""Unit tests for pending-marker ownership checks (worker/execution/pending.py).

#203 P1：仅凭 marker 存在就跳过 claim 会让旧 lease 的孤儿 marker 耗尽当前
重试次数（claim 每次 attempt+1，sweeper 超过 requeue_limit 不再重排）。
ownership 核对的全部边界在这里钉住；两条 prepare 路径的集成行为分别在
tests/workers/test_worker_execution_prepare.py 与 tests/workers/test_code_runner.py。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worker.execution.pending import refuse_if_pending_upload
from worker.upload.queue import PENDING_FILENAME, PendingUploadExists

pytestmark = pytest.mark.no_db


def _claim(lease_id: str = "lease-1") -> dict:
    return {"execution_id": "exec-1", "lease_id": lease_id}


def _marker(execution_dir: Path, body: str) -> None:
    execution_dir.mkdir(parents=True, exist_ok=True)
    (execution_dir / PENDING_FILENAME).write_text(body, encoding="utf-8")


def test_no_marker_no_refusal(tmp_path: Path) -> None:
    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir(parents=True)
    refuse_if_pending_upload(execution_dir, _claim())  # 不抛即通过


def test_marker_of_current_lease_refuses(tmp_path: Path) -> None:
    execution_dir = tmp_path / "exec-1"
    _marker(execution_dir, '{"version": 1, "lease_id": "lease-1"}')
    with pytest.raises(PendingUploadExists, match="lease"):
        refuse_if_pending_upload(execution_dir, _claim())


def test_marker_of_stale_lease_is_orphan(tmp_path: Path) -> None:
    execution_dir = tmp_path / "exec-1"
    _marker(execution_dir, '{"version": 1, "lease_id": "lease-old"}')
    refuse_if_pending_upload(execution_dir, _claim())  # 孤儿：不抛


def test_marker_without_lease_field_is_orphan(tmp_path: Path) -> None:
    """旧版/外部工具写的无 lease 字段 marker：无法核对所有权，按孤儿处理
    （保守方向：宁可重跑，不让 attempt 预算被无限跳过耗尽）。"""
    execution_dir = tmp_path / "exec-1"
    _marker(execution_dir, '{"version": 1, "execution_id": "exec-1"}')
    refuse_if_pending_upload(execution_dir, _claim())


def test_unreadable_marker_is_orphan(tmp_path: Path) -> None:
    """损坏 JSON 的 marker：restore() 启动时本就会丢弃它，这里同样按孤儿。"""
    execution_dir = tmp_path / "exec-1"
    _marker(execution_dir, "{truncated")
    refuse_if_pending_upload(execution_dir, _claim())


def test_claim_without_lease_id_treats_marker_as_orphan(tmp_path: Path) -> None:
    """claim 缺 lease_id（异常入参）：不因空字符串巧合匹配空的 marker lease
    而误判所有权。"""
    execution_dir = tmp_path / "exec-1"
    _marker(execution_dir, '{"version": 1, "lease_id": ""}')
    refuse_if_pending_upload(execution_dir, {"execution_id": "exec-1"})
