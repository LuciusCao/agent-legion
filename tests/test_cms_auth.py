"""Priority matrix for the single CMS credential-availability helper."""

import pytest

import server.app.cms.env as cms_env
from server.app.cms.auth import cms_token_available

_CMS_ENV_KEYS = (
    "CMS_TOKEN",
    "CMS_APP_ID",
    "CMS_NONCE",
    "CMS_SECRET",
    "CMS_TOKEN_URL",
    "BASECMS_TOKEN",
    "BASECMS_APP_ID",
    "BASECMS_NONCE",
    "BASECMS_SECRET",
    "BASECMS_TOKEN_URL",
)


@pytest.fixture(autouse=True)
def _clear_cms_env(monkeypatch):
    for key in _CMS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    cms_env._warned_aliases.clear()


def _token_gen_config() -> dict[str, dict[str, str]]:
    return {
        "token_gen": {
            "app_id": "app",
            "nonce": "nonce",
            "secret": "secret",
            "url": "http://token.example/generate",
        }
    }


def test_env_cms_token_suffices_without_any_config(monkeypatch):
    monkeypatch.setenv("CMS_TOKEN", "env-token")
    assert cms_token_available(None) is True
    assert cms_token_available({}) is True


def test_env_cms_token_beats_config_and_token_gen(monkeypatch):
    monkeypatch.setenv("CMS_TOKEN", "env-token")
    assert cms_token_available({"token": "config-token", **_token_gen_config()}) is True


def test_env_basecms_token_alias_still_suffices(monkeypatch):
    monkeypatch.setenv("BASECMS_TOKEN", "env-token")
    assert cms_token_available({}) is True


def test_config_token_suffices_without_env():
    # Binding/env-injected in-memory token (vault secret_refs are resolved by
    # the caller before this check).
    assert cms_token_available({"token": "config-token"}) is True


def test_config_token_beats_partial_token_gen():
    partial = {"token_gen": {"app_id": "app", "nonce": "nonce"}}
    assert cms_token_available({"token": "config-token", **partial}) is True


def test_token_gen_config_four_keys_suffice():
    assert cms_token_available(_token_gen_config()) is True


def test_token_gen_env_four_keys_suffice(monkeypatch):
    monkeypatch.setenv("CMS_APP_ID", "app")
    monkeypatch.setenv("CMS_NONCE", "nonce")
    monkeypatch.setenv("CMS_SECRET", "secret")
    monkeypatch.setenv("CMS_TOKEN_URL", "http://token.example/generate")
    assert cms_token_available({}) is True


def test_token_gen_basecms_alias_four_keys_suffice(monkeypatch):
    monkeypatch.setenv("BASECMS_APP_ID", "app")
    monkeypatch.setenv("BASECMS_NONCE", "nonce")
    monkeypatch.setenv("BASECMS_SECRET", "secret")
    monkeypatch.setenv("BASECMS_TOKEN_URL", "http://token.example/generate")
    assert cms_token_available({}) is True


def test_token_gen_conflicting_dual_assignment_rejected(monkeypatch):
    monkeypatch.setenv("CMS_SECRET", "new-secret")
    monkeypatch.setenv("BASECMS_SECRET", "old-secret")
    with pytest.raises(ValueError, match="CMS_SECRET"):
        cms_token_available(_token_gen_config())


def test_token_gen_partial_keys_are_not_enough():
    assert (
        cms_token_available({"token_gen": {"app_id": "app", "nonce": "n", "secret": "s"}}) is False
    )


def test_no_credentials_is_unavailable():
    assert cms_token_available({}) is False
    assert cms_token_available(None) is False
    assert cms_token_available("not-a-mapping") is False
