"""Node-level secret fields: vault diversion, masking, and ref handling."""

from __future__ import annotations

import pytest

from server.app.config_schema import ConfigSchemaError
from server.app.services.node_secrets import (
    apply_node_secret_fields,
    mask_node_config_secrets,
    node_secret_name,
    secret_config_fields,
    strip_secret_fields,
)
from server.app.services.vault import VaultMasterKeyMissingError

SCHEMA = {
    "type": "object",
    "properties": {
        "api_url": {"type": "string"},
        "token": {"type": "string", "secret": True},
        "api_key": {"type": "string", "secret": True},
    },
}

WORKSPACE = "ws-1"
NAME = "node:wf:fetch:token"


class _FakeVault:
    """In-memory stand-in for VaultService set/delete."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set(self, workspace_id: str, name: str, plaintext: str) -> None:
        self.store[(workspace_id, name)] = plaintext

    def delete(self, workspace_id: str, name: str) -> None:
        self.store.pop((workspace_id, name), None)


def test_node_secret_name_format() -> None:
    assert node_secret_name("wf", "fetch", "token") == "node:wf:fetch:token"


def test_secret_config_fields_extracts_secret_properties() -> None:
    assert secret_config_fields(SCHEMA) == ("token", "api_key")
    assert secret_config_fields({}) == ()
    assert secret_config_fields({"properties": "not-a-dict"}) == ()
    assert secret_config_fields({"type": "object", "properties": {"a": {"type": "string"}}}) == ()


def test_strip_secret_fields_removes_secret_values() -> None:
    values = {"api_url": "http://x", "token": "plain", "api_key": {"secret_ref": "k"}}
    stripped = strip_secret_fields(SCHEMA, values)
    assert stripped == {"api_url": "http://x"}
    # The input mapping is not mutated.
    assert values["token"] == "plain"


def test_strip_secret_fields_passthrough_without_schema_or_dict() -> None:
    values = {"token": "plain"}
    assert strip_secret_fields({}, values) is values
    assert strip_secret_fields(SCHEMA, "not-a-dict") == "not-a-dict"


def test_apply_node_secret_fields_diverts_plaintext_to_vault() -> None:
    vault = _FakeVault()
    result = apply_node_secret_fields(
        vault, WORKSPACE, "wf", "fetch", SCHEMA, {"token": "  s3cr3t "}, {}
    )
    assert result["token"] == {"secret_ref": NAME}
    assert vault.store[(WORKSPACE, NAME)] == "  s3cr3t "


def test_apply_node_secret_fields_empty_string_clears_vault_and_field() -> None:
    vault = _FakeVault()
    vault.store[(WORKSPACE, NAME)] = "old"
    current = {"token": {"secret_ref": NAME}}
    result = apply_node_secret_fields(
        vault, WORKSPACE, "wf", "fetch", SCHEMA, {"token": ""}, current
    )
    assert "token" not in result
    assert (WORKSPACE, NAME) not in vault.store


def test_apply_node_secret_fields_keeps_existing_secret_ref() -> None:
    vault = _FakeVault()
    ref = {"secret_ref": "node:other:fetch:token"}
    result = apply_node_secret_fields(vault, WORKSPACE, "wf", "fetch", SCHEMA, {"token": ref}, {})
    assert result["token"] == ref
    assert vault.store == {}


def test_apply_node_secret_fields_masked_echo_inherits_stored_value() -> None:
    vault = _FakeVault()
    current = {"token": {"secret_ref": NAME}}
    result = apply_node_secret_fields(
        vault, WORKSPACE, "wf", "fetch", SCHEMA, {"token": {"secret_set": True}}, current
    )
    assert result["token"] == {"secret_ref": NAME}


def test_apply_node_secret_fields_masked_echo_without_stored_drops_field() -> None:
    vault = _FakeVault()
    result = apply_node_secret_fields(
        vault, WORKSPACE, "wf", "fetch", SCHEMA, {"token": {"secret_set": False}}, {}
    )
    assert "token" not in result


def test_apply_node_secret_fields_absent_field_inherits_stored_value() -> None:
    vault = _FakeVault()
    current = {"token": {"secret_ref": NAME}}
    result = apply_node_secret_fields(
        vault, WORKSPACE, "wf", "fetch", SCHEMA, {"api_url": "http://new"}, current
    )
    assert result == {"api_url": "http://new", "token": {"secret_ref": NAME}}


def test_apply_node_secret_fields_rejects_non_string_value() -> None:
    vault = _FakeVault()
    with pytest.raises(ConfigSchemaError, match="nodeConfig.fetch.token must be a string"):
        apply_node_secret_fields(vault, WORKSPACE, "wf", "fetch", SCHEMA, {"token": 123}, {})


def test_inherited_legacy_plaintext_is_vaulted_on_absent_field() -> None:
    vault = _FakeVault()
    current = {"token": "legacy-plaintext"}
    result = apply_node_secret_fields(
        vault, WORKSPACE, "wf", "fetch", SCHEMA, {"api_url": "http://new"}, current
    )
    assert result == {"api_url": "http://new", "token": {"secret_ref": NAME}}
    assert vault.store[(WORKSPACE, NAME)] == "legacy-plaintext"


def test_inherited_legacy_plaintext_is_vaulted_on_masked_echo() -> None:
    vault = _FakeVault()
    current = {"token": "legacy-plaintext"}
    result = apply_node_secret_fields(
        vault, WORKSPACE, "wf", "fetch", SCHEMA, {"token": {"secret_set": True}}, current
    )
    assert result["token"] == {"secret_ref": NAME}
    assert vault.store[(WORKSPACE, NAME)] == "legacy-plaintext"


def test_inherited_legacy_blank_plaintext_is_cleared() -> None:
    vault = _FakeVault()
    vault.store[(WORKSPACE, NAME)] = "old"
    current = {"token": "   "}
    result = apply_node_secret_fields(
        vault, WORKSPACE, "wf", "fetch", SCHEMA, {"token": {"secret_set": True}}, current
    )
    assert "token" not in result
    assert (WORKSPACE, NAME) not in vault.store


def test_inherit_vaulting_without_master_key_fails_closed() -> None:
    class _NoKeyVault(_FakeVault):
        def set(self, workspace_id: str, name: str, plaintext: str) -> None:
            raise VaultMasterKeyMissingError("vault master key missing")

    vault = _NoKeyVault()
    current = {"token": "legacy-plaintext"}
    with pytest.raises(VaultMasterKeyMissingError):
        apply_node_secret_fields(
            vault, WORKSPACE, "wf", "fetch", SCHEMA, {"api_url": "http://new"}, current
        )


def test_mask_node_config_secrets_marks_set_and_unset() -> None:
    overrides = {
        "fetch": {"api_url": "http://x", "token": {"secret_ref": NAME}},
        "other": {"page_size": 5},
    }
    schemas = {"fetch": SCHEMA}
    masked = mask_node_config_secrets(overrides, schemas)
    assert masked["fetch"] == {
        "api_url": "http://x",
        "token": {"secret_set": True},
        "api_key": {"secret_set": False},
    }
    # Nodes without a schema pass through untouched.
    assert masked["other"] == {"page_size": 5}


def test_mask_node_config_secrets_marks_unset_for_missing_or_blank() -> None:
    masked = mask_node_config_secrets({"fetch": {"api_url": "http://x"}}, {"fetch": SCHEMA})
    assert masked["fetch"]["token"] == {"secret_set": False}
    masked_blank = mask_node_config_secrets({"fetch": {"token": "  "}}, {"fetch": SCHEMA})
    assert masked_blank["fetch"]["token"] == {"secret_set": False}


def test_mask_node_config_secrets_tolerates_non_dict_values() -> None:
    assert mask_node_config_secrets({"fetch": "raw"}, {"fetch": SCHEMA}) == {"fetch": "raw"}
