import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.app.settings import load_env_file, load_settings


@pytest.fixture(autouse=True)
def _clear_agent_legion_env(monkeypatch):
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
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text("database: {url: postgresql://configured/app}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", "fernet-key-value")
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", "/run/secrets/vault_key")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    vault = settings.config["vault"]
    assert vault["master_key"] == "fernet-key-value"
    assert vault["master_key_file"] == "/run/secrets/vault_key"


def test_database_url_environment_override(tmp_path, monkeypatch):
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text("database: {url: postgresql://configured/app}\n", encoding="utf-8")
    # Skip the worktree .env so only the names set below are in play.
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.delenv("AGENT_LEGION_DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENT_LEGION_DATABASE_URL", "postgresql://override/test")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.database_url == "postgresql://override/test"


def test_sqlite_database_url_is_rejected(tmp_path, monkeypatch):
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text("database: {url: data/app.sqlite}\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_LEGION_DATABASE_URL", raising=False)
    # Worktrees carry a real AGENT_LEGION_DATABASE_URL in the project .env;
    # skip it so the yaml value below is the one under test.
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")

    with pytest.raises(ValueError, match="PostgreSQL URL"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)


def test_load_settings_rejects_retired_yaml_cms_token(tmp_path):
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\ncms: {token: yaml-token}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"cms\.token") as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    message = str(exc_info.value)
    assert "外部服务连接" in message


def test_load_settings_rejects_retired_yaml_cms_token_gen(tmp_path):
    config_path = tmp_path / "explicit.yaml"
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
    assert "外部服务连接" in message


def test_load_settings_rejects_retired_yaml_cms_section_with_non_token_keys(tmp_path):
    """The whole yaml ``cms:`` section is dead config: any key fails startup."""
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\ncms: {endpoint: http://yaml/cms}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"cms\.endpoint") as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    message = str(exc_info.value)
    assert "外部服务连接" in message


@pytest.mark.parametrize("cms_block", ["cms:\n", "cms: {}\n"])
def test_load_settings_rejects_empty_retired_yaml_cms_section(tmp_path, cms_block):
    """An empty ``cms:`` block (None or {}) is still the retired section."""
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\n" + cms_block,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"Unsupported yaml section: cms") as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    message = str(exc_info.value)
    assert "外部服务连接" in message


def test_split_layout_rejects_retired_agent_legion_yaml(tmp_path, monkeypatch):
    """config/agent_legion.yaml is retired: its presence fails startup with guidance."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_legion.yaml").write_text("asr: {provider: auto}\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match=r"retired.*agent_legion\.yaml") as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert "node configuration in Studio" in message
    assert "instance-settings" in message


def test_load_settings_reads_project_dotenv_by_default(tmp_path, monkeypatch):
    # The split layout carries zero files (agent_legion.yaml is retired); the
    # config dict is built from code defaults plus env overrides.
    (tmp_path / ".env").write_text("AGENT_LEGION_OPENCLAW_CWD=dotenv-cwd\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("AGENT_LEGION_SKIP_DOTENV", raising=False)

    try:
        settings = load_settings()
    finally:
        # load_env_file writes os.environ directly (bypassing monkeypatch);
        # keep the shared worker process clean for later tests.
        os.environ.pop("AGENT_LEGION_OPENCLAW_CWD", None)

    assert settings.config["openclaw"]["cwd"] == "dotenv-cwd"


def test_load_settings_can_skip_project_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("AGENT_LEGION_OPENCLAW_CWD=dotenv-cwd\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")

    settings = load_settings()

    # With the dotenv skipped, the env override never lands.
    assert settings.config.get("openclaw", {}).get("cwd") != "dotenv-cwd"


def test_default_split_layout_builds_effective_settings(tmp_path, monkeypatch):
    # Zero split config files: the canonical layout starts from code defaults.
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)
    # data_dir is env-only after the app.yaml retirement.
    monkeypatch.setenv("AGENT_LEGION_DATA_DIR", str(tmp_path / "runtime"))
    settings = load_settings()
    assert settings.data_dir == tmp_path / "runtime"


def test_split_layout_rejects_retired_app_yaml(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text("data_dir: runtime\n", encoding="utf-8")
    monkeypatch.setattr("server.app.settings.PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match=r"retired.*app\.yaml"):
        load_settings()


def test_explicit_path_does_not_inspect_partial_neighbor_layout(tmp_path):
    (tmp_path / "app.yaml").write_text("data_dir: ignored\n", encoding="utf-8")
    explicit = tmp_path / "custom.yaml"
    explicit.write_text("data_dir: selected\n", encoding="utf-8")
    settings = load_settings(data_dir=tmp_path / "data", config_path=explicit)
    assert settings.config["data_dir"] == "selected"
    # The neighbor app.yaml must not influence the explicit configuration.
    assert settings.config.get("server") is None
    assert settings.config.get("worker") is None


def test_load_settings_ignores_executors_yaml_section(tmp_path, monkeypatch):
    """Executor definitions are retired (schema v47, P-0.5): yaml is inert.

    A stray ``executors:`` section in an explicit config is ignored rather
    than validated — there is no executor catalog left to hydrate.
    """
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

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.config["data_dir"] == "data"


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


def test_load_settings_exposes_executor_runtime(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\nworkflows:\n  enabled: true\nopenclaw:\n  cwd: .\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.executor_runtime.workflows.enabled is True
    assert settings.executor_runtime.openclaw.cwd == "."
    # 退役的 openclaw 旋钮（command_template/timeout_seconds/skill_safety）随
    # 配置面清理移除：extra="ignore" 使 yaml 里的残留键被静默丢弃。


def test_load_settings_ignores_retired_openclaw_knobs(tmp_path, monkeypatch):
    """Retired openclaw keys are ignored, not validated."""
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "openclaw:\n"
        "  cwd: .\n"
        "  timeout_seconds: 600\n"
        "  command_template: []\n"
        "  skill_safety:\n"
        "    enabled: true\n"
        "    repos:\n"
        "      - path: ~/.openclaw/workspace/skills/s1\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.executor_runtime.openclaw.cwd == "."


def test_load_settings_rejects_skill_safety_ref(tmp_path, monkeypatch):
    """skill_safety refs stay rejected at startup (config governance G3): the
    DB skill_lock document is the single source of truth for refs, so the
    retired-key ignore rule must not swallow a ref silently."""
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "openclaw:\n"
        "  cwd: .\n"
        "  skill_safety:\n"
        "    enabled: true\n"
        "    repos:\n"
        "      - path: ~/.openclaw/workspace/skills/s1\n"
        "        ref: v1.0.0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="ref"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)


@pytest.mark.parametrize(
    ("layout", "env_var", "config_path", "env_value", "expected"),
    [
        ("legacy", "AGENT_LEGION_OPENCLAW_CWD", ["openclaw", "cwd"], "/tmp/cwd", "/tmp/cwd"),
        ("split", "AGENT_LEGION_OPENCLAW_CWD", ["openclaw", "cwd"], "/tmp/cwd", "/tmp/cwd"),
    ],
)
def test_env_override_precedes_yaml(
    tmp_path, monkeypatch, layout, env_var, config_path, env_value, expected
):
    # Skip the worktree .env so only the names set below are in play.
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.setenv(env_var, env_value)
    if layout == "legacy":
        config_path_file = tmp_path / "workflow.yaml"
        config_path_file.write_text(
            "data_dir: data\nworkflows:\n  enabled: false\nopenclaw:\n  cwd: yaml-cwd\n",
            encoding="utf-8",
        )
    else:
        # The split layout carries zero files (agent_legion.yaml is retired):
        # env overrides land on the code-default config dict.
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


def test_load_settings_rejects_retired_register_token_config(tmp_path, monkeypatch):
    """issue #35：全局 register token 退役后，遗留 yaml 键与 env 变量都 fail-fast。"""
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")

    config_path = tmp_path / "explicit.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\n"
        "agent_workers:\n"
        "  register_token: legacy-secret\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"agent_workers\.register_token"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    config_path.write_text(
        "database: {url: postgresql://configured/app}\n"
        "agent_workers:\n"
        "  register_token_file: /run/secrets/legacy\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"agent_workers\.register_token_file"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    config_path.write_text("database: {url: postgresql://configured/app}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_LEGION_WORKER_REGISTER_TOKEN", "legacy-secret")
    with pytest.raises(ValueError, match="AGENT_LEGION_WORKER_REGISTER_TOKEN"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)
    monkeypatch.delenv("AGENT_LEGION_WORKER_REGISTER_TOKEN")
    monkeypatch.setenv("AGENT_LEGION_WORKER_REGISTER_TOKEN_FILE", "/run/secrets/legacy")
    with pytest.raises(ValueError, match="AGENT_LEGION_WORKER_REGISTER_TOKEN_FILE"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)


def test_load_settings_skills_runs_dir_defaults_to_temp(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.delenv("AGENT_LEGION_SKILLS_RUNS_DIR", raising=False)
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text("{}\n", encoding="utf-8")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    from server.app.skills.paths import default_skills_runs_dir

    assert settings.skills_runs_dir == default_skills_runs_dir()
    assert "agent-legion-skills.runs" in settings.skills_runs_dir.name


def test_load_settings_skills_runs_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    override = tmp_path / "pinned-scratch"
    monkeypatch.setenv("AGENT_LEGION_SKILLS_RUNS_DIR", str(override))
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text("{}\n", encoding="utf-8")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.skills_runs_dir == override


def test_env_example_documents_skills_runs_dir():
    example_path = Path(__file__).resolve().parents[1] / ".env.example"
    example = example_path.read_text(encoding="utf-8")
    assert "AGENT_LEGION_SKILLS_RUNS_DIR=" in example
