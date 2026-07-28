"""Resource provider declaration loading from config yaml (spec D11)."""

from __future__ import annotations

import pytest
import yaml

from server.app.config_schema import ConfigSchemaError
from server.app.settings import load_settings
from server.app.workflows.resource_providers import (
    PROJECT_ROOT,
    RESOURCE_PROVIDER_SCHEMAS,
    RESOURCE_PROVIDERS,
    load_resource_provider_declarations,
)

_COMMON_PROPERTIES = {
    "api_url": {"type": "string"},
    "bank_version": {"type": "string"},
    "country_id": {"type": "string"},
    "subject_id": {"type": "string"},
    "env": {"type": "string"},
    "token": {"type": "string", "secret": True},
}

# The exact schemas previously hardcoded in resource_schemas.py (spec D10).
EXPECTED_SCHEMAS = {
    "question_detail": {
        "type": "object",
        "properties": dict(_COMMON_PROPERTIES),
    },
    "by_knowledge": {
        "type": "object",
        "properties": {
            **_COMMON_PROPERTIES,
            "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
        },
    },
}

# The exact mapping previously hardcoded as RESOURCE_PROVIDERS in resources.py.
EXPECTED_PROVIDERS = {
    "question_detail": {
        "provider": "cms.question.detail",
        "url_key": "question_detail_url",
    },
    "by_knowledge": {
        "provider": "cms.question.list_by_knowledge",
        "url_key": "question_list_url",
    },
}


def _repo_declarations():
    raw = yaml.safe_load((PROJECT_ROOT / "config" / "video_hive.yaml").read_text(encoding="utf-8"))
    return load_resource_provider_declarations(raw["resource_providers"])


def test_yaml_declarations_match_previous_hardcoded_version():
    declarations = _repo_declarations()
    assert declarations.providers == EXPECTED_PROVIDERS
    assert declarations.schemas == EXPECTED_SCHEMAS


def test_module_constants_are_loaded_from_yaml():
    assert RESOURCE_PROVIDERS == EXPECTED_PROVIDERS
    assert RESOURCE_PROVIDER_SCHEMAS == EXPECTED_SCHEMAS


def test_none_section_yields_empty_declarations():
    declarations = load_resource_provider_declarations(None)
    assert declarations.providers == {}
    assert declarations.schemas == {}


def test_non_mapping_section_rejected():
    with pytest.raises(ConfigSchemaError, match="resource_providers must be a mapping"):
        load_resource_provider_declarations(["cms.question.detail"])


def test_non_mapping_provider_entry_rejected():
    with pytest.raises(ConfigSchemaError, match="cms.question.detail must be a mapping"):
        load_resource_provider_declarations({"cms.question.detail": "path-only"})


def test_missing_resource_key_rejected():
    with pytest.raises(ConfigSchemaError, match="resource_key must be a non-empty string"):
        load_resource_provider_declarations({"cms.question.detail": {"path": "/q/detail"}})


def test_duplicate_resource_key_rejected():
    with pytest.raises(ConfigSchemaError, match="declared by multiple providers"):
        load_resource_provider_declarations(
            {
                "cms.question.detail": {"resource_key": "question_detail"},
                "cms.other.detail": {"resource_key": "question_detail"},
            }
        )


def test_non_string_url_key_rejected():
    with pytest.raises(ConfigSchemaError, match="url_key must be a string"):
        load_resource_provider_declarations(
            {"cms.question.detail": {"resource_key": "question_detail", "url_key": 3}}
        )


def test_invalid_config_schema_rejected():
    with pytest.raises(ConfigSchemaError, match="config_schema"):
        load_resource_provider_declarations(
            {
                "cms.question.detail": {
                    "resource_key": "question_detail",
                    "config_schema": {
                        "type": "object",
                        "properties": {"token": {"type": "uuid"}},
                    },
                }
            }
        )


def test_load_settings_accepts_valid_resource_provider_declarations(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_HIVE_SKIP_DOTENV", "1")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\n"
        "resource_providers:\n"
        "  cms.question.detail:\n"
        "    resource_key: question_detail\n"
        "    url_key: question_detail_url\n"
        "    path: /question/detail\n"
        "    config_schema:\n"
        "      type: object\n"
        "      properties:\n"
        "        token: {type: string, secret: true}\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.config["resource_providers"]["cms.question.detail"]["resource_key"] == (
        "question_detail"
    )


def test_load_settings_rejects_invalid_resource_provider_declarations(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_HIVE_SKIP_DOTENV", "1")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        "database: {url: postgresql://configured/app}\n"
        "resource_providers:\n"
        "  cms.question.detail:\n"
        "    path: /question/detail\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigSchemaError, match="resource_key"):
        load_settings(data_dir=tmp_path / "data", config_path=config_path)
