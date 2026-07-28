import pytest

from server.app.workflows.resource_providers import load_resource_provider_declarations
from server.app.workflows.resources import resolve_cms_resource

_DETAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "api_url": {"type": "string"},
        "bank_version": {"type": "string"},
        "country_id": {"type": "string"},
        "subject_id": {"type": "string"},
        "env": {"type": "string"},
        "token": {"type": "string", "secret": True},
    },
}
_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        **_DETAIL_SCHEMA["properties"],
        "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
    },
}

# Valid declaration shape (spec D11): parseable by
# load_resource_provider_declarations, which is how resolve_cms_resource
# derives declarations from a raw settings config.
_RESOURCE_PROVIDERS_SECTION = {
    "cms.question.detail": {
        "resource_key": "question_detail",
        "url_key": "question_detail_url",
        "api_url": "http://cms.example/detail",
        "config_schema": _DETAIL_SCHEMA,
    },
    "cms.question.list_by_knowledge": {
        "resource_key": "by_knowledge",
        "url_key": "question_list_url",
        "api_url": "http://cms.example/list",
        "config_schema": _LIST_SCHEMA,
    },
}


def _declarations():
    return load_resource_provider_declarations(_RESOURCE_PROVIDERS_SECTION)


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
        "resource_providers": _RESOURCE_PROVIDERS_SECTION,
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


def test_resolve_settings_legacy_url_wins_over_derived():
    settings_config = {
        "cms": {
            "base_url": "http://cms.example",
            "question_detail_url": "http://legacy.example/detail",
        },
        "resource_providers": {
            "cms.question.detail": {
                "resource_key": "question_detail",
                "url_key": "question_detail_url",
                "path": "/question/detail",
                "config_schema": _DETAIL_SCHEMA,
            }
        },
    }
    result = resolve_cms_resource(settings_config, None, None, "question_detail")
    assert result["api_url"].startswith("http://legacy.example/detail")


def test_resolve_ignores_workspace_legacy_cms_config(settings_config):
    workspace = {"cms_config": {"question_detail_url": "http://legacy.example/detail"}}
    result = resolve_cms_resource(settings_config, workspace, None, "question_detail")
    assert "legacy.example" not in result["api_url"]


def test_resource_param_keys_match_declared_schemas():
    from server.app.workflows.resource_schemas import resource_param_keys

    schemas = _declarations().schemas
    detail = resource_param_keys("question_detail", schemas)
    listing = resource_param_keys("by_knowledge", schemas)
    # Regression: order and content feed URL param appending and the settings UI.
    assert tuple(dict.fromkeys(detail + listing)) == (
        "bank_version",
        "country_id",
        "subject_id",
        "page_size",
    )
    # page_size is a list-only param and must not leak onto the detail URL.
    assert detail == ("bank_version", "country_id", "subject_id")
    assert listing == ("bank_version", "country_id", "subject_id", "page_size")


def test_validate_resource_bindings_accepts_known_providers():
    from server.app.workflows.resource_schemas import validate_resource_bindings

    validate_resource_bindings(
        {
            "question_detail": {"enabled": True, "config": {"subject_id": "5"}},
            "by_knowledge": {"enabled": True, "config": {"page_size": 100}},
        },
        _declarations().schemas,
    )


def test_validate_resource_bindings_rejects_bad_values():
    from server.app.config_schema import ConfigSchemaError
    from server.app.workflows.resource_schemas import validate_resource_bindings

    schemas = _declarations().schemas
    with pytest.raises(ConfigSchemaError, match="unknown resource"):
        validate_resource_bindings({"nope": {"config": {}}}, schemas)
    with pytest.raises(ConfigSchemaError, match="unknown keys"):
        validate_resource_bindings({"question_detail": {"config": {"evil": "x"}}}, schemas)
    with pytest.raises(ConfigSchemaError, match="page_size must be of type integer"):
        validate_resource_bindings({"by_knowledge": {"config": {"page_size": "100"}}}, schemas)
    with pytest.raises(ConfigSchemaError, match="must be <= 500"):
        validate_resource_bindings({"by_knowledge": {"config": {"page_size": 9999}}}, schemas)


def test_resolve_node_config_wins_over_binding_and_defaults(settings_config):
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
    result = resolve_cms_resource(
        settings_config,
        workspace,
        None,
        "question_detail",
        node_config={"subject_id": "9", "bank_version": "v6"},
    )
    # node_config (spec D15) overrides both the binding config and the global
    # cms defaults for the non-secret URL params the capability declares.
    assert "subject_id=9" in result["api_url"]
    assert "bank_version=v6" in result["api_url"]


def test_resolve_node_config_empty_values_do_not_override(settings_config):
    result = resolve_cms_resource(
        settings_config, None, None, "question_detail", node_config={"bank_version": ""}
    )
    assert "bank_version=v5" in result["api_url"]


def test_effective_cms_config_applies_context_node_config():
    from server.app.workflows.cms_helpers import _effective_cms_config

    context = {
        "settings_config": {
            "cms": {
                "env": "prod",
                "bank_version": "v5",
                "country_id": "1",
                "subject_id": "2",
                "question_detail_url": "http://cms.example/detail",
            },
            "resource_providers": _RESOURCE_PROVIDERS_SECTION,
        },
        "node_config": {"country_id": "8"},
    }
    resolved = _effective_cms_config({"source_id": "q1"}, context)
    assert "country_id=8" in resolved["api_url"]
    assert "bank_version=v5" in resolved["api_url"]
