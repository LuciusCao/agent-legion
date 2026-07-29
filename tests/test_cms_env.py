"""Arbitration matrix for CMS_* authoritative names vs BASECMS_* aliases (D3)."""

import pytest

import server.app.cms.env as cms_env
from server.app.cms.env import CMS_ENV_ALIASES, resolve_cms_env


@pytest.fixture(autouse=True)
def _clear_cms_env(monkeypatch):
    for primary, alias in CMS_ENV_ALIASES.items():
        monkeypatch.delenv(primary, raising=False)
        monkeypatch.delenv(alias, raising=False)
    cms_env._warned_aliases.clear()


@pytest.mark.parametrize("primary", sorted(CMS_ENV_ALIASES))
def test_primary_only_wins(primary, monkeypatch):
    monkeypatch.setenv(primary, "value")
    assert resolve_cms_env(primary) == "value"


@pytest.mark.parametrize("primary", sorted(CMS_ENV_ALIASES))
def test_alias_only_applies_with_deprecation_warning(primary, monkeypatch, caplog):
    alias = CMS_ENV_ALIASES[primary]
    monkeypatch.setenv(alias, "value")
    with caplog.at_level("WARNING", logger="server.app.cms.env"):
        assert resolve_cms_env(primary) == "value"
    assert alias in caplog.text
    assert primary in caplog.text


@pytest.mark.parametrize("primary", sorted(CMS_ENV_ALIASES))
def test_both_set_same_value_accepted_silently(primary, monkeypatch, caplog):
    monkeypatch.setenv(primary, "value")
    monkeypatch.setenv(CMS_ENV_ALIASES[primary], "value")
    with caplog.at_level("WARNING", logger="server.app.cms.env"):
        assert resolve_cms_env(primary) == "value"
    assert "deprecated" not in caplog.text


@pytest.mark.parametrize("primary", sorted(CMS_ENV_ALIASES))
def test_both_set_different_values_rejected(primary, monkeypatch):
    alias = CMS_ENV_ALIASES[primary]
    monkeypatch.setenv(primary, "new")
    monkeypatch.setenv(alias, "old")
    with pytest.raises(ValueError, match=alias) as exc_info:
        resolve_cms_env(primary)
    assert primary in str(exc_info.value)


def test_neither_set_returns_none():
    for primary in CMS_ENV_ALIASES:
        assert resolve_cms_env(primary) is None


def test_empty_values_count_as_unset(monkeypatch):
    monkeypatch.setenv("CMS_TOKEN", "")
    monkeypatch.setenv("BASECMS_TOKEN", "")
    assert resolve_cms_env("CMS_TOKEN") is None


def test_alias_warning_fires_once_per_process(monkeypatch, caplog):
    monkeypatch.setenv("BASECMS_TOKEN", "value")
    with caplog.at_level("WARNING", logger="server.app.cms.env"):
        resolve_cms_env("CMS_TOKEN")
        resolve_cms_env("CMS_TOKEN")
    assert caplog.text.count("BASECMS_TOKEN is deprecated") == 1


def test_unknown_primary_key_rejected():
    with pytest.raises(KeyError):
        resolve_cms_env("CMS_UNKNOWN")
