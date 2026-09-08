"""Unit tests for scripts/submit_campaign.py (#505)——投放循环编排。

纯静态单测（no_db）：run_campaign 的水位门控、游标推进、重试退避与
幂等吸收（全重复 400）编排、dry-run。HTTP 层全部用桩替（_StubClient
覆盖 CampaignClient 的方法），不触网络不触 DB。

姊妹文件（按被测主题拆分，AGENTS.md §4 的 800 行主动拆分阈值）：
- test_submit_campaign_manifest.py —— 清单解析（normalize_item / load_items）
- test_submit_campaign_guards.py —— 水位口径与参数护栏
- test_submit_campaign_http_cli.py —— CampaignClient HTTP 层与 CLI 编排
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.submit_campaign import (  # noqa: E402
    SubmitError,
    UsageError,
    run_campaign,
)

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# 投放循环（桩 client）
# ---------------------------------------------------------------------------


class _StubClient:
    """HTTP 桩：水位序列 + 提交应答序列，记录调用。"""

    def __init__(
        self,
        job_stats_sequence: list[dict[str, int]] | None = None,
        submit_responses: list[dict] | None = None,
        submit_error: Exception | None = None,
        submit_error_times: int = 0,
        max_items_per_run: int = 20_000,
    ) -> None:
        self.max_items_per_run = max_items_per_run
        self.job_stats_sequence = job_stats_sequence or [{}]
        self.submit_responses = submit_responses or []
        self.submit_error = submit_error
        self.submit_error_times = submit_error_times
        self.submitted: list[list[dict]] = []
        self.stats_reads = 0
        self.logs: list[str] = []
        self.max_items_calls = 0

    def log(self, message: str) -> None:
        self.logs.append(message)

    def fetch_max_items_per_run(self) -> int:
        self.max_items_calls += 1
        return self.max_items_per_run

    def fetch_job_stats(self, workspace_id: str) -> dict[str, int]:
        index = min(self.stats_reads, len(self.job_stats_sequence) - 1)
        self.stats_reads += 1
        return self.job_stats_sequence[index]

    def submit_batch(self, workspace_id: str, items: list[dict]) -> dict:
        if self.submit_error_times > 0:
            self.submit_error_times -= 1
            raise self.submit_error
        self.submitted.append(list(items))
        response = (
            self.submit_responses.pop(0)
            if self.submit_responses
            else {
                "run": {"id": f"run-{len(self.submitted)}"},
                "created_count": len(items),
            }
        )
        return response


def _items(count: int) -> list[dict]:
    return [
        {"type": "ref", "connection_key": "cms", "external_id": f"Q-{index}"}
        for index in range(count)
    ]


class TestRunCampaign:
    def test_submits_all_batches_when_below_watermark(self):
        client = _StubClient(job_stats_sequence=[{"completed": 100}])
        stats = run_campaign(
            client, "ws-1", _items(12), watermark=100, batch_size=5, poll_interval=0
        )
        assert [len(batch) for batch in client.submitted] == [5, 5, 2]
        assert stats == {
            "submitted_runs": 3,
            "submitted_items": 12,
            "created_jobs": 12,
        }

    def test_watermark_gates_submission(self):
        """水位 ≥ 水位线时等待，水位下降后才投下一批。"""
        client = _StubClient(
            job_stats_sequence=[
                {"running": 50},  # 首查：水位 50 >= 50，等
                {"running": 50},  # 再查仍满，等
                {"running": 10},  # 降了，投批
                {"running": 10},  # 投完再查（水位查询在每轮循环开头）
            ]
        )
        sleeps: list[float] = []
        stats = run_campaign(
            client,
            "ws-1",
            _items(5),
            watermark=50,
            batch_size=5,
            poll_interval=2.0,
            sleep=sleeps.append,
        )
        assert len(client.submitted) == 1
        assert sleeps == [2.0, 2.0]
        assert stats["submitted_runs"] == 1

    def test_final_batch_never_polled_again(self):
        """最后一批投完即退出——不再做无谓的水位轮询。"""
        client = _StubClient(job_stats_sequence=[{}])
        run_campaign(client, "ws-1", _items(3), watermark=100, batch_size=3, poll_interval=0)
        assert client.stats_reads == 1
        assert len(client.submitted) == 1

    def test_created_counts_accumulate_from_response(self):
        client = _StubClient(
            submit_responses=[
                {"run": {"id": "run-1"}, "created_count": 5},
                {"run": {"id": "run-2"}, "created_count": 0},  # 全量重复（dedup 跳过）
                {"run": {"id": "run-3"}, "created_count": 2},
            ]
        )
        stats = run_campaign(
            client, "ws-1", _items(12), watermark=100, batch_size=5, poll_interval=0
        )
        assert stats["created_jobs"] == 7  # 重发批的 dedup 跳过不重复计数
        assert stats["submitted_items"] == 12

    def test_batch_size_clamped_to_remaining(self):
        client = _StubClient(job_stats_sequence=[{}])
        run_campaign(client, "ws-1", _items(3), watermark=100, batch_size=5000, poll_interval=0)
        assert [len(batch) for batch in client.submitted] == [3]

    def test_retry_resubmits_same_batch_then_advances(self):
        """失败重试重发同一批（游标未动），成功后前进——幂等续投的编排。"""
        client = _StubClient(
            submit_error=SubmitError("HTTP 500: boom", transient=True), submit_error_times=2
        )
        sleeps: list[float] = []
        stats = run_campaign(
            client,
            "ws-1",
            _items(4),
            watermark=100,
            batch_size=4,
            retry_wait=3.0,
            poll_interval=0,
            sleep=sleeps.append,
        )
        # 2 次失败 + 1 次成功 = 3 次提交调用，但 recorded submitted 只含成功的 1 批
        assert client.submit_error_times == 0
        assert len(client.submitted) == 1
        # 线性退避：3s、6s
        assert sleeps == [3.0, 6.0]
        assert stats["submitted_runs"] == 1

    def test_retry_max_exhausted_raises_with_cursor(self):
        client = _StubClient(
            submit_error=SubmitError("HTTP 500: boom", transient=True), submit_error_times=99
        )
        with pytest.raises(SubmitError, match=r"items\[0:4\]"):
            run_campaign(
                client,
                "ws-1",
                _items(4),
                watermark=100,
                batch_size=4,
                retry_wait=0,
                retry_max=2,
                poll_interval=0,
                sleep=lambda _s: None,
            )

    def test_retry_max_zero_means_infinite(self):
        client = _StubClient(
            submit_error=SubmitError("HTTP 500: boom", transient=True), submit_error_times=5
        )
        stats = run_campaign(
            client,
            "ws-1",
            _items(2),
            watermark=100,
            batch_size=2,
            retry_wait=0,
            retry_max=0,
            poll_interval=0,
            sleep=lambda _s: None,
        )
        assert stats["submitted_runs"] == 1

    def test_backoff_capped_at_sixty_seconds(self):
        """线性退避有 60s 上限：无限重试模式下不累积出刻钟级单次等待。"""
        client = _StubClient(
            submit_error=SubmitError("HTTP 500: boom", transient=True), submit_error_times=14
        )
        sleeps: list[float] = []
        run_campaign(
            client,
            "ws-1",
            _items(4),
            watermark=100,
            batch_size=4,
            retry_wait=10.0,
            poll_interval=0,
            sleep=sleeps.append,
        )
        # 10s 基数线性放大：10、20、...、60 后封顶不再增长。
        assert sleeps == [
            10.0,
            20.0,
            30.0,
            40.0,
            50.0,
            60.0,
            60.0,
            60.0,
            60.0,
            60.0,
            60.0,
            60.0,
            60.0,
            60.0,
        ]

    def test_deterministic_4xx_not_retried(self):
        """401/403/422 等确定性 4xx 重试不会改变结果——立即失败退出。"""
        for error in (
            SubmitError("HTTP 401: 未认证"),
            SubmitError("HTTP 403: 无权限"),
            SubmitError("HTTP 422: exceed the per-run limit"),
            SubmitError("HTTP 400: 其他 400"),
        ):
            client = _StubClient(submit_error=error, submit_error_times=99)
            sleeps: list[float] = []
            with pytest.raises(SubmitError, match="不可恢复失败"):
                run_campaign(
                    client,
                    "ws-1",
                    _items(4),
                    watermark=100,
                    batch_size=4,
                    retry_wait=0,
                    poll_interval=0,
                    sleep=sleeps.append,
                )
            assert sleeps == []  # 不进退避
            assert client.submitted == []

    def test_transient_5xx_retried(self):
        """5xx 是瞬态错误，照常进退避重试。"""
        client = _StubClient(
            submit_error=SubmitError("HTTP 503: boom", transient=True), submit_error_times=2
        )
        sleeps: list[float] = []
        stats = run_campaign(
            client,
            "ws-1",
            _items(4),
            watermark=100,
            batch_size=4,
            retry_wait=2.0,
            poll_interval=0,
            sleep=sleeps.append,
        )
        assert sleeps == [2.0, 4.0]
        assert stats["submitted_runs"] == 1

    def test_non_submit_error_retried_as_transient(self):
        """网络层异常（ConnectionError 等非 SubmitError）按瞬态重试。"""
        client = _StubClient(submit_error=ConnectionError("reset by peer"), submit_error_times=1)
        stats = run_campaign(
            client,
            "ws-1",
            _items(4),
            watermark=100,
            batch_size=4,
            retry_wait=0,
            poll_interval=0,
            sleep=lambda _s: None,
        )
        assert stats["submitted_runs"] == 1

    def test_rerun_of_succeeded_batch_absorbed_and_cursor_advances(self):
        """P1-1 核心验收：重跑已成功批（第 1 批全量重复）被吸收后游标推进，
        后续批次照常投放——而不是对 400 无限重试。

        场景：崩溃前第 1 批已成功建 job，重跑时第 1 批撞服务端「全重复
        且 run 非 failed」的 400 "No tasks were resolved"；客户端把它转
        成 created_count=0 的成功应答，循环继续第 2 批。
        """
        client = _StubClient(
            submit_responses=[
                {"run": None, "created_count": 0, "job_ids": []},  # 第 1 批：吸收
                {"run": {"id": "run-2"}, "created_count": 5},  # 第 2 批：正常
            ]
        )
        sleeps: list[float] = []
        stats = run_campaign(
            client,
            "ws-1",
            _items(10),
            watermark=100,
            batch_size=5,
            retry_wait=0,
            poll_interval=0,
            sleep=sleeps.append,
        )
        assert [len(batch) for batch in client.submitted] == [5, 5]
        assert stats == {
            "submitted_runs": 2,
            "submitted_items": 10,
            "created_jobs": 5,  # 吸收批不重复计数
        }
        assert sleeps == []  # 吸收不进重试
        assert any("全重复 400" in line for line in client.logs)

    def test_guards_run_before_first_request(self):
        """批大小超 run 上限在校验期失败，不产生任何提交。"""
        client = _StubClient(max_items_per_run=20_000)
        with pytest.raises(UsageError, match="max_items_per_run"):
            run_campaign(
                client, "ws-1", _items(10), watermark=100, batch_size=30_000, poll_interval=0
            )
        assert client.submitted == []

    def test_dry_run_prints_batches_without_submitting(self):
        client = _StubClient(job_stats_sequence=[{}])
        stats = run_campaign(
            client, "ws-1", _items(12), watermark=100, batch_size=5, poll_interval=0, dry_run=True
        )
        assert client.submitted == []
        assert stats["submitted_runs"] == 3
        assert stats["submitted_items"] == 12
        assert stats["created_jobs"] == 0
        assert any("[dry-run] 将提交第 1 批" in line for line in client.logs)
