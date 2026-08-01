# ruff: noqa: E402
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# The tool package lives inside a hyphenated directory, so add its parent to
# the import path for the tests.
_TOOL_DIR = Path(__file__).parents[2] / "tools" / "comprehension-uploader"
sys.path.insert(0, str(_TOOL_DIR))

from comprehension_uploader import cli
from comprehension_uploader.auth import AuthError, get_token
from comprehension_uploader.packager import package_comprehension_info_from_workspace_zip
from comprehension_uploader.question_source import JSONFileQuestionSource
from comprehension_uploader.uploader import Uploader

from tests.tools.comprehension_uploader_fixtures import make_valid_comprehension_data


def _make_cli_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "api_base_url: http://example.com",
                f"db_path: {tmp_path / 'cli.db'}",
                f'question_source: {{"type": "json_file", "path": "{tmp_path / "questions.json"}"}}',
                "upload_on_duplicate: skip",
                "request_timeout: 5",
                "max_retries: 0",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _make_cli_package(tmp_path: Path) -> Path:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "QCLI",
                "subject_id": 2,
                "question_uuid": "uuid-cli",
                "question_vno": 1,
                "comprehension_difficulty": 50,
                "format_vno": "v1",
                "comprehension_data": make_valid_comprehension_data(),
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return package


def test_cli_upload_uses_workspace_as_batch_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _make_cli_config(tmp_path)
    package_path = _make_cli_package(tmp_path)

    captured: dict[str, Any] = {}

    def fake_upload_batch(
        self: Uploader, records: list[Any], batch_id: str, workspace_id: str | None = None
    ) -> None:
        captured["batch_id"] = batch_id
        captured["workspace_id"] = workspace_id
        captured["count"] = len(records)

    monkeypatch.setattr(Uploader, "upload_batch", fake_upload_batch)
    monkeypatch.setenv("CMS_TOKEN", "token")

    rc = cli.main(
        [
            "upload",
            "--config",
            str(config_path),
            "--workspace",
            "ws-123",
            str(package_path),
        ]
    )
    assert rc == 0
    assert captured["batch_id"].startswith("ws-123-")
    assert captured["workspace_id"] == "ws-123"
    assert captured["count"] == 1
    assert "ws-123" in capsys.readouterr().out


def test_cli_upload_batch_id_overrides_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _make_cli_config(tmp_path)
    package_path = _make_cli_package(tmp_path)

    captured: dict[str, Any] = {}

    def fake_upload_batch(
        self: Uploader, records: list[Any], batch_id: str, workspace_id: str | None = None
    ) -> None:
        captured["batch_id"] = batch_id
        captured["workspace_id"] = workspace_id

    monkeypatch.setattr(Uploader, "upload_batch", fake_upload_batch)
    monkeypatch.setenv("CMS_TOKEN", "token")

    rc = cli.main(
        [
            "upload",
            "--config",
            str(config_path),
            "--workspace",
            "ws-123",
            "--batch-id",
            "batch-999",
            str(package_path),
        ]
    )
    assert rc == 0
    assert captured["batch_id"] == "batch-999"
    assert captured["workspace_id"] == "ws-123"


def test_get_token_prefers_cms_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMS_TOKEN", "direct-token")
    monkeypatch.delenv("CMS_APP_ID", raising=False)
    assert get_token({}) == "direct-token"


def test_get_token_accepts_basecms_token_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMS_TOKEN", raising=False)
    monkeypatch.setenv("BASECMS_TOKEN", "alias-token")
    assert get_token({}) == "alias-token"


def test_get_token_rejects_conflicting_dual_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMS_TOKEN", "new-token")
    monkeypatch.setenv("BASECMS_TOKEN", "old-token")
    monkeypatch.setattr("comprehension_uploader.auth._maybe_load_dotenv", lambda: None)
    with pytest.raises(AuthError, match="BASECMS_TOKEN"):
        get_token({})


def test_get_token_generates_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMS_TOKEN", raising=False)
    monkeypatch.delenv("CMS_USER_NAME", raising=False)
    monkeypatch.delenv("CMS_USER_PASSWORD", raising=False)
    monkeypatch.setenv("CMS_APP_ID", "app-1")
    monkeypatch.setenv("CMS_NONCE", "nonce-1")
    monkeypatch.setenv("CMS_SECRET", "secret-1")
    monkeypatch.setenv("CMS_TOKEN_URL", "http://auth.example.com/token")
    monkeypatch.setattr("comprehension_uploader.auth._maybe_load_dotenv", lambda: None)

    with patch("comprehension_uploader.auth.requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"data": {"token": "generated-token"}}
        mock_post.return_value.raise_for_status = lambda: None
        token = get_token({})

    assert token == "generated-token"
    call_kwargs = mock_post.call_args.kwargs
    payload = call_kwargs["json"]
    assert payload["app_id"] == "app-1"
    assert payload["nonce"] == "nonce-1"
    assert payload["secret"] == "secret-1"
    assert "timestamp" in payload
    assert "sign" in payload
    assert call_kwargs["timeout"] == 10


def test_get_token_raises_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMS_TOKEN", raising=False)
    monkeypatch.delenv("CMS_APP_ID", raising=False)
    monkeypatch.delenv("CMS_NONCE", raising=False)
    monkeypatch.delenv("CMS_SECRET", raising=False)
    monkeypatch.delenv("CMS_TOKEN_URL", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)
    monkeypatch.delenv("BASECMS_APP_ID", raising=False)
    monkeypatch.delenv("BASECMS_NONCE", raising=False)
    monkeypatch.delenv("BASECMS_SECRET", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN_URL", raising=False)
    monkeypatch.delenv("CMS_USER_NAME", raising=False)
    monkeypatch.delenv("CMS_USER_PASSWORD", raising=False)
    monkeypatch.setattr("comprehension_uploader.auth._maybe_load_dotenv", lambda: None)
    with pytest.raises(AuthError):
        get_token({})


# ---------------------------------------------------------------------------
# User-login JWT flow (/user/login -> /v1/auth)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.trust_env = True

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse(self.responses[len(self.calls) - 1])


def _user_auth_config() -> dict[str, Any]:
    return {
        "user_auth": {
            "app_id": 78002100,
            "account_type": 1,
            "client_params": '{"source":"SPAD"}',
            "login_url": "http://study-user-api.internal.example.com/user/user/login",
            "auth_url": "https://addons-api.example.com/common/v1/auth",
        }
    }


def _prepare_user_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMS_TOKEN", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)
    monkeypatch.setenv("CMS_USER_NAME", "student001")
    monkeypatch.setenv("CMS_USER_PASSWORD", "secret-pw")
    monkeypatch.setattr("comprehension_uploader.auth._maybe_load_dotenv", lambda: None)


def test_get_token_user_auth_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_user_auth_env(monkeypatch)
    session = _FakeSession(
        [
            {"code": 200, "data": {"user_token": "user-token-1"}},
            {"code": 0, "data": {"token": "jwt-token-1"}},
        ]
    )
    monkeypatch.setattr("comprehension_uploader.auth.requests.Session", lambda: session)

    token = get_token(_user_auth_config())

    assert token == "jwt-token-1"
    assert session.trust_env is False
    login_call, auth_call = session.calls
    assert login_call["url"] == "http://study-user-api.internal.example.com/user/user/login"
    assert login_call["json"] == {
        "app_id": 78002100,
        "account_type": 1,
        "uname": "student001",
        "password": "secret-pw",
        "client_params": '{"source":"SPAD"}',
    }
    assert auth_call["url"] == "https://addons-api.example.com/common/v1/auth"
    assert auth_call["json"] == {"user_token": "user-token-1", "app_id": 78002100}


def test_get_token_user_auth_resolve_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_user_auth_env(monkeypatch)
    session = _FakeSession(
        [
            {"code": 200, "data": {"user_token": "user-token-1"}},
            {"code": 0, "data": {"token": "jwt-token-1"}},
        ]
    )
    monkeypatch.setattr("comprehension_uploader.auth.requests.Session", lambda: session)
    config = _user_auth_config()
    config["user_auth"]["login_resolve_ip"] = "10.1.2.3"

    get_token(config)

    login_call = session.calls[0]
    assert login_call["url"] == "http://10.1.2.3/user/user/login"
    assert login_call["headers"]["Host"] == "study-user-api.internal.example.com"


def test_get_token_user_auth_login_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_user_auth_env(monkeypatch)
    session = _FakeSession([{"code": -9110004, "data": {}, "message": "账号密码错误"}])
    monkeypatch.setattr("comprehension_uploader.auth.requests.Session", lambda: session)

    with pytest.raises(AuthError, match="User login failed"):
        get_token(_user_auth_config())


def test_get_token_user_auth_jwt_exchange_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_user_auth_env(monkeypatch)
    session = _FakeSession(
        [
            {"code": 200, "data": {"user_token": "user-token-1"}},
            {"code": 10011, "data": None, "message": "参数错误"},
        ]
    )
    monkeypatch.setattr("comprehension_uploader.auth.requests.Session", lambda: session)

    with pytest.raises(AuthError, match="JWT exchange failed"):
        get_token(_user_auth_config())


def test_get_token_user_auth_missing_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMS_TOKEN", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)
    monkeypatch.delenv("CMS_USER_NAME", raising=False)
    monkeypatch.delenv("CMS_USER_PASSWORD", raising=False)
    monkeypatch.setattr("comprehension_uploader.auth._maybe_load_dotenv", lambda: None)

    with pytest.raises(AuthError, match="uname"):
        get_token(_user_auth_config())


# ---------------------------------------------------------------------------
# Package and validate CLI commands
# ---------------------------------------------------------------------------


def test_cli_package_command_builds_jsonl(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    question_dir = input_dir / "QPKG"
    question_dir.mkdir()
    question_dir.joinpath("comprehension_info.json").write_text(
        json.dumps(
            {
                "question_id": "QPKG",
                "schema_version": "v1",
                "comprehension_data": make_valid_comprehension_data(55),
            }
        ),
        encoding="utf-8",
    )

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "QPKG": {
                    "question_id": "QPKG",
                    "subject_id": 3,
                    "question_uuid": "uuid-pkg",
                    "question_vno": 2,
                    "stem": "Package stem",
                    "options": [{"label": "A", "text": "pkg-a"}],
                }
            }
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "api_base_url: http://example.com",
                f"db_path: {tmp_path / 'pkg.db'}",
                f'question_source: {{"type": "json_file", "path": "{questions_path}"}}',
                "upload_on_duplicate: skip",
                "request_timeout: 5",
                "max_retries: 0",
            ]
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "package.jsonl"
    rc = cli.main(
        [
            "package",
            "--config",
            str(config_path),
            "--input-dir",
            str(input_dir),
            "--output",
            str(output_path),
        ]
    )
    assert rc == 0
    lines = output_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    package_line = json.loads(lines[0])
    assert package_line["question_id"] == "QPKG"
    assert package_line["subject_id"] == 3
    assert package_line["question_uuid"] == "uuid-pkg"
    assert package_line["question_vno"] == 2
    assert package_line["comprehension_difficulty"] == 55
    assert package_line["format_vno"] == "v1"
    assert package_line["stem"] == "Package stem"
    assert package_line["options"] == [{"label": "A", "text": "pkg-a"}]
    assert "written" in capsys.readouterr().out


def test_cli_validate_command_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "QV1",
                "format_vno": "v1",
                "comprehension_data": make_valid_comprehension_data(),
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = cli.main(["validate", str(package)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "passed=1" in out
    assert "failed=0" in out


def test_cli_validate_command_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "package.jsonl"
    invalid_data = make_valid_comprehension_data()
    invalid_data["comprehension_difficulty"] = 0
    package.write_text(
        json.dumps(
            {
                "question_id": "QV2",
                "format_vno": "v1",
                "comprehension_data": invalid_data,
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = cli.main(["validate", str(package)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "passed=0" in captured.out
    assert "failed=1" in captured.out
    assert "v1" in captured.err


def test_package_from_workspace_zip_builds_jsonl(tmp_path: Path) -> None:
    import zipfile

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "QZIP": {
                    "question_id": "QZIP",
                    "subject_id": 3,
                    "question_uuid": "uuid-zip",
                    "question_vno": 2,
                    "stem": "Zip stem",
                    "options": [{"label": "A", "text": "zip-a"}],
                }
            }
        ),
        encoding="utf-8",
    )

    workspace_zip = tmp_path / "workspace-jobs-20260706120000000000.zip"
    with zipfile.ZipFile(workspace_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"jobs": [{"id": "job-1"}]}))
        zf.writestr(
            "job-1/comprehension_info.json",
            json.dumps(
                {
                    "question_id": "QZIP",
                    "schema_version": "v1",
                    "comprehension_data": make_valid_comprehension_data(55),
                }
            ),
        )

    output_path = tmp_path / "package.jsonl"
    summary = package_comprehension_info_from_workspace_zip(
        workspace_zip, output_path, JSONFileQuestionSource(questions_path)
    )
    assert summary["written"] == 1
    assert summary["skipped"] == 0

    lines = output_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    package_line = json.loads(lines[0])
    assert package_line["question_id"] == "QZIP"
    assert package_line["format_vno"] == "v1"
    assert package_line["stem"] == "Zip stem"


def _make_workspace_zip(tmp_path: Path, question_id: str, file_name: str = "workspace.zip") -> Path:
    import zipfile

    workspace_zip = tmp_path / file_name
    with zipfile.ZipFile(workspace_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"jobs": [{"id": "job-1"}]}))
        zf.writestr(
            "job-1/comprehension_info.json",
            json.dumps(
                {
                    "question_id": question_id,
                    "schema_version": "v1",
                    "comprehension_data": make_valid_comprehension_data(55),
                }
            ),
        )
    return workspace_zip


def test_cli_package_command_from_workspace_zip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace_zip = _make_workspace_zip(tmp_path, "QWS")

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "QWS": {
                    "question_id": "QWS",
                    "subject_id": 3,
                    "question_uuid": "uuid-ws",
                    "question_vno": 2,
                    "stem": "Workspace stem",
                    "options": [{"label": "A", "text": "ws-a"}],
                }
            }
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "api_base_url: http://example.com",
                f"db_path: {tmp_path / 'ws_pkg.db'}",
                f'question_source: {{"type": "json_file", "path": "{questions_path}"}}',
                "upload_on_duplicate: skip",
                "request_timeout: 5",
                "max_retries: 0",
            ]
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out.jsonl"
    rc = cli.main(
        [
            "package",
            "--config",
            str(config_path),
            "--workspace-package",
            str(workspace_zip),
            "--output",
            str(output_path),
        ]
    )
    assert rc == 0
    lines = output_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    package_line = json.loads(lines[0])
    assert package_line["question_id"] == "QWS"
    assert package_line["stem"] == "Workspace stem"


def test_cli_upload_command_from_workspace_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_zip = _make_workspace_zip(tmp_path, "QUP")

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "QUP": {
                    "question_id": "QUP",
                    "subject_id": 3,
                    "question_uuid": "uuid-up",
                    "question_vno": 2,
                    "stem": "Upload stem",
                    "options": [{"label": "A", "text": "up-a"}],
                }
            }
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "api_base_url: http://example.com",
                f"db_path: {tmp_path / 'ws_up.db'}",
                f'question_source: {{"type": "json_file", "path": "{questions_path}"}}',
                "upload_on_duplicate: skip",
                "request_timeout: 5",
                "max_retries: 0",
            ]
        ),
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    def fake_upload_batch(
        self: Uploader, records: list[Any], batch_id: str, workspace_id: str | None = None
    ) -> None:
        captured["count"] = len(records)
        captured["workspace_id"] = workspace_id

    monkeypatch.setattr(Uploader, "upload_batch", fake_upload_batch)
    monkeypatch.setenv("CMS_TOKEN", "token")

    rc = cli.main(
        [
            "upload",
            "--config",
            str(config_path),
            "--workspace",
            "ws-zip",
            "--workspace-package",
            str(workspace_zip),
        ]
    )
    assert rc == 0
    assert captured["count"] == 1
    assert captured["workspace_id"] == "ws-zip"
