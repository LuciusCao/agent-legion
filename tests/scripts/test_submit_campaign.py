"""Unit tests for scripts/submit_campaign.py (#505 drip-feed submitter).

纯静态单测（no_db）：item 规整 / 清单加载 / 水位口径 / 批大小护栏 /
投放循环编排（水位门控、游标推进、重试、dry-run）。HTTP 层全部用桩
替（CampaignClient 的方法覆盖），不触网络不触 DB。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.submit_campaign import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    DEFAULT_WATERMARK,
    SubmitError,
    UsageError,
    check_batch_size,
    check_watermark,
    load_items,
    non_terminal_count,
    normalize_item,
    run_campaign,
)

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# normalize_item
# ---------------------------------------------------------------------------


class TestNormalizeItem:
    def test_material_item_passes_through(self):
        item = normalize_item({"type": "material", "material_id": "mat-1"}, source="x")
        assert item == {"type": "material", "material_id": "mat-1"}

    def test_ref_item_gets_default_params(self):
        item = normalize_item(
            {"type": "ref", "connection_key": "cms", "external_id": "Q-1"}, source="x"
        )
        assert item["params"] == {}
        assert item["connection_key"] == "cms"
        assert item["external_id"] == "Q-1"

    def test_ref_item_keeps_explicit_params(self):
        item = normalize_item(
            {
                "type": "ref",
                "connection_key": "cms",
                "external_id": "Q-1",
                "params": {"lang": "zh"},
            },
            source="x",
        )
        assert item["params"] == {"lang": "zh"}

    def test_bundle_item(self):
        item = normalize_item({"type": "bundle", "bundle_id": "b-1"}, source="x")
        assert item == {"type": "bundle", "bundle_id": "b-1"}

    def test_unknown_type_rejected(self):
        with pytest.raises(UsageError, match="不支持的 item type"):
            normalize_item({"type": "video"}, source="x")

    def test_missing_type_rejected(self):
        with pytest.raises(UsageError, match="不支持的 item type"):
            normalize_item({"material_id": "mat-1"}, source="x")

    @pytest.mark.parametrize(
        ("item", "field"),
        [
            ({"type": "material"}, "material_id"),
            ({"type": "material", "material_id": "  "}, "material_id"),
            ({"type": "bundle"}, "bundle_id"),
            ({"type": "ref", "external_id": "Q-1"}, "connection_key"),
            ({"type": "ref", "connection_key": "cms"}, "external_id"),
        ],
    )
    def test_missing_required_field_rejected(self, item, field):
        with pytest.raises(UsageError, match=field):
            normalize_item(item, source="x")

    def test_string_values_are_stripped(self):
        item = normalize_item(
            {"type": "ref", "connection_key": " cms ", "external_id": " Q-1 "}, source="x"
        )
        assert item["connection_key"] == "cms"
        assert item["external_id"] == "Q-1"

    def test_non_object_rejected(self):
        with pytest.raises(UsageError, match="JSON object"):
            normalize_item(["material"], source="x")

    def test_error_message_carries_source(self):
        with pytest.raises(UsageError, match="list.jsonl:3"):
            normalize_item({"type": "video"}, source="list.jsonl:3")


# ---------------------------------------------------------------------------
# load_items
# ---------------------------------------------------------------------------


class TestLoadItems:
    def test_jsonl_loads_in_order(self, tmp_path: Path):
        path = tmp_path / "campaign.jsonl"
        path.write_text(
            '{"type": "material", "material_id": "m-1"}\n'
            "# 注释行跳过\n"
            "\n"
            '{"type": "ref", "connection_key": "cms", "external_id": "Q-1"}\n',
            encoding="utf-8",
        )
        items = load_items(path)
        assert [item["type"] for item in items] == ["material", "ref"]
        assert items[1]["params"] == {}

    def test_jsonl_invalid_json_reports_line(self, tmp_path: Path):
        path = tmp_path / "campaign.jsonl"
        path.write_text('{"type": "material", "material_id": "m-1"}\n{oops\n', encoding="utf-8")
        with pytest.raises(UsageError, match="campaign.jsonl:2"):
            load_items(path)

    def test_csv_loads_rows(self, tmp_path: Path):
        path = tmp_path / "campaign.csv"
        path.write_text("type,material_id\nmaterial,m-1\nmaterial,m-2\n", encoding="utf-8")
        items = load_items(path)
        # csv 每行变成 {列头: 值}，走 normalize_item 规整（str 化 + 必填校验）
        assert [item["material_id"] for item in items] == ["m-1", "m-2"]
        assert all(item["type"] == "material" for item in items)

    def test_csv_ref_item_gets_params(self, tmp_path: Path):
        path = tmp_path / "campaign.csv"
        path.write_text("type,connection_key,external_id\nref,cms,Q-1\n", encoding="utf-8")
        items = load_items(path)
        assert items[0]["params"] == {}

    def test_csv_missing_field_reports_row(self, tmp_path: Path):
        path = tmp_path / "campaign.csv"
        path.write_text("type,material_id\nmaterial,\n", encoding="utf-8")
        with pytest.raises(UsageError, match="campaign.csv:2"):
            load_items(path)

    def test_csv_blank_rows_skipped(self, tmp_path: Path):
        path = tmp_path / "campaign.csv"
        path.write_text("type,material_id\n,\nmaterial,m-1\n", encoding="utf-8")
        items = load_items(path)
        assert len(items) == 1

    def test_missing_file_rejected(self, tmp_path: Path):
        with pytest.raises(UsageError, match="清单文件不存在"):
            load_items(tmp_path / "nope.jsonl")

    def test_empty_list_rejected(self, tmp_path: Path):
        path = tmp_path / "campaign.jsonl"
        path.write_text("# 只有注释\n", encoding="utf-8")
        with pytest.raises(UsageError, match="没有可用 item"):
            load_items(path)

    def test_hundred_thousand_items_load(self, tmp_path: Path):
        """几十万级清单的加载冒烟（issue 的目标规模）。"""
        path = tmp_path / "big.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for index in range(100_000):
                fh.write(
                    json.dumps(
                        {"type": "ref", "connection_key": "cms", "external_id": f"Q-{index}"}
                    )
                    + "\n"
                )
        items = load_items(path)
        assert len(items) == 100_000
        assert items[0]["external_id"] == "Q-0"
        assert items[-1]["external_id"] == "Q-99999"


# ---------------------------------------------------------------------------
# non_terminal_count（水位口径）
# ---------------------------------------------------------------------------


class TestNonTerminalCount:
    def test_terminal_statuses_excluded(self):
        stats = {"completed": 1000, "failed": 50, "running": 200, "pending": 500}
        assert non_terminal_count(stats) == 700

    def test_paused_and_awaiting_approval_counted(self):
        """宽口径：paused / awaiting_approval 占着非终态集合，计入水位。"""
        stats = {"completed": 10, "paused": 5, "awaiting_approval": 7}
        assert non_terminal_count(stats) == 12

    def test_unknown_statuses_counted(self):
        """未知状态按非终态计（保守：宁可少投不可超红线的集合口径）。"""
        assert non_terminal_count({"weird": 3, "completed": 1}) == 3

    def test_empty_stats(self):
        assert non_terminal_count({}) == 0


# ---------------------------------------------------------------------------
# 参数护栏
# ---------------------------------------------------------------------------


class TestGuards:
    def test_batch_size_over_run_limit_rejected(self):
        with pytest.raises(UsageError, match="max_items_per_run"):
            check_batch_size(20_001, 20_000)

    def test_batch_size_at_run_limit_ok(self):
        check_batch_size(20_000, 20_000)

    def test_batch_size_zero_rejected(self):
        with pytest.raises(UsageError, match="batch-size"):
            check_batch_size(0, 20_000)

    def test_run_limit_zero_disables_check(self):
        """max_items_per_run=0 = 关闭护栏（#358 语义），批大小不拦。"""
        check_batch_size(99_999, 0)

    def test_watermark_below_batch_rejected(self):
        with pytest.raises(UsageError, match="水位永远补不进一批"):
            check_watermark(4_999, 5_000)

    def test_watermark_at_batch_size_ok(self):
        check_watermark(DEFAULT_WATERMARK, DEFAULT_BATCH_SIZE)

    def test_watermark_zero_rejected(self):
        with pytest.raises(UsageError, match="watermark"):
            check_watermark(0, 20_000)


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


# ---------------------------------------------------------------------------
# CLI：登录与 dry-run 编排（mock requests）
# ---------------------------------------------------------------------------


class TestMainDryRun:
    def _write_items(self, tmp_path: Path) -> Path:
        path = tmp_path / "campaign.jsonl"
        path.write_text(
            "\n".join(
                json.dumps({"type": "ref", "connection_key": "cms", "external_id": f"Q-{i}"})
                for i in range(7)
            ),
            encoding="utf-8",
        )
        return path

    def test_dry_run_skips_login(self, tmp_path, capsys):
        from scripts import submit_campaign

        items_path = self._write_items(tmp_path)
        with mock.patch.object(submit_campaign, "requests", create=True) as requests_mock:
            # requests.Session 不应被构造（dry-run 不登录）
            requests_mock.Session.side_effect = AssertionError("dry-run must not login")
            code = submit_campaign.main(
                [
                    "--username",
                    "admin",
                    "--password",
                    "pw",
                    "--workspace-id",
                    "ws-1",
                    "--items",
                    str(items_path),
                    "--batch-size",
                    "3",
                    "--dry-run",
                ]
            )
        assert code == 0
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "3 批" in out

    def test_usage_error_raised_from_main(self, tmp_path):
        """main 内部抛 UsageError（exit code 2 的映射在 __main__ 块）。"""
        from scripts import submit_campaign

        with pytest.raises(UsageError, match="清单文件不存在"):
            submit_campaign.main(
                [
                    "--username",
                    "admin",
                    "--password",
                    "pw",
                    "--workspace-id",
                    "ws-1",
                    "--items",
                    str(tmp_path / "missing.jsonl"),
                ]
            )

    def test_main_dry_run_with_small_watermark_guard(self, tmp_path, capsys):
        """水位线低于批大小的配置错误在 dry-run 下同样拦截。"""
        from scripts import submit_campaign

        items_path = self._write_items(tmp_path)
        with pytest.raises(UsageError, match="水位永远补不进一批"):
            submit_campaign.main(
                [
                    "--username",
                    "admin",
                    "--password",
                    "pw",
                    "--workspace-id",
                    "ws-1",
                    "--items",
                    str(items_path),
                    "--watermark",
                    "2",
                    "--dry-run",
                ]
            )

    def test_main_module_exit_code_mapping(self, tmp_path):
        """`python scripts/submit_campaign.py` 的退出码映射：UsageError -> 2。"""
        import subprocess
        import sys as _sys

        result = subprocess.run(
            [
                _sys.executable,
                str(Path(__file__).resolve().parents[2] / "scripts" / "submit_campaign.py"),
                "--username",
                "admin",
                "--password",
                "pw",
                "--workspace-id",
                "ws-1",
                "--items",
                str(tmp_path / "missing.jsonl"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 2
        assert "清单文件不存在" in result.stderr


class TestPasswordEnv:
    """--password 缺省时读 AGENT_LEGION_CAMPAIGN_PASSWORD（P3）。"""

    def _write_items(self, tmp_path: Path) -> Path:
        path = tmp_path / "campaign.jsonl"
        path.write_text(
            '{"type": "ref", "connection_key": "cms", "external_id": "Q-1"}\n', encoding="utf-8"
        )
        return path

    def test_env_var_supplies_password(self, tmp_path):
        from scripts import submit_campaign

        items_path = self._write_items(tmp_path)
        requests_stub = mock.Mock()
        session = mock.Mock()
        login_response = mock.Mock()
        login_response.status_code = 200
        settings_response = mock.Mock()
        settings_response.status_code = 200
        settings_response.json.return_value = {"workflows": {"max_items_per_run": 20000}}
        stats_response = mock.Mock()
        stats_response.status_code = 200
        stats_response.json.return_value = {"job_stats": {}}
        submit_response = mock.Mock()
        submit_response.status_code = 500
        submit_response.json.return_value = {"detail": "boom"}
        session.post.side_effect = [login_response, submit_response]
        session.get.side_effect = [settings_response, stats_response]
        requests_stub.Session.return_value = session
        with (
            mock.patch.dict("os.environ", {"AGENT_LEGION_CAMPAIGN_PASSWORD": "env-pw"}),
            mock.patch.dict(sys.modules, {"requests": requests_stub}),
            pytest.raises(SubmitError),
        ):
            # 登录成功后让首个批次 POST 以 5xx 失败退出（这里只验证登录
            # 用了 env 密码）。
            submit_campaign.main(
                [
                    "--username",
                    "admin",
                    "--workspace-id",
                    "ws-1",
                    "--items",
                    str(items_path),
                    "--poll-interval",
                    "0",
                    "--retry-wait",
                    "0",
                    "--retry-max",
                    "1",
                ]
            )
        (_, kwargs) = session.post.call_args_list[0]
        assert kwargs["json"] == {"username": "admin", "password": "env-pw"}

    def test_missing_password_is_usage_error(self, tmp_path):
        from scripts import submit_campaign

        items_path = self._write_items(tmp_path)
        env = {k: v for k, v in os.environ.items() if k != "AGENT_LEGION_CAMPAIGN_PASSWORD"}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.object(submit_campaign, "requests", create=True) as requests_mock,
        ):
            requests_mock.Session.side_effect = AssertionError("must not login without password")
            with pytest.raises(UsageError, match="AGENT_LEGION_CAMPAIGN_PASSWORD"):
                submit_campaign.main(
                    [
                        "--username",
                        "admin",
                        "--workspace-id",
                        "ws-1",
                        "--items",
                        str(items_path),
                    ]
                )


class TestCampaignClientHttp:
    def _client(self, session: mock.Mock) -> object:
        from scripts.submit_campaign import CampaignClient

        return CampaignClient("http://127.0.0.1:8000/", 30.0, session, lambda _m: None)

    def test_login_sets_csrf_header_and_posts_credentials(self):
        from scripts.submit_campaign import CampaignClient

        session = mock.Mock()
        login_response = mock.Mock()
        login_response.status_code = 200
        session.post.return_value = login_response
        requests_mock = mock.Mock()
        requests_mock.Session.return_value = session

        client = CampaignClient.login(
            "http://127.0.0.1:8000/", "admin", "pw", 30.0, requests_mock, lambda _m: None
        )

        session.headers.update.assert_called_once_with({"x-agent-legion-request": "1"})
        (url,), kwargs = session.post.call_args
        assert url == "http://127.0.0.1:8000/api/auth/login"
        assert kwargs["json"] == {"username": "admin", "password": "pw"}
        assert client.base == "http://127.0.0.1:8000"

    def test_fetch_job_stats_reads_counter(self):
        session = mock.Mock()
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"job_stats": {"pending": 5, "completed": 7}}
        session.get.return_value = response

        stats = self._client(session).fetch_job_stats("ws-1")

        assert stats == {"pending": 5, "completed": 7}
        (url,), _ = session.get.call_args
        assert url == "http://127.0.0.1:8000/api/workspaces/ws-1/stats"

    def test_fetch_job_stats_non_200_raises_submit_error(self):
        session = mock.Mock()
        response = mock.Mock()
        response.status_code = 404
        response.json.return_value = {"detail": "Workspace not found"}
        session.get.return_value = response

        from scripts.submit_campaign import SubmitError

        with pytest.raises(SubmitError, match="404"):
            self._client(session).fetch_job_stats("ws-1")

    def test_fetch_max_items_per_run_reads_settings(self):
        session = mock.Mock()
        response = mock.Mock()
        response.status_code = 200
        # InstanceSettingsResponse 的真实契约形状：workflows 在顶层、
        # 无 executor_runtime 包装（instance_settings_contracts.py）。
        response.json.return_value = {"workflows": {"max_items_per_run": 5}}
        session.get.return_value = response

        assert self._client(session).fetch_max_items_per_run() == 5

    def test_fetch_max_items_per_run_falls_back_on_drift(self):
        session = mock.Mock()
        response = mock.Mock()
        response.status_code = 200
        # 服务端响应缺 workflows.max_items_per_run（契约漂移）时回落 20000。
        response.json.return_value = {"unexpected": "shape"}
        session.get.return_value = response

        assert self._client(session).fetch_max_items_per_run() == 20_000

    def test_submit_batch_posts_items_without_workflow_key(self):
        session = mock.Mock()
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"run": {"id": "r-1"}, "created_count": 2}
        session.post.return_value = response
        items = [{"type": "ref", "connection_key": "cms", "external_id": "Q-1"}]

        result = self._client(session).submit_batch("ws-1", items)

        assert result["created_count"] == 2
        (url,), kwargs = session.post.call_args
        assert url == "http://127.0.0.1:8000/api/workspaces/ws-1/runs"
        assert kwargs["json"] == {"items": items}
        assert kwargs["timeout"] is None  # 批提交不设超时：5k items 实测 6.9s，超时即重试

    def test_submit_batch_422_raises_with_detail(self):
        session = mock.Mock()
        response = mock.Mock()
        response.status_code = 422
        response.json.return_value = {"detail": "Run items exceed the per-run limit: 5 > 1."}
        session.post.return_value = response

        from scripts.submit_campaign import SubmitError

        with pytest.raises(SubmitError, match="exceed the per-run limit"):
            self._client(session).submit_batch("ws-1", [{"type": "material", "material_id": "m"}])

    def test_submit_batch_absorbs_all_duplicate_400(self):
        """P1-1：全重复 400（"No tasks were resolved"）转成 created_count=0
        的成功应答，不抛异常——重跑已成功批的场景。"""
        session = mock.Mock()
        response = mock.Mock()
        response.status_code = 400
        response.json.return_value = {"detail": "No tasks were resolved from input"}
        session.post.return_value = response
        items = [{"type": "ref", "connection_key": "cms", "external_id": "Q-1"}]

        result = self._client(session).submit_batch("ws-1", items)

        assert result == {"run": None, "created_count": 0, "job_ids": []}

    def test_submit_batch_other_400_still_raises(self):
        """非吸收语义的 400（如分块中途失败的 partial-failure 结构）照常抛。"""
        session = mock.Mock()
        response = mock.Mock()
        response.status_code = 400
        response.json.return_value = {"detail": "Partial run creation failed"}
        session.post.return_value = response

        from scripts.submit_campaign import SubmitError

        with pytest.raises(SubmitError, match="Partial run creation failed"):
            self._client(session).submit_batch("ws-1", [{"type": "material", "material_id": "m"}])

    @pytest.mark.parametrize(
        ("status_code", "transient"),
        [
            (400, False),
            (401, False),
            (403, False),
            (404, False),
            (422, False),
            (500, True),
            (503, True),
        ],
    )
    def test_http_error_transient_classification(self, status_code, transient):
        """4xx 一律不可重试、5xx 瞬态可重试（P2-1）。"""
        from scripts.submit_campaign import SubmitError, _raise_http_error

        response = mock.Mock()
        response.status_code = status_code
        response.json.return_value = {"detail": "boom"}
        with pytest.raises(SubmitError) as exc_info:
            _raise_http_error(response, "http://x/api")
        assert exc_info.value.transient is transient

    def test_http_error_401_mentions_session_expiry(self):
        """401 提示语说明长投放中 session 可能过期、需重跑续投。"""
        from scripts.submit_campaign import SubmitError, _raise_http_error

        response = mock.Mock()
        response.status_code = 401
        response.json.return_value = {"detail": "Not authenticated"}
        with pytest.raises(SubmitError, match="重跑同一命令幂等续投"):
            _raise_http_error(response, "http://x/api")

    def test_http_error_403_mentions_admin(self):
        """403 提示语提及 admin 权限（instance-settings 端点 require_admin）。"""
        from scripts.submit_campaign import SubmitError, _raise_http_error

        response = mock.Mock()
        response.status_code = 403
        response.json.return_value = {"detail": "Forbidden"}
        with pytest.raises(SubmitError, match="admin"):
            _raise_http_error(response, "http://x/api")

    def test_http_error_422_mentions_contract(self):
        """422 提示语指向 batch-size / item 契约。"""
        from scripts.submit_campaign import SubmitError, _raise_http_error

        response = mock.Mock()
        response.status_code = 422
        response.json.return_value = {"detail": "Validation failed"}
        with pytest.raises(SubmitError, match="batch-size"):
            _raise_http_error(response, "http://x/api")
