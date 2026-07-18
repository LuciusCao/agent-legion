from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.app.executors.config import (
    RemoteExecutorConfig,
    load_executor_definitions,
)
from server.app.executors.runtime_config import ExecutorRuntimeConfig


def test_remote_executor_config_parses():
    defs = load_executor_definitions(
        {
            "pi-remote": {
                "kind": "remote",
                "global_capacity": 100,
                "capabilities": {
                    "generate_key_info": {
                        "skill": "question_comprehension_info/generate_key_info",
                        "tools": ["read", "write", "bash"],
                    }
                },
            }
        }
    )
    config = defs["pi-remote"]
    assert isinstance(config, RemoteExecutorConfig)
    assert config.kind == "remote"
    assert config.global_capacity == 100
    assert config.capabilities["generate_key_info"].tools == ("read", "write", "bash")


def test_remote_executor_config_defaults_tools():
    defs = load_executor_definitions(
        {
            "pi-remote": {
                "kind": "remote",
                "global_capacity": 10,
                "capabilities": {"cap": {"skill": "wf/cap"}},
            }
        }
    )
    assert defs["pi-remote"].capabilities["cap"].tools == ("read", "write", "bash")


@pytest.mark.parametrize("skill", ["/abs/path", "a/../b"])
def test_remote_capability_rejects_unsafe_skill(skill):
    with pytest.raises(ValidationError):
        load_executor_definitions(
            {
                "pi-remote": {
                    "kind": "remote",
                    "global_capacity": 10,
                    "capabilities": {"cap": {"skill": skill}},
                }
            }
        )


def test_remote_executor_rejects_empty_capability_name():
    with pytest.raises(ValidationError):
        load_executor_definitions(
            {
                "pi-remote": {
                    "kind": "remote",
                    "global_capacity": 10,
                    "capabilities": {"": {"skill": "wf/cap"}},
                }
            }
        )


def test_runtime_config_remote_defaults():
    runtime = ExecutorRuntimeConfig.model_validate({})
    assert runtime.remote.worker_token == ""
    assert runtime.remote.claim_timeout_seconds == 120.0
    assert runtime.remote.requeue_limit == 3
    assert runtime.remote.max_archive_bytes == 64 * 1024 * 1024


def test_runtime_config_remote_round_trip():
    runtime = ExecutorRuntimeConfig.model_validate(
        {"remote": {"worker_token": "secret", "claim_timeout_seconds": 30}}
    )
    assert runtime.remote.worker_token == "secret"
    assert runtime.remote.claim_timeout_seconds == 30.0
