import os
from pathlib import Path

import pytest
from pydantic import ValidationError

import server.app.cms.env as cms_env
from server.app.executors.kinds import UnknownExecutorKindError
from server.app.settings import load_env_file, load_settings


@pytest.fixture(autouse=True)
def _clear_agent_legion_env(monkeypatch):
    cms_env._warned_aliases.clear()
    for key in (
        "CMS_BASE_URL",
        "CMS_TOKEN",
        "CMS_APP_ID",
        "CMS_NONCE",
        "CMS_SECRET",
        "CMS_TOKEN_URL",
        "BASECMS_BASE_URL",
        "BASECMS_TOKEN",
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
        "AGENT_LEGION_CMS_TOKEN",
        "AGENT_LEGION_CMS_TOKEN_GEN_SECRET",
        "AGENT_LEGION_ASR_WHISPER_BINARY",
        "AGENT_LEGION_ASR_WHISPER_MODEL",
        "AGENT_LEGION_ASR_SENSEVOICE_MODEL_DIR",
        "AGENT_LEGION_PI_BINARY",
        "AGENT_LEGION_OPENCLAW_CWD",
        "AGENT_LEGION_SKIP_DOTENV",
    ):
        monkeypatch.delenv(key, raising=False)


def test_load_env_file_preserves_quoted_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("CMS_TOKEN", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text(
        'CMS_TOKEN="from-file"\nCMS_SECRET="fake#secret$value"\n',
        encoding="utf-8",
    )

    load_env_file(env_file)

    assert os.environ["CMS_TOKEN"] == "already-set"
    assert os.environ["CMS_SECRET"] == "fake#secret$value"


def test_env_example_lists_all_cms_variables():
    example_path = Path(__file__).resolve().parents[1] / ".env.example"
    example = example_path.read_text(encoding="utf-8")
    for key in (
        "CMS_BASE_URL",
        "CMS_TOKEN",
        "CMS_APP_ID",
        "CMS_NONCE",
        "CMS_SECRET",
        "CMS_TOKEN_URL",
        "AGENT_LEGION_VAULT_MASTER_KEY",
        "AGENT_LEGION_VAULT_MASTER_KEY_FILE",
    ):
        assert f"{key}=" in example, f"{key} is missing from .env.example"


def test_vault_master_key_env_overrides_map_to_config(tmp_path, monkeypatch):
    config_path = tmp_path / "app.yaml"
    config_path.write_text("database: {url: postgresql://configured/app}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", "fernet-key-value")
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", "/run/secrets/vault_key")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    vault = settings.config["vault"]
    assert vault["master_key"] == "fernet-key-value"
    assert vault["master_key_file"] == "/run/secrets/vault_key"


def test_database_url_environment_override(tmp_path, monkeypatch):
    config_path = tmp_path / "app.yaml"
    config_path.write_text("database: {url: postgresql://configured/app}\n", encoding="utf-8")
    # Skip the worktree .env so only the names set below are in play.
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.delenv("AGENT_LEGION_DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENT_LEGION_DATABASE_URL", "postgresql://override/test")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.database_url == "postgresql://override/test"


def test_cms_env_alias_conflict_rejected_at_startup(tmp_path, monkeypatch):
    config_path = tmp_path / "app.yaml"
    config_path.write_text("database: {url: postgresql://configured/app}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.setenv("CMS_TOKEN", "new-token")
    monkeypatch.setenv("BASECMS_TOKEN", "old-token")

    with pytest.raises(ValueError, match="BASECMS_TOKEN") as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert "CMS_TOKEN" in str(exc_info.value)


def test_cms_base_url_alias_only_applies_with_warning(tmp_path, monkeypatch, caplog):
    config_path = tmp_path / "app.yaml"
    config_path.write_text("database: {url: postgresql://configured/app}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.setenv("BASECMS_BASE_URL", "http://cms.alias.example/v2")

    with caplog.at_level("WARNING", logger="server.app.cms.env"):
        settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.config["cms"]["base_url"] == "http://cms.alias.example/v2"
    assert "BASECMS_BASE_URL" in caplog.text
    assert "CMS_BASE_URL" in caplog.text


def test_sqlite_database_url_is_rejected(tmp_path, monkeypatch):
    config_path = tmp_path / "app.yaml"
    config_path.write_text("database: {url: data/app.sqlite}\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_LEGION_DATABASE_URL", raising=False)
    # Worktrees carry a real AGENT_LEGION_DATABASE_URL in the project .env;
    # skip it so the yaml value below is the one under test.
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")

    with pytest.raises(ValueError, match="PostgreSQL URL"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)


def test_load_settings_rejects_retired_yaml_cms_token(tmp_path):
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\ncms: {token: yaml-token}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"cms\.token") as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    message = str(exc_info.value)
    assert "CMS_TOKEN" in message
    assert "AGENT_LEGION_CMS_TOKEN" in message
    assert "BASECMS_TOKEN" in message


def test_load_settings_rejects_retired_yaml_cms_token_gen(tmp_path):
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\n"
        "cms:\n"
        "  token_gen:\n"
        "    app_id: a\n"
        "    nonce: n\n"
        "    secret: s\n"
        "    url: http://yaml/token\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"cms\.token_gen") as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    message = str(exc_info.value)
    for env_key in ("CMS_APP_ID", "CMS_NONCE", "CMS_SECRET", "CMS_TOKEN_URL"):
        assert env_key in message


def test_load_settings_rejects_retired_yaml_cms_keys_in_split_layout(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text("data_dir: runtime\n", encoding="utf-8")
    (config_dir / "agent_legion.yaml").write_text("cms: {token: yaml-token}\n", encoding="utf-8")
    (config_dir / "workflow.yaml").write_text("workflows: {enabled: false}\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match=r"cms\.token"):
        load_settings()


def test_cms_env_takes_precedence_over_agent_legion_cms_env(tmp_path, monkeypatch):
    from server.app.cms.auth import _token_gen_config
    from server.app.cms.client import get_token

    monkeypatch.setenv("AGENT_LEGION_CMS_TOKEN", "agent-legion-token")
    monkeypatch.setenv("AGENT_LEGION_CMS_TOKEN_GEN_SECRET", "agent-legion-secret")
    # Skip the worktree .env: it carries BASECMS_* aliases whose values would
    # conflict with the CMS_* names set below.
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.setenv("CMS_TOKEN", "cms-token")
    monkeypatch.setenv("CMS_APP_ID", "cms-app")
    monkeypatch.setenv("CMS_NONCE", "cms-nonce")
    monkeypatch.setenv("CMS_SECRET", "cms-secret")
    monkeypatch.setenv("CMS_TOKEN_URL", "http://cms/token")
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\nopenclaw:\n  cwd: .\n  command_template:\n    - openclaw\n    - agent\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    # AGENT_LEGION_* still wins in the parsed config (generic YAML override).
    assert settings.config["cms"]["token"] == "agent-legion-token"
    assert settings.config["cms"]["token_gen"]["secret"] == "agent-legion-secret"
    # CMS_* wins at the CMS client/auth layer.
    assert get_token("dev", settings.config) == "cms-token"
    token_gen = _token_gen_config(settings.config)
    assert token_gen["app_id"] == "cms-app"
    assert token_gen["nonce"] == "cms-nonce"
    assert token_gen["secret"] == "cms-secret"
    assert token_gen["url"] == "http://cms/token"


def _write_split_config(root: Path) -> None:
    config_dir = root / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text("data_dir: data\nserver: {}\n", encoding="utf-8")
    (config_dir / "agent_legion.yaml").write_text(
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
        "workflows:\n  enabled: false\nexecutors: {}\n",
        encoding="utf-8",
    )


def test_load_settings_reads_project_dotenv_by_default(tmp_path, monkeypatch):
    _write_split_config(tmp_path)
    (tmp_path / ".env").write_text("AGENT_LEGION_CMS_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("AGENT_LEGION_SKIP_DOTENV", raising=False)

    settings = load_settings()

    assert settings.config["cms"]["token"] == "dotenv-token"


def test_load_settings_can_skip_project_dotenv(tmp_path, monkeypatch):
    _write_split_config(tmp_path)
    (tmp_path / ".env").write_text("AGENT_LEGION_CMS_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")

    settings = load_settings()

    # With the dotenv skipped, no env-injected CMS token exists; yaml values
    # from the split config stand on their own.
    assert "token" not in settings.config.get("cms", {})
    assert settings.config["asr"]["whisper"]["binary"] == "yaml-binary"


def test_default_split_layout_builds_effective_settings(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text("data_dir: runtime\n", encoding="utf-8")
    (config_dir / "agent_legion.yaml").write_text(
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
    assert settings.config["cms"]["knowledge_url"].startswith("https://cms.example/v2")
    # Question endpoint URLs derive from cms.base_url at execution time, not
    # from settings-time derivation.
    assert "question_url" not in settings.config["cms"]
    assert "question_detail_url" not in settings.config["cms"]
    assert "question_list_url" not in settings.config["cms"]


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
        "    kind: code\n"
        "    global_capacity: 0\n"
        "    capabilities: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    message = str(exc_info.value)
    assert "bad-exec" in message
    assert "global_capacity" in message


def test_load_settings_rejects_retired_agents_yaml(tmp_path, monkeypatch):
    """yaml ``agents:`` 段已退役（agent 配置治理 phase 3）：启动 fail-fast。"""
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
    monkeypatch.delenv("AGENT_LEGION_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="agents"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)


def test_load_settings_rejects_retired_workflows_pi_yaml(tmp_path, monkeypatch):
    """``workflows.pi`` 块已退役（agent 配置治理 phase 3）：启动 fail-fast。"""
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        'data_dir: data\nworkflows:\n  enabled: true\n  pi:\n    binary: pi\n    model: ""\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="workflows.pi"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)


def test_load_settings_exposes_executor_definitions(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "executors:\n"
        "  code-default:\n"
        "    kind: code\n"
        "    global_capacity: 4\n"
        "    capabilities:\n"
        "      fetch_questions:\n"
        "        path: workflow_nodes/question_intake.py\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert "code-default" in settings.executor_definitions
    assert settings.executor_definitions["code-default"].kind == "code"
    assert settings.executor_definitions["code-default"].global_capacity == 4


def test_load_settings_exposes_executor_runtime(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "workflows:\n"
        "  enabled: true\n"
        "openclaw:\n"
        "  cwd: .\n"
        "  timeout_seconds: 600\n"
        "  skill_safety:\n"
        "    enabled: true\n"
        "    repos:\n"
        "      - path: ~/.openclaw/workspace/skills/s1\n"
        "  command_template:\n"
        "    - openclaw\n"
        "    - agent\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.executor_runtime.workflows.enabled is True
    # workflows.pi 块已退役：PiRuntimeConfig 只剩硬编码默认（死路径 executors/pi.py 专用）。
    assert settings.executor_runtime.workflows.pi.flavor == "pi"
    assert settings.executor_runtime.workflows.pi.binary == "pi"
    assert settings.executor_runtime.openclaw.cwd == "."
    assert settings.executor_runtime.openclaw.timeout_seconds == 600
    assert settings.executor_runtime.openclaw.command_template == ("openclaw", "agent")
    assert settings.executor_runtime.openclaw.skill_safety.enabled is True
    assert [repo.path for repo in settings.executor_runtime.openclaw.skill_safety.repos] == [
        "~/.openclaw/workspace/skills/s1"
    ]


def test_load_settings_rejects_skill_safety_ref(tmp_path, monkeypatch):
    """skill_safety refs were retired (config governance G3); lock is the source."""
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "openclaw:\n"
        "  command_template:\n"
        "    - openclaw\n"
        "  skill_safety:\n"
        "    enabled: true\n"
        "    repos:\n"
        "      - path: ~/.openclaw/workspace/skills/s1\n"
        "        ref: v1.0.0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert "ref" in str(exc_info.value)


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
            "CMS_BASE_URL",
            ["cms", "base_url"],
            "http://cms.internal.example.cn/v2",
            "http://cms.internal.example.cn/v2",
        ),
        ("legacy", "AGENT_LEGION_CMS_TOKEN", ["cms", "token"], "env-token", "env-token"),
        (
            "legacy",
            "AGENT_LEGION_CMS_TOKEN_GEN_SECRET",
            ["cms", "token_gen", "secret"],
            "env-secret",
            "env-secret",
        ),
        (
            "legacy",
            "AGENT_LEGION_ASR_WHISPER_BINARY",
            ["asr", "whisper", "binary"],
            "/tmp/whisper-cli",
            "/tmp/whisper-cli",
        ),
        (
            "legacy",
            "AGENT_LEGION_ASR_WHISPER_MODEL",
            ["asr", "whisper", "model"],
            "/tmp/model.bin",
            "/tmp/model.bin",
        ),
        (
            "legacy",
            "AGENT_LEGION_ASR_SENSEVOICE_MODEL_DIR",
            ["asr", "sensevoice", "model_dir"],
            "/tmp/sensevoice",
            "/tmp/sensevoice",
        ),
        ("legacy", "AGENT_LEGION_OPENCLAW_CWD", ["openclaw", "cwd"], "/tmp/cwd", "/tmp/cwd"),
        (
            "split",
            "CMS_BASE_URL",
            ["cms", "base_url"],
            "http://cms.internal.example.cn/v2",
            "http://cms.internal.example.cn/v2",
        ),
        ("split", "AGENT_LEGION_CMS_TOKEN", ["cms", "token"], "env-token", "env-token"),
        (
            "split",
            "AGENT_LEGION_CMS_TOKEN_GEN_SECRET",
            ["cms", "token_gen", "secret"],
            "env-secret",
            "env-secret",
        ),
        (
            "split",
            "AGENT_LEGION_ASR_WHISPER_BINARY",
            ["asr", "whisper", "binary"],
            "/tmp/whisper-cli",
            "/tmp/whisper-cli",
        ),
        (
            "split",
            "AGENT_LEGION_ASR_WHISPER_MODEL",
            ["asr", "whisper", "model"],
            "/tmp/model.bin",
            "/tmp/model.bin",
        ),
        (
            "split",
            "AGENT_LEGION_ASR_SENSEVOICE_MODEL_DIR",
            ["asr", "sensevoice", "model_dir"],
            "/tmp/sensevoice",
            "/tmp/sensevoice",
        ),
        ("split", "AGENT_LEGION_OPENCLAW_CWD", ["openclaw", "cwd"], "/tmp/cwd", "/tmp/cwd"),
    ],
)
def test_env_override_precedes_yaml(
    tmp_path, monkeypatch, layout, env_var, config_path, env_value, expected
):
    # Skip the worktree .env: its BASECMS_* alias values would conflict with
    # the CMS_* names under test.
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.setenv(env_var, env_value)
    if layout == "legacy":
        config_path_file = tmp_path / "workflow.yaml"
        config_path_file.write_text(
            "data_dir: data\n"
            "asr:\n"
            "  provider: whisper\n"
            "  whisper:\n"
            "    binary: yaml-binary\n"
            "    model: yaml-model\n"
            "  sensevoice:\n"
            "    model_dir: yaml-dir\n"
            "workflows:\n"
            "  enabled: false\n"
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
