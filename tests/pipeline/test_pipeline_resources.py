import pytest

from server.app.workflows.resources import resolve_cms_resource


@pytest.fixture
def settings_config():
    return {
        "cms": {
            "env": "prod",
            "bank_version": "v5",
            "country_id": "1",
            "subject_id": "2",
            "question_detail_url": "http://cms.example/detail",
            "question_list_url": "http://cms.example/list",
        },
        "resource_providers": {
            "cms.question.detail": {"api_url": "http://cms.example/detail"},
            "cms.question.list_by_knowledge": {"api_url": "http://cms.example/list"},
        },
    }


def test_resolve_with_resource_config_new_format(settings_config):
    workspace = {
        "resource_config": {
            "resources": {
                "question_detail": {
                    "enabled": True,
                    "config": {"subject_id": "5"},
                }
            }
        }
    }
    result = resolve_cms_resource(settings_config, workspace, None, "question_detail")
    assert "subject_id=5" in result["api_url"]


def test_resolve_disabled_provider_returns_empty_url(settings_config):
    workspace = {
        "resource_config": {
            "resources": {
                "question_detail": {"enabled": False, "config": {}},
            }
        }
    }
    result = resolve_cms_resource(settings_config, workspace, None, "question_detail")
    assert result.get("api_url", "") == ""


def test_resolve_fallback_to_legacy_cms_config(settings_config):
    workspace = {
        "cms_config": {
            "subject_id": "9",
            "question_detail_url": "http://legacy.example/detail",
        }
    }
    result = resolve_cms_resource(settings_config, workspace, None, "question_detail")
    assert "subject_id=9" in result["api_url"]


def test_resolve_resource_config_overrides_legacy(settings_config):
    workspace = {
        "cms_config": {"subject_id": "9"},
        "resource_config": {
            "resources": {"question_detail": {"enabled": True, "config": {"subject_id": "7"}}}
        },
    }
    result = resolve_cms_resource(settings_config, workspace, None, "question_detail")
    assert "subject_id=7" in result["api_url"]


def test_resource_param_keys_match_declared_schemas():
    from server.app.workflows.resource_schemas import (
        RESOURCE_PARAM_KEYS,
        resource_param_keys,
    )

    # Regression: order and content feed URL param appending and the settings UI.
    assert RESOURCE_PARAM_KEYS == ("bank_version", "country_id", "subject_id", "page_size")
    # page_size is a list-only param and must not leak onto the detail URL.
    assert resource_param_keys("question_detail") == ("bank_version", "country_id", "subject_id")
    assert resource_param_keys("by_knowledge") == (
        "bank_version",
        "country_id",
        "subject_id",
        "page_size",
    )


def test_validate_resource_bindings_accepts_known_providers():
    from server.app.workflows.resource_schemas import validate_resource_bindings

    validate_resource_bindings(
        {
            "question_detail": {"enabled": True, "config": {"subject_id": "5"}},
            "by_knowledge": {"enabled": True, "config": {"page_size": 100}},
        }
    )


def test_validate_resource_bindings_rejects_bad_values():
    from server.app.config_schema import ConfigSchemaError
    from server.app.workflows.resource_schemas import validate_resource_bindings

    with pytest.raises(ConfigSchemaError, match="unknown resource"):
        validate_resource_bindings({"nope": {"config": {}}})
    with pytest.raises(ConfigSchemaError, match="unknown keys"):
        validate_resource_bindings({"question_detail": {"config": {"evil": "x"}}})
    with pytest.raises(ConfigSchemaError, match="page_size must be of type integer"):
        validate_resource_bindings({"by_knowledge": {"config": {"page_size": "100"}}})
    with pytest.raises(ConfigSchemaError, match="must be <= 500"):
        validate_resource_bindings({"by_knowledge": {"config": {"page_size": 9999}}})
