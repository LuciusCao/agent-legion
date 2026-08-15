"""Built-in executor definitions: factory catalog content and model validation.

The constants were transcribed field-for-field from the retired tracked
``config/workflow.yaml`` executors section (equivalence was pinned by a
yaml-comparison test before the file was deleted); this suite now guards the
constant against accidental drift: it must parse through the same loader the
runtime uses and every capability path must resolve inside the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors.builtin_definitions import BUILTIN_EXECUTOR_DEFINITIONS
from server.app.executors.code_config import CodeExecutorConfig, validate_code_config_paths
from server.app.executors.definitions import load_executor_definitions

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CAPABILITIES = {
    # Demo workflow code capabilities (example nodes).
    "intake_knowledge_points",
    "publish_content",
}


@pytest.mark.no_db
def test_builtin_definitions_parse_and_resolve_to_repo_files() -> None:
    definitions = load_executor_definitions(BUILTIN_EXECUTOR_DEFINITIONS)
    assert set(definitions) == {"code-default"}
    config = definitions["code-default"]
    assert isinstance(config, CodeExecutorConfig)
    assert config.global_capacity == 16
    assert set(config.capabilities) == EXPECTED_CAPABILITIES
    assert validate_code_config_paths(definitions, REPO_ROOT) == []


@pytest.mark.no_db
def test_builtin_definitions_preserve_factory_semantics() -> None:
    config = load_executor_definitions(BUILTIN_EXECUTOR_DEFINITIONS)["code-default"]
    assert isinstance(config, CodeExecutorConfig)
    capabilities = config.capabilities
    # Demo nodes are pure-stdlib and network-free.
    assert {name for name, cap in capabilities.items() if cap.sandbox_network} == set()
    # The intake node's knowledge-dir default survives (node/workspace can
    # override) and resolves relative to the repo root.
    intake_schema = capabilities["intake_knowledge_points"].config_schema["properties"]
    assert (
        intake_schema["knowledge_dir"]["default"] == "examples/education-video-problems-generation"
    )
    assert "publish_content" in capabilities
