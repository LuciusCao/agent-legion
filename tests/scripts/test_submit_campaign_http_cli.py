"""Unit tests for scripts/submit_campaign.py (#505)——HTTP 层与 CLI 编排。

纯静态单测（no_db）：CampaignClient 的登录 / 水位读 / 批提交 / HTTP
错误分类（mock requests.Session 桩替），main 的 dry-run 编排、退出码
映射与 password 环境变量，全部不触网络不触 DB。

姊妹文件：test_submit_campaign.py（投放循环）、test_submit_campaign_manifest.py
（清单解析）、test_submit_campaign_guards.py（水位口径与参数护栏）。
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
    SubmitError,
    UsageError,
)

pytestmark = pytest.mark.no_db


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

    def test_main_dry_run_low_watermark_large_batch_ok(self, tmp_path, capsys):
        """低水位线 + 大批次的 dry-run 照常出批（codex #531 P2-1）。

        --watermark 2、7 个 item（实际批大小 7 > 2）是合法的突发配置：
        dry-run 不在启动期拒绝，正常打印批次计划。
        """
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
                    "--watermark",
                    "2",
                    "--batch-size",
                    "7",
                    "--dry-run",
                ]
            )
        assert code == 0
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "1 批" in out

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
