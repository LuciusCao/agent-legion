import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.app.executors.kinds import UnknownExecutorKindError
from server.app.settings import load_env_file, load_settings


@pytest.fixture(autouse=True)
def _clear_video_hive_env(monkeypatch):
    for key in (
        "BASECMS_BASE_URL",
        "BASECMS_TOKEN",
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
        "VIDEO_HIVE_CMS_TOKEN",
        "VIDEO_HIVE_CMS_TOKEN_GEN_SECRET",
        "VIDEO_HIVE_ASR_WHISPER_BINARY",
        "VIDEO_HIVE_ASR_WHISPER_MODEL",
        "VIDEO_HIVE_ASR_SENSEVOICE_MODEL_DIR",
        "VIDEO_HIVE_PI_BINARY",
        "VIDEO_HIVE_OPENCLAW_CWD",
        "VIDEO_HIVE_SKIP_DOTENV",
    ):
        monkeypatch.delenv(key, raising=False)


def test_load_env_file_preserves_quoted_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("BASECMS_TOKEN", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text(
        'BASECMS_TOKEN="from-file"\nBASECMS_SECRET="fake#secret$value"\n',
        encoding="utf-8",
    )

    load_env_file(env_file)

    assert os.environ["BASECMS_TOKEN"] == "already-set"
    assert os.environ["BASECMS_SECRET"] == "fake#secret$value"


def test_env_example_lists_all_basecms_variables():
    example_path = Path(__file__).resolve().parents[1] / ".env.example"
    example = example_path.read_text(encoding="utf-8")
    for key in (
        "BASECMS_BASE_URL",
        "BASECMS_TOKEN",
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
    ):
        assert f"{key}=" in example, f"{key} is missing from .env.example"


def test_database_url_environment_override(tmp_path, monkeypatch):
    config_path = tmp_path / "app.yaml"
    config_path.write_text("database: {url: postgresql://configured/app}\n", encoding="utf-8")
    monkeypatch.setenv("VIDEO_HIVE_DATABASE_URL", "postgresql://override/test")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.database_url == "postgresql://override/test"


def test_sqlite_database_url_is_rejected(tmp_path, monkeypatch):
    config_path = tmp_path / "app.yaml"
    config_path.write_text("database: {url: data/app.sqlite}\n", encoding="utf-8")
    monkeypatch.delenv("VIDEO_HIVE_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="PostgreSQL URL"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)


def test_basecms_env_takes_precedence_over_video_hive_cms_env(tmp_path, monkeypatch):
    from server.app.cms.auth import _token_gen_config
    from server.app.cms.client import get_token

    monkeypatch.setenv("VIDEO_HIVE_CMS_TOKEN", "video-hive-token")
    monkeypatch.setenv("VIDEO_HIVE_CMS_TOKEN_GEN_SECRET", "video-hive-secret")
    monkeypatch.setenv("BASECMS_TOKEN", "basecms-token")
    monkeypatch.setenv("BASECMS_APP_ID", "basecms-app")
    monkeypatch.setenv("BASECMS_NONCE", "basecms-nonce")
    monkeypatch.setenv("BASECMS_SECRET", "basecms-secret")
    monkeypatch.setenv("BASECMS_TOKEN_URL", "http://basecms/token")
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "cms:\n"
        "  token: yaml-token\n"
        "  token_gen:\n"
        "    app_id: yaml-app\n"
        "    nonce: yaml-nonce\n"
        "    secret: yaml-secret\n"
        "    url: http://yaml/token\n"
        "openclaw:\n"
        "  cwd: .\n"
        "  command_template:\n"
        "    - openclaw\n"
        "    - agent\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    # VIDEO_HIVE_* still wins in the parsed config (generic YAML override).
    assert settings.config["cms"]["token"] == "video-hive-token"
    assert settings.config["cms"]["token_gen"]["secret"] == "video-hive-secret"
    # BASECMS_* wins at the CMS client/auth layer.
    assert get_token("dev", settings.config) == "basecms-token"
    token_gen = _token_gen_config(settings.config)
    assert token_gen["app_id"] == "basecms-app"
    assert token_gen["nonce"] == "basecms-nonce"
    assert token_gen["secret"] == "basecms-secret"
    assert token_gen["url"] == "http://basecms/token"


def _write_split_config(root: Path) -> None:
    config_dir = root / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text("data_dir: data\nserver: {}\n", encoding="utf-8")
    (config_dir / "video_hive.yaml").write_text(
        "cms:\n"
        "  token: yaml-token\n"
        "  token_gen:\n"
        "    secret: yaml-secret\n"
        "asr:\n"
        "  provider: whisper\n"
        "  whisper:\n"
        "    binary: yaml-binary\n"
        "    model: yaml-model\n"
        "  sensevoice:\n"
        "    model_dir: yaml-dir\n"
        "openclaw:\n"
        "  cwd: yaml-cwd\n"
        "  command_template:\n"
        "    - openclaw\n"
        "    - agent\n",
        encoding="utf-8",
    )
    (config_dir / "workflow.yaml").write_text(
        "workflows:\n  enabled: false\n  pi:\n    binary: yaml-pi\nexecutors: {}\n",
        encoding="utf-8",
    )


def test_load_settings_reads_project_dotenv_by_default(tmp_path, monkeypatch):
    _write_split_config(tmp_path)
    (tmp_path / ".env").write_text("VIDEO_HIVE_CMS_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("VIDEO_HIVE_SKIP_DOTENV", raising=False)

    settings = load_settings()

    assert settings.config["cms"]["token"] == "dotenv-token"


def test_load_settings_can_skip_project_dotenv(tmp_path, monkeypatch):
    _write_split_config(tmp_path)
    (tmp_path / ".env").write_text("VIDEO_HIVE_CMS_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("VIDEO_HIVE_SKIP_DOTENV", "1")

    settings = load_settings()

    assert settings.config["cms"]["token"] == "yaml-token"


def test_default_split_layout_builds_effective_settings(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text("data_dir: runtime\n", encoding="utf-8")
    (config_dir / "video_hive.yaml").write_text(
        "cms: {base_url: 'https://cms.example/v2'}\n"
        "openclaw: {cwd: '.', command_template: [openclaw, agent]}\n",
        encoding="utf-8",
    )
    (config_dir / "workflow.yaml").write_text(
        "workflows: {enabled: false}\nexecutors: {}\n", encoding="utf-8"
    )
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
    settings = load_settings()
    assert settings.data_dir == tmp_path / "runtime"
    assert settings.config["cms"]["question_url"].startswith("https://cms.example/v2")


def test_explicit_path_does_not_inspect_partial_neighbor_layout(tmp_path):
    (tmp_path / "app.yaml").write_text("data_dir: ignored\n", encoding="utf-8")
    explicit = tmp_path / "custom.yaml"
    explicit.write_text("data_dir: selected\n", encoding="utf-8")
    settings = load_settings(data_dir=tmp_path / "data", config_path=explicit)
    assert settings.config["data_dir"] == "selected"
    # The neighbor app.yaml must not influence the explicit configuration.
    assert settings.config.get("server") is None
    assert settings.config.get("worker") is None


def test_load_settings_rejects_malformed_executor_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "executors:\n"
        "  bad-exec:\n"
        "    kind: local\n"
        "    global_capacity: 0\n"
        "    capabilities: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    message = str(exc_info.value)
    assert "bad-exec" in message
    assert "global_capacity" in message


def test_load_settings_builds_agent_catalog(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\n"
        "agents:\n"
        "  key-info-v1:\n"
        "    capability: generate_key_info\n"
        "    runtime: pi\n"
        "    skill: question/generate_key_info\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VIDEO_HIVE_DATABASE_URL", raising=False)

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.agent_definitions["key-info-v1"].capability == "generate_key_info"
    assert settings.agent_definitions["key-info-v1"].runtime == "pi"


def test_load_settings_exposes_executor_definitions(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "executors:\n"
        "  local-default:\n"
        "    kind: local\n"
        "    global_capacity: 4\n"
        "    capabilities:\n"
        "      fetch_questions:\n"
        "        handler: question_comprehension_info.fetch_questions\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert "local-default" in settings.executor_definitions
    assert settings.executor_definitions["local-default"].kind == "local"
    assert settings.executor_definitions["local-default"].global_capacity == 4


def test_load_settings_exposes_executor_runtime(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "workflows:\n"
        "  enabled: true\n"
        "  pi:\n"
        "    binary: pi\n"
        '    provider: ""\n'
        '    model: ""\n'
        "    thinking: low\n"
        "    timeout_seconds: 600\n"
        "    environment:\n"
        '      PI_SKIP_VERSION_CHECK: "1"\n'
        "openclaw:\n"
        "  cwd: .\n"
        "  timeout_seconds: 600\n"
        "  skill_safety:\n"
        "    enabled: true\n"
        "    repos:\n"
        "      - path: ~/.openclaw/workspace/skills/s1\n"
        "        ref: v1.0.0\n"
        "  command_template:\n"
        "    - openclaw\n"
        "    - agent\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.executor_runtime.workflows.enabled is True
    assert settings.executor_runtime.workflows.pi.binary == "pi"
    assert settings.executor_runtime.workflows.pi.thinking == "low"
    assert settings.executor_runtime.openclaw.cwd == "."
    assert settings.executor_runtime.openclaw.timeout_seconds == 600
    assert settings.executor_runtime.openclaw.command_template == ("openclaw", "agent")
    assert settings.executor_runtime.openclaw.skill_safety.enabled is True
    assert settings.executor_runtime.openclaw.skill_safety.repos == [
        {"path": "~/.openclaw/workspace/skills/s1", "ref": "v1.0.0"}
    ]
    assert settings.config["workflows"]["pi"]["thinking"] == "low"


def test_load_settings_rejects_empty_openclaw_command_template(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\nopenclaw:\n  command_template: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert "command_template" in str(exc_info.value)


def test_load_settings_rejects_unknown_executor_kind(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "executors:\n"
        "  weird-exec:\n"
        "    kind: unknown\n"
        "    global_capacity: 1\n"
        "    capabilities: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(UnknownExecutorKindError) as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert "weird-exec" in str(exc_info.value)


@pytest.mark.parametrize(
    ("layout", "env_var", "config_path", "env_value", "expected"),
    [
        (
            "legacy",
            "BASECMS_BASE_URL",
            ["cms", "base_url"],
            "http://cms.example.com/v2",
            "http://cms.example.com/v2",
        ),
        ("legacy", "VIDEO_HIVE_CMS_TOKEN", ["cms", "token"], "env-token", "env-token"),
        (
            "legacy",
            "VIDEO_HIVE_CMS_TOKEN_GEN_SECRET",
            ["cms", "token_gen", "secret"],
            "env-secret",
            "env-secret",
        ),
        (
            "legacy",
            "VIDEO_HIVE_ASR_WHISPER_BINARY",
            ["asr", "whisper", "binary"],
            "/tmp/whisper-cli",
            "/tmp/whisper-cli",
        ),
        (
            "legacy",
            "VIDEO_HIVE_ASR_WHISPER_MODEL",
            ["asr", "whisper", "model"],
            "/tmp/model.bin",
            "/tmp/model.bin",
        ),
        (
            "legacy",
            "VIDEO_HIVE_ASR_SENSEVOICE_MODEL_DIR",
            ["asr", "sensevoice", "model_dir"],
            "/tmp/sensevoice",
            "/tmp/sensevoice",
        ),
        ("legacy", "VIDEO_HIVE_PI_BINARY", ["workflows", "pi", "binary"], "/tmp/pi", "/tmp/pi"),
        ("legacy", "VIDEO_HIVE_OPENCLAW_CWD", ["openclaw", "cwd"], "/tmp/cwd", "/tmp/cwd"),
        (
            "split",
            "BASECMS_BASE_URL",
            ["cms", "base_url"],
            "http://cms.example.com/v2",
            "http://cms.example.com/v2",
        ),
        ("split", "VIDEO_HIVE_CMS_TOKEN", ["cms", "token"], "env-token", "env-token"),
        (
            "split",
            "VIDEO_HIVE_CMS_TOKEN_GEN_SECRET",
            ["cms", "token_gen", "secret"],
            "env-secret",
            "env-secret",
        ),
        (
            "split",
            "VIDEO_HIVE_ASR_WHISPER_BINARY",
            ["asr", "whisper", "binary"],
            "/tmp/whisper-cli",
            "/tmp/whisper-cli",
        ),
        (
            "split",
            "VIDEO_HIVE_ASR_WHISPER_MODEL",
            ["asr", "whisper", "model"],
            "/tmp/model.bin",
            "/tmp/model.bin",
        ),
        (
            "split",
            "VIDEO_HIVE_ASR_SENSEVOICE_MODEL_DIR",
            ["asr", "sensevoice", "model_dir"],
            "/tmp/sensevoice",
            "/tmp/sensevoice",
        ),
        ("split", "VIDEO_HIVE_PI_BINARY", ["workflows", "pi", "binary"], "/tmp/pi", "/tmp/pi"),
        ("split", "VIDEO_HIVE_OPENCLAW_CWD", ["openclaw", "cwd"], "/tmp/cwd", "/tmp/cwd"),
    ],
)
def test_env_override_precedes_yaml(
    tmp_path, monkeypatch, layout, env_var, config_path, env_value, expected
):
    monkeypatch.setenv(env_var, env_value)
    if layout == "legacy":
        config_path_file = tmp_path / "workflow.yaml"
        config_path_file.write_text(
            "data_dir: data\n"
            "cms:\n"
            "  token: yaml-token\n"
            "  token_gen:\n"
            "    secret: yaml-secret\n"
            "asr:\n"
            "  provider: whisper\n"
            "  whisper:\n"
            "    binary: yaml-binary\n"
            "    model: yaml-model\n"
            "  sensevoice:\n"
            "    model_dir: yaml-dir\n"
            "workflows:\n"
            "  enabled: false\n"
            "  pi:\n"
            "    binary: yaml-pi\n"
            "openclaw:\n"
            "  cwd: yaml-cwd\n"
            "  command_template:\n"
            "    - openclaw\n"
            "    - agent\n",
            encoding="utf-8",
        )
    else:
        _write_split_config(tmp_path)
        config_path_file = tmp_path / "config" / "workflow.yaml"

    if layout == "split":
        monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
        settings = load_settings()
    else:
        settings = load_settings(data_dir=tmp_path / "data", config_path=config_path_file)

    node = settings.config
    for key in config_path[:-1]:
        node = node[key]
    assert node[config_path[-1]] == expected
