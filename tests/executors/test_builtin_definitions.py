"""Built-in executor definitions: factory catalog content and model validation.

The constants were transcribed field-for-field from the retired tracked
``config/workflow.yaml`` executors section (equivalence was pinned by a
yaml-comparison test before the file was deleted); this suite now guards the
constant against accidental drift: it must parse through the same loader the
runtime uses, and (since #96) no capability may carry the retired ``path``
key — the demo node code reaches the DB via the global factory seed instead.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from server.app.executors.builtin_definitions import BUILTIN_EXECUTOR_DEFINITIONS
from server.app.executors.code_config import CodeExecutorConfig, strip_retired_path_keys
from server.app.executors.definitions import load_executor_definitions

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CAPABILITIES = {
    # Demo workflow code capabilities (example nodes).
    "intake_knowledge_points",
    "publish_content",
}


@pytest.mark.no_db
def test_builtin_definitions_parse_and_carry_no_path() -> None:
    definitions = load_executor_definitions(BUILTIN_EXECUTOR_DEFINITIONS)
    assert set(definitions) == {"code-default"}
    config = definitions["code-default"]
    assert isinstance(config, CodeExecutorConfig)
    assert config.global_capacity == 16
    assert set(config.capabilities) == EXPECTED_CAPABILITIES
    # The capability path binding is retired (#96): nothing to resolve.
    for raw in BUILTIN_EXECUTOR_DEFINITIONS.values():
        for capability in raw["capabilities"].values():
            assert "path" not in capability


@pytest.mark.no_db
def test_legacy_path_keys_are_stripped_at_load() -> None:
    """Stored pre-#96 definitions may still carry ``path``: the loader drops
    it with a warning instead of crashing hydration (immutable entities)."""
    legacy = {
        "code-default": {
            "kind": "code",
            "global_capacity": 4,
            "capabilities": {"fetch_items": {"path": "workflow_nodes/example_intake.py"}},
        }
    }
    definitions = load_executor_definitions(legacy)
    assert set(definitions["code-default"].capabilities) == {"fetch_items"}


@pytest.mark.no_db
def test_path_strip_warning_logged_once_per_executor(caplog) -> None:
    """The ~5s published-catalog cache re-strips legacy keys on every
    refresh; the warning fires once per executor per process, not per parse,
    so a stale stored definition cannot spam the logs."""
    legacy = {
        "kind": "code",
        "global_capacity": 4,
        "capabilities": {"fetch_items": {"path": "workflow_nodes/example_intake.py"}},
    }
    with caplog.at_level(logging.WARNING, logger="server.app.executors.code_config"):
        strip_retired_path_keys("code-warn-dedup-a", dict(legacy))
        strip_retired_path_keys("code-warn-dedup-a", dict(legacy))
        strip_retired_path_keys("code-warn-dedup-b", dict(legacy))
    warnings = [
        record
        for record in caplog.records
        if "dropped retired capability path key" in record.getMessage()
    ]
    assert len(warnings) == 2  # one per executor, not per call


@pytest.mark.no_db
def test_builtin_definitions_preserve_factory_semantics() -> None:
    config = load_executor_definitions(BUILTIN_EXECUTOR_DEFINITIONS)["code-default"]
    assert isinstance(config, CodeExecutorConfig)
    capabilities = config.capabilities
    # Demo nodes are pure-stdlib and network-free.
    assert {name for name, cap in capabilities.items() if cap.sandbox_network} == set()
    # The intake node's knowledge-dir default survives (node/workspace can
    # override) and resolves relative to the host root.
    intake_schema = capabilities["intake_knowledge_points"].config_schema["properties"]
    assert (
        intake_schema["knowledge_dir"]["default"] == "examples/education-video-problems-generation"
    )
    assert "publish_content" in capabilities
