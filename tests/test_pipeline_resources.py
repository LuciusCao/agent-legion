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
