from __future__ import annotations

from copy import deepcopy

import pytest

from server.app.routes.instance_settings_contracts import (
    InstanceAgentClaimSettings,
    InstanceAgentEnqueueSettings,
    InstanceAgentWorkersSettings,
    InstanceCleanupSettings,
    InstanceMonitoringSettings,
    InstanceResultUnpackSettings,
    InstanceResultValidateSettings,
    InstanceSettingsResponse,
    InstanceSettingsUpdate,
    InstanceWorkflowsSettings,
)
from server.app.routes.instance_settings_core_contracts import (
    InstanceCleanupSettings as CoreCleanupSettings,
)
from server.app.routes.instance_settings_core_contracts import (
    InstanceMonitoringSettings as CoreMonitoringSettings,
)
from server.app.routes.instance_settings_execution_contracts import (
    InstanceAgentClaimSettings as ExecutionAgentClaimSettings,
)
from server.app.routes.instance_settings_execution_contracts import (
    InstanceAgentEnqueueSettings as ExecutionAgentEnqueueSettings,
)
from server.app.routes.instance_settings_execution_contracts import (
    InstanceAgentWorkersSettings as ExecutionAgentWorkersSettings,
)
from server.app.routes.instance_settings_execution_contracts import (
    InstanceResultUnpackSettings as ExecutionResultUnpackSettings,
)
from server.app.routes.instance_settings_execution_contracts import (
    InstanceResultValidateSettings as ExecutionResultValidateSettings,
)
from server.app.routes.instance_settings_execution_contracts import (
    InstanceWorkflowsSettings as ExecutionWorkflowsSettings,
)
from server.app.skills.skill_roots import SKILLS_ROOT_DISPLAY

pytestmark = pytest.mark.no_db


def _payload() -> dict:
    return {
        "cleanup": {
            "log_retention_days": 7,
            "run_dir_retention_days": 3,
            "interval_seconds": 3600,
        },
        "monitoring": {"sample_interval_seconds": 60.0, "retention_days": 30},
        "heartbeat_interval_seconds": 10.0,
        "lease_ttl_seconds": 90,
        "heartbeat_failure_threshold": 3,
        "sweeper_enabled": True,
        "sweeper_interval_seconds": 5.0,
        "code_capacity": 16,
        "materials_ttl_days": 0,
        "execution_retention_days": 0,
        "studio_chat_retention_days": 0,
        "studio_chat_terminal_grant_required": False,
        "workflows": {"max_items_per_run": 20_000, "node_code_max_bytes": 65_536},
        "agent_workers": {
            "max_archive_bytes": 67_108_864,
            "min_protocol_version": 1,
            "max_concurrent_result_commits": 16,
            "result_commit_batching": True,
            "artifact_spot_check_percent": 3,
            "artifact_download_presign_ttl_seconds": 3600,
        },
        "agent_enqueue": {"workers": 48, "max_pending": 1024},
        "result_unpack": {"workers": 0},
        "result_validate": {"workers": 0},
        "agent_claim": {"worker_touch_interval_seconds": 30.0},
        "csp_script_unsafe_inline": False,
    }


def test_original_module_reexports_split_models() -> None:
    assert InstanceCleanupSettings is CoreCleanupSettings
    assert InstanceMonitoringSettings is CoreMonitoringSettings
    assert InstanceWorkflowsSettings is ExecutionWorkflowsSettings
    assert InstanceAgentWorkersSettings is ExecutionAgentWorkersSettings
    assert InstanceAgentEnqueueSettings is ExecutionAgentEnqueueSettings
    assert InstanceResultUnpackSettings is ExecutionResultUnpackSettings
    assert InstanceResultValidateSettings is ExecutionResultValidateSettings
    assert InstanceAgentClaimSettings is ExecutionAgentClaimSettings


def test_update_schema_preserves_all_fields_and_nested_models() -> None:
    schema = InstanceSettingsUpdate.model_json_schema()

    assert set(schema["properties"]) == set(_payload())
    assert set(schema["required"]) == set(_payload()) - {"studio_chat_terminal_grant_required"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["studio_chat_terminal_grant_required"]["default"] is False
    assert set(schema["$defs"]) == {
        "InstanceCleanupSettings",
        "InstanceMonitoringSettings",
        "InstanceWorkflowsSettings",
        "InstanceAgentWorkersSettings",
        "InstanceAgentEnqueueSettings",
        "InstanceResultUnpackSettings",
        "InstanceResultValidateSettings",
        "InstanceAgentClaimSettings",
    }
    for definition in schema["$defs"].values():
        assert definition["additionalProperties"] is False


def test_split_models_keep_validation_constraints() -> None:
    definitions = InstanceSettingsUpdate.model_json_schema()["$defs"]

    assert (
        definitions["InstanceWorkflowsSettings"]["properties"]["node_code_max_bytes"]["minimum"]
        == 1024
    )
    worker_fields = definitions["InstanceAgentWorkersSettings"]["properties"]
    assert worker_fields["max_archive_bytes"]["minimum"] == 1024
    assert worker_fields["artifact_spot_check_percent"]["maximum"] == 100
    assert worker_fields["artifact_download_presign_ttl_seconds"]["minimum"] == 60
    assert worker_fields["artifact_download_presign_ttl_seconds"]["maximum"] == 604_800
    enqueue_fields = definitions["InstanceAgentEnqueueSettings"]["properties"]
    assert enqueue_fields["workers"]["minimum"] == 1
    assert enqueue_fields["workers"]["maximum"] == 256
    for name in ("InstanceResultUnpackSettings", "InstanceResultValidateSettings"):
        assert definitions[name]["properties"]["workers"]["minimum"] == 0
        assert definitions[name]["properties"]["workers"]["maximum"] == 64
    claim_fields = definitions["InstanceAgentClaimSettings"]["properties"]
    assert claim_fields["worker_touch_interval_seconds"]["minimum"] == 0
    assert claim_fields["worker_touch_interval_seconds"]["maximum"] == 86_400


def test_update_serialization_and_response_defaults_are_unchanged() -> None:
    payload = _payload()

    update = InstanceSettingsUpdate.model_validate(payload)
    assert update.model_dump(mode="json", exclude_unset=True) == payload
    response = InstanceSettingsResponse.model_validate(payload)
    assert response.model_dump(mode="json") == {
        **payload,
        "skills_root": SKILLS_ROOT_DISPLAY,
    }

    legacy_payload = deepcopy(payload)
    del legacy_payload["studio_chat_terminal_grant_required"]
    legacy_update = InstanceSettingsUpdate.model_validate(legacy_payload)
    assert legacy_update.studio_chat_terminal_grant_required is False
    assert "studio_chat_terminal_grant_required" not in legacy_update.model_dump(exclude_unset=True)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("workflows", "node_code_max_bytes"), 1023),
        (("agent_workers", "artifact_download_presign_ttl_seconds"), 604_801),
        (("agent_enqueue", "workers"), 257),
        (("result_unpack", "workers"), 65),
        (("result_validate", "workers"), -1),
        (("agent_claim", "worker_touch_interval_seconds"), 86_401),
        (("csp_script_unsafe_inline",), "yes"),
    ],
)
def test_update_still_rejects_invalid_values(path: tuple[str, ...], value: object) -> None:
    payload = deepcopy(_payload())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError):
        InstanceSettingsUpdate.model_validate(payload)
