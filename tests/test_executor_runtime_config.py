from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.app.executors.runtime_config import (
    ExecutorRuntimeConfig,
    OpenClawRuntimeConfig,
    WorkflowsRuntimeConfig,
)


def test_openclaw_runtime_config_defaults():
    config = OpenClawRuntimeConfig()
    assert config.cwd == "."


def test_openclaw_runtime_config_valid_overrides():
    config = OpenClawRuntimeConfig(cwd="/tmp/openclaw")
    assert config.cwd == "/tmp/openclaw"


def test_openclaw_runtime_config_ignores_extra_fields():
    config = OpenClawRuntimeConfig(
        runners=[{"command_template": ["openclaw"], "count": 8}],
    )
    assert "runners" not in config.model_dump()


def test_openclaw_runtime_config_ignores_retired_knobs():
    config = OpenClawRuntimeConfig.model_validate(
        {
            "cwd": "/tmp/openclaw",
            "command_template": ["openclaw", "agent"],
            "timeout_seconds": 300,
            "isolated_workspace_root": "/tmp/isolated",
            "skill_safety": {"enabled": True, "repos": [{"path": "~/.skills/s1"}]},
        }
    )
    assert config.cwd == "/tmp/openclaw"
    assert "command_template" not in config.model_dump()
    assert "skill_safety" not in config.model_dump()


def test_openclaw_runtime_config_rejects_skill_safety_ref():
    """Refs are pinned by the DB skill_lock document (G3): a ref key must be
    rejected at startup even though the rest of the retired block is ignored."""
    with pytest.raises(ValidationError, match="ref"):
        OpenClawRuntimeConfig.model_validate(
            {
                "cwd": ".",
                "skill_safety": {
                    "enabled": True,
                    "repos": [{"path": "~/.skills/s1", "ref": "v1.0.0"}],
                },
            }
        )


def test_workflows_runtime_config_defaults():
    config = WorkflowsRuntimeConfig()
    # Default on: matches the retired tracked workflow.yaml value
    # (workflows.enabled: true).
    assert config.enabled is True


def test_workflows_runtime_config_valid_overrides():
    config = WorkflowsRuntimeConfig(enabled=False, custom_nodes_enabled=False)
    assert config.enabled is False
    assert config.custom_nodes_enabled is False


def test_executor_runtime_config_from_full_config():
    raw = {
        "data_dir": "data",
        "workflows": {
            "enabled": True,
        },
        "openclaw": {
            "cwd": ".",
            # Retired knobs are ignored (extra="ignore"), not validated.
            "command_template": ["openclaw", "agent"],
            "skill_safety": {"enabled": True, "repos": [{"path": "~/.skills/s1"}]},
        },
    }
    config = ExecutorRuntimeConfig.model_validate(raw)
    assert config.workflows.enabled is True
    assert config.openclaw.cwd == "."
    assert "skill_safety" not in config.openclaw.model_dump()
    assert "command_template" not in config.openclaw.model_dump()


def test_executor_runtime_config_parses_lease_heartbeat_settings():
    config = ExecutorRuntimeConfig.model_validate(
        {
            "heartbeat_interval_seconds": 7,
            "lease_ttl_seconds": 90,
            "heartbeat_failure_threshold": 3,
        }
    )
    assert config.heartbeat_interval_seconds == 7
    assert config.lease_ttl_seconds == 90
    assert config.heartbeat_failure_threshold == 3


def test_executor_runtime_config_sweeper_defaults():
    config = ExecutorRuntimeConfig.model_validate({})
    assert config.sweeper_enabled is True
    assert config.sweeper_interval_seconds == 5.0


def test_executor_runtime_config_sweeper_overrides():
    config = ExecutorRuntimeConfig.model_validate(
        {
            "sweeper_enabled": False,
            "sweeper_interval_seconds": 1.5,
        }
    )
    assert config.sweeper_enabled is False
    assert config.sweeper_interval_seconds == 1.5


def test_executor_runtime_config_rejects_non_positive_sweeper_interval():
    with pytest.raises(ValidationError) as exc_info:
        ExecutorRuntimeConfig.model_validate(
            {
                "sweeper_interval_seconds": 0,
            }
        )
    assert "sweeper_interval_seconds" in str(exc_info.value)


def test_executor_runtime_config_ignores_unknown_top_level_keys():
    raw = {
        "executors": {"legacy-default": {"kind": "unknown"}},
        "workflows": {"enabled": False},
    }
    config = ExecutorRuntimeConfig.model_validate(raw)
    assert config.workflows.enabled is False
