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
    "fetch_questions",
    "download_video",
    "clean_and_parse",
    "classify_comprehension_eligibility",
    "finalize_non_uploadable",
    "assemble_comprehension_info",
    "transcribe_video",
    "assemble_video_metadata",
    "package_video_job",
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
    # Network-enabled capabilities: CMS forks and the ASR providers.
    assert {name for name, cap in capabilities.items() if cap.sandbox_network} == {
        "fetch_questions",
        "download_video",
        "transcribe_video",
    }
    # The CMS connection schema factory defaults survive (node/workspace can
    # override): endpoint URLs and credentials live on the instance-level
    # external connection, the schema only carries the connection key plus
    # non-secret business selectors.
    fetch_schema = capabilities["fetch_questions"].config_schema["properties"]
    assert fetch_schema["connection"]["default"] == "cms-internal"
    assert fetch_schema["bank_version"]["default"] == "v5"
    assert fetch_schema["country_id"]["default"] == "1"
    assert fetch_schema["subject_id"]["default"] == "2"
    assert fetch_schema["page_size"]["default"] == 50
    assert "token" not in fetch_schema
    download_schema = capabilities["download_video"].config_schema["properties"]
    assert download_schema["connection"]["default"] == "cms-internal"
    assert download_schema["bank_version"]["default"] == "v5"
    assert "page_size" not in download_schema
    assert "token" not in download_schema
    # The retired yaml asr: section's business parameters live here (factory
    # defaults; node/workspace can override). Machine paths stay env-only.
    transcribe_schema = capabilities["transcribe_video"].config_schema["properties"]
    assert transcribe_schema["provider"]["enum"] == ["auto", "whisper", "sensevoice"]
    assert transcribe_schema["provider"]["default"] == "auto"
    assert transcribe_schema["timeout_seconds"]["default"] == 900
    assert transcribe_schema["timeout_seconds"]["minimum"] == 1
