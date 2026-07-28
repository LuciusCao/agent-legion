from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.app.executors.runtime_config import (
    ExecutorRuntimeConfig,
    OpenClawRuntimeConfig,
    OpenClawSkillSafetyRuntimeConfig,
    PiRuntimeConfig,
    WorkflowsRuntimeConfig,
)


def test_pi_runtime_config_defaults():
    config = PiRuntimeConfig()
    assert config.binary == "pi"
    assert config.provider == ""
    assert config.model == ""
    assert config.thinking == ""
    assert config.timeout_seconds == 600
    assert config.environment == {}


def test_pi_runtime_config_valid_overrides():
    config = PiRuntimeConfig(
        binary="/usr/local/bin/pi",
        provider="openai",
        model="gpt-4",
        thinking="low",
        timeout_seconds=300,
        environment={"PI_SKIP_VERSION_CHECK": "1"},
    )
    assert config.binary == "/usr/local/bin/pi"
    assert config.provider == "openai"
    assert config.model == "gpt-4"
    assert config.thinking == "low"
    assert config.timeout_seconds == 300
    assert config.environment == {"PI_SKIP_VERSION_CHECK": "1"}


def test_pi_runtime_config_rejects_non_positive_timeout():
    with pytest.raises(ValidationError) as exc_info:
        PiRuntimeConfig(timeout_seconds=0)
    assert "timeout_seconds" in str(exc_info.value)


def test_pi_runtime_config_rejects_negative_timeout():
    with pytest.raises(ValidationError) as exc_info:
        PiRuntimeConfig(timeout_seconds=-1)
    assert "timeout_seconds" in str(exc_info.value)


@pytest.mark.parametrize(
    ("environment",),
    [
        (["PI_SKIP_VERSION_CHECK=1"],),
        ("PI_SKIP_VERSION_CHECK=1",),
        ({"PI_SKIP_VERSION_CHECK": ["1"]},),
    ],
)
def test_pi_runtime_config_rejects_invalid_environment(environment):
    with pytest.raises(ValidationError) as exc_info:
        PiRuntimeConfig(environment=environment)
    assert "environment" in str(exc_info.value)


def test_openclaw_skill_safety_defaults():
    config = OpenClawSkillSafetyRuntimeConfig()
    assert config.enabled is False
    assert config.repos == []


def test_openclaw_skill_safety_valid_overrides():
    config = OpenClawSkillSafetyRuntimeConfig(
        enabled=True,
        repos=[{"path": "~/.skills/s1"}],
    )
    assert config.enabled is True
    assert [repo.path for repo in config.repos] == ["~/.skills/s1"]


def test_openclaw_skill_safety_rejects_ref_override():
    """Refs are pinned by skills.lock (config governance G3); yaml ref is retired."""
    with pytest.raises(ValidationError) as exc_info:
        OpenClawSkillSafetyRuntimeConfig(
            enabled=True,
            repos=[{"path": "~/.skills/s1", "ref": "v1.0.0"}],
        )
    assert "ref" in str(exc_info.value)


def test_openclaw_skill_safety_rejects_invalid_repos_container():
    with pytest.raises(ValidationError) as exc_info:
        OpenClawSkillSafetyRuntimeConfig(enabled=True, repos={"path": "~/.skills/s1"})
    assert "repos" in str(exc_info.value)


def test_openclaw_runtime_config_defaults():
    config = OpenClawRuntimeConfig(command_template=("openclaw", "agent"))
    assert config.cwd == "."
    assert config.timeout_seconds == 600
    assert config.isolated_workspace_root == ""
    assert config.skill_safety.enabled is False
    assert config.skill_safety.repos == []


def test_openclaw_runtime_config_valid_overrides():
    config = OpenClawRuntimeConfig(
        command_template=("openclaw", "agent", "--local"),
        cwd="/tmp/openclaw",
        timeout_seconds=300,
        isolated_workspace_root="/tmp/isolated",
        skill_safety=OpenClawSkillSafetyRuntimeConfig(
            enabled=True,
            repos=[{"path": "~/.skills/s1"}],
        ),
    )
    assert config.command_template == ("openclaw", "agent", "--local")
    assert config.cwd == "/tmp/openclaw"
    assert config.timeout_seconds == 300
    assert config.isolated_workspace_root == "/tmp/isolated"
    assert config.skill_safety.enabled is True


def test_openclaw_runtime_config_rejects_empty_command_template():
    with pytest.raises(ValidationError) as exc_info:
        OpenClawRuntimeConfig(command_template=())
    assert "command_template" in str(exc_info.value)


def test_openclaw_runtime_config_rejects_non_positive_timeout():
    with pytest.raises(ValidationError) as exc_info:
        OpenClawRuntimeConfig(command_template=("openclaw",), timeout_seconds=0)
    assert "timeout_seconds" in str(exc_info.value)


def test_openclaw_runtime_config_ignores_extra_fields():
    config = OpenClawRuntimeConfig(
        command_template=("openclaw",),
        runners=[{"command_template": ["openclaw"], "count": 8}],
    )
    assert config.command_template == ("openclaw",)
    assert "runners" not in config.model_dump()


def test_workflows_runtime_config_defaults():
    config = WorkflowsRuntimeConfig()
    assert config.enabled is False
    assert config.pi.binary == "pi"


def test_workflows_runtime_config_valid_overrides():
    config = WorkflowsRuntimeConfig(
        enabled=True,
        pi=PiRuntimeConfig(binary="/usr/local/bin/pi", thinking="low"),
    )
    assert config.enabled is True
    assert config.pi.binary == "/usr/local/bin/pi"
    assert config.pi.thinking == "low"


def test_executor_runtime_config_from_full_config():
    raw = {
        "data_dir": "data",
        "workflows": {
            "enabled": True,
            "pi": {
                "binary": "pi",
                "provider": "",
                "model": "",
                "thinking": "low",
                "timeout_seconds": 600,
                "environment": {"PI_SKIP_VERSION_CHECK": "1"},
            },
        },
        "openclaw": {
            "cwd": ".",
            "timeout_seconds": 600,
            "skill_safety": {
                "enabled": True,
                "repos": [
                    {"path": "~/.openclaw/workspace/skills/s1"},
                ],
            },
            "command_template": [
                "openclaw",
                "agent",
                "--local",
                "--agent",
                "main",
            ],
            "runners": [{"command_template": ["openclaw"], "count": 8}],
        },
    }
    config = ExecutorRuntimeConfig.model_validate(raw)
    assert config.workflows.enabled is True
    assert config.workflows.pi.thinking == "low"
    assert config.openclaw.command_template == (
        "openclaw",
        "agent",
        "--local",
        "--agent",
        "main",
    )
    assert config.openclaw.skill_safety.enabled is True
    assert config.openclaw.skill_safety.repos[0].path == "~/.openclaw/workspace/skills/s1"


def test_executor_runtime_config_parses_lease_heartbeat_settings():
    config = ExecutorRuntimeConfig.model_validate(
        {
            "heartbeat_interval_seconds": 7,
            "lease_ttl_seconds": 90,
            "heartbeat_failure_threshold": 3,
            "openclaw": {"command_template": ["openclaw"]},
        }
    )
    assert config.heartbeat_interval_seconds == 7
    assert config.lease_ttl_seconds == 90
    assert config.heartbeat_failure_threshold == 3


def test_executor_runtime_config_sweeper_defaults():
    config = ExecutorRuntimeConfig.model_validate({"openclaw": {"command_template": ["openclaw"]}})
    assert config.sweeper_enabled is True
    assert config.sweeper_interval_seconds == 5.0


def test_executor_runtime_config_sweeper_overrides():
    config = ExecutorRuntimeConfig.model_validate(
        {
            "sweeper_enabled": False,
            "sweeper_interval_seconds": 1.5,
            "openclaw": {"command_template": ["openclaw"]},
        }
    )
    assert config.sweeper_enabled is False
    assert config.sweeper_interval_seconds == 1.5


def test_executor_runtime_config_rejects_non_positive_sweeper_interval():
    with pytest.raises(ValidationError) as exc_info:
        ExecutorRuntimeConfig.model_validate(
            {
                "sweeper_interval_seconds": 0,
                "openclaw": {"command_template": ["openclaw"]},
            }
        )
    assert "sweeper_interval_seconds" in str(exc_info.value)


def test_executor_runtime_config_rejects_missing_command_template():
    raw = {"workflows": {"enabled": True}, "openclaw": {"cwd": "."}}
    with pytest.raises(ValidationError) as exc_info:
        ExecutorRuntimeConfig.model_validate(raw)
    assert "command_template" in str(exc_info.value)


def test_executor_runtime_config_rejects_wrong_timeout_scalar_type():
    raw = {
        "workflows": {"enabled": True, "pi": {"timeout_seconds": "fast"}},
        "openclaw": {"command_template": ["openclaw"], "timeout_seconds": "slow"},
    }
    with pytest.raises(ValidationError) as exc_info:
        ExecutorRuntimeConfig.model_validate(raw)
    errors = exc_info.value.errors()
    assert any("timeout_seconds" in str(e["loc"]) for e in errors)


def test_executor_runtime_config_ignores_unknown_top_level_keys():
    raw = {
        "executors": {"local-default": {"kind": "unknown"}},
        "workflows": {"enabled": False},
        "openclaw": {"command_template": ["openclaw"]},
    }
    config = ExecutorRuntimeConfig.model_validate(raw)
    assert config.workflows.enabled is False
