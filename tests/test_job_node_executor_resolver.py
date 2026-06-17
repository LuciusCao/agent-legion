from dataclasses import dataclass
from typing import Any

from server.app.services.job_node_executor_resolver import resolve_node_executors
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)


@dataclass
class FakeExecutorConfig:
    kind: str


class FakeWorkspaceExecutorConfigurationService:
    def __init__(self, configs: dict[str, dict[str, Any]] | None = None, fail: bool = False):
        self._configs = configs or {}
        self._fail = fail

    def get(self, workspace_id: str) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("boom")
        return self._configs.get(workspace_id, {"bindings": []})


class FakeSettings:
    def __init__(self, executor_definitions: dict[str, FakeExecutorConfig] | None = None):
        self.executor_definitions = executor_definitions or {}


def test_resolve_node_executors_returns_empty_when_config_raises():
    service = FakeWorkspaceExecutorConfigurationService(fail=True)
    settings = FakeSettings()

    result = resolve_node_executors("missing", "question_content", service, settings)

    assert result == {}


def test_resolve_node_executors_maps_node_keys_to_executor_id_and_kind():
    service = FakeWorkspaceExecutorConfigurationService(
        configs={
            "ws1": {
                "bindings": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "node_a",
                        "executor_id": "local-default",
                    },
                    {
                        "pipeline_key": "question_content",
                        "node_key": "node_b",
                        "executor_id": "pi-default",
                    },
                    {
                        "pipeline_key": "reading_analysis",
                        "node_key": "node_a",
                        "executor_id": "pi-default",
                    },
                ]
            }
        }
    )
    settings = FakeSettings(
        executor_definitions={
            "local-default": FakeExecutorConfig("local"),
            "pi-default": FakeExecutorConfig("pi"),
        }
    )

    result = resolve_node_executors("ws1", "question_content", service, settings)

    assert result == {
        "node_a": ("local-default", "local"),
        "node_b": ("pi-default", "pi"),
    }


def test_resolve_node_executors_returns_none_kind_for_unknown_executor():
    service = FakeWorkspaceExecutorConfigurationService(
        configs={
            "ws1": {
                "bindings": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "node_a",
                        "executor_id": "unknown-executor",
                    },
                ]
            }
        }
    )
    settings = FakeSettings(executor_definitions={})

    result = resolve_node_executors("ws1", "question_content", service, settings)

    assert result == {"node_a": ("unknown-executor", None)}


def test_resolve_node_executors_skips_bindings_without_node_key():
    service = FakeWorkspaceExecutorConfigurationService(
        configs={
            "ws1": {
                "bindings": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "node_a",
                        "executor_id": "local-default",
                    },
                    {"pipeline_key": "question_content", "executor_id": "pi-default"},
                ]
            }
        }
    )
    settings = FakeSettings(executor_definitions={"local-default": FakeExecutorConfig("local")})

    result = resolve_node_executors("ws1", "question_content", service, settings)

    assert result == {"node_a": ("local-default", "local")}


def test_resolve_node_executors_with_real_service(job_db, settings):
    workspace = job_db.create_workspace("ws1")
    job_db.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[{"executor_id": "local-default", "concurrency_limit": 1}],
        bindings=[
            {
                "pipeline_key": "question_content",
                "node_key": "assemble_package",
                "executor_id": "local-default",
            }
        ],
        node_limits=[],
    )

    config_service = WorkspaceExecutorConfigurationService(job_db)

    result = resolve_node_executors(workspace["id"], "question_content", config_service, settings)

    assert result == {"assemble_package": ("local-default", "local")}
