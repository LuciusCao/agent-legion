"""Unit tests for workspace_libs/node_sdk.py (the node framework API)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_libs.node_sdk import (
    AUTH_FAILURE_MARKER_PATH,
    NodeContext,
    entrypoint,
    parse_json_object,
)

pytestmark = pytest.mark.no_db


def _ctx(
    tmp_path: Path,
    runtime: dict | None = None,
    job: dict | None = None,
) -> NodeContext:
    return NodeContext(job or {"id": "job-1"}, tmp_path, runtime)


class _Token:
    def __init__(self, cancelled: bool) -> None:
        self._cancelled = cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise _CancelledError


class _CancelledError(Exception):
    pass


# ---------------------------------------------------------------------------
# service_config


def test_service_config_merges_settings_section_and_node_config(tmp_path: Path) -> None:
    runtime = {
        "settings_config": {
            "asr": {
                "provider": "auto",
                "timeout_seconds": 900,
                "whisper": {"binary": "/env/whisper-cli"},
            }
        },
        "node_config": {"provider": "whisper", "timeout_seconds": 120},
    }

    merged = _ctx(tmp_path, runtime).service_config(section="asr")

    # node_config 业务参数覆盖 settings 级（env 注入）值。
    assert merged["provider"] == "whisper"
    assert merged["timeout_seconds"] == 120
    # env 注入的机器路径保留。
    assert merged["whisper"] == {"binary": "/env/whisper-cli"}


def test_service_config_ignores_empty_node_config_values(tmp_path: Path) -> None:
    runtime = {
        "settings_config": {"asr": {"provider": "sensevoice"}},
        "node_config": {"provider": "", "timeout_seconds": None},
    }

    merged = _ctx(tmp_path, runtime).service_config(section="asr")

    assert merged["provider"] == "sensevoice"
    assert "timeout_seconds" not in merged


def test_service_config_without_runtime_is_empty(tmp_path: Path) -> None:
    assert _ctx(tmp_path, None).service_config(section="asr") == {}
    assert _ctx(tmp_path, {}).service_config() == {}


def test_service_config_prefers_injected_connection_over_legacy_keys(tmp_path: Path) -> None:
    legacy_keys = ("token", "env", "base_url", "api_url")
    runtime = {
        "node_config": {
            "connection": "cms-internal",
            "connection_config": {"token": "conn-token", "api_url": "https://conn.example.com"},
            "token": "legacy-token",
            "api_url": "https://legacy.example.com",
            "page_size": 50,
        }
    }

    merged = _ctx(tmp_path, runtime).service_config(legacy_keys=legacy_keys)

    # 注入的连接配置优先；legacy 键让步；业务覆盖保留；选择器键不进入结果。
    assert merged == {
        "token": "conn-token",
        "api_url": "https://conn.example.com",
        "page_size": 50,
    }


def test_service_config_honors_legacy_keys_without_connection(tmp_path: Path) -> None:
    runtime = {"node_config": {"token": "legacy-token", "api_url": "https://legacy.example.com"}}

    merged = _ctx(tmp_path, runtime).service_config(legacy_keys=("token", "api_url"))

    assert merged == {"token": "legacy-token", "api_url": "https://legacy.example.com"}


def test_service_config_filters_empty_injected_values(tmp_path: Path) -> None:
    runtime = {
        "node_config": {
            "connection_config": {"token": "conn-token", "api_url": ""},
            "api_url": "https://node.example.com",
        }
    }

    merged = _ctx(tmp_path, runtime).service_config()

    assert merged == {"token": "conn-token", "api_url": "https://node.example.com"}


# ---------------------------------------------------------------------------
# artifacts


def test_write_json_format_and_auto_mkdir(tmp_path: Path) -> None:
    job_dir = tmp_path / "nested" / "job"
    ctx = _ctx(job_dir, {})

    ctx.artifacts.write_json("out.json", {"题干": "1+1=?"})

    text = (job_dir / "out.json").read_text(encoding="utf-8")
    assert text == json.dumps({"题干": "1+1=?"}, ensure_ascii=False, indent=2)


def test_read_json_roundtrip_and_read_text(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {})
    ctx.artifacts.write_json("data.json", [1, 2])
    ctx.artifacts.write_text("note.md", "你好")

    assert ctx.artifacts.read_json("data.json") == [1, 2]
    assert ctx.artifacts.read_text("note.md") == "你好"


def test_read_json_object_requires_existing_dict(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {})
    with pytest.raises(ValueError, match="Missing input: missing.json"):
        ctx.artifacts.read_json_object("missing.json")

    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid content in list.json"):
        ctx.artifacts.read_json_object("list.json")


def test_write_checkpoints_before_producing_output(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"cancellation": _Token(cancelled=True)})

    with pytest.raises(_CancelledError):
        ctx.artifacts.write_json("out.json", {})

    assert not (tmp_path / "out.json").exists()


def test_checkpoint_without_token_is_noop(tmp_path: Path) -> None:
    _ctx(tmp_path, {}).checkpoint()
    _ctx(tmp_path, None).checkpoint()
    _ctx(tmp_path, {"cancellation": object()}).checkpoint()


# ---------------------------------------------------------------------------
# prefetched inputs


def test_batch_returns_prefetched_row_copy(tmp_path: Path) -> None:
    batch = {"id": "b-1", "source_payload_json": "{}"}
    ctx = _ctx(tmp_path, {"job_batch": batch})

    assert ctx.batch == batch
    assert ctx.batch is not batch


def test_batch_defaults_to_none(tmp_path: Path) -> None:
    assert _ctx(tmp_path, {}).batch is None
    assert _ctx(tmp_path, {"job_batch": "not-a-dict"}).batch is None


def test_skill_versions_stringifies_mapping(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"skill_versions": {"n1": "v1"}})
    assert ctx.skill_versions == {"n1": "v1"}
    assert _ctx(tmp_path, {}).skill_versions == {}
    assert _ctx(tmp_path, {"skill_versions": ["bad"]}).skill_versions == {}


def test_workflow_manifest_reads_job_identity(tmp_path: Path) -> None:
    job = {
        "workflow_key": "wk",
        "workflow_version": 3,
        "workflow_revision_id": "rev-1",
        "workflow_definition_hash": "hash",
    }
    assert _ctx(tmp_path, {}, job).workflow_manifest("default") == {
        "key": "wk",
        "version": 3,
        "revision_id": "rev-1",
        "definition_hash": "hash",
    }
    assert _ctx(tmp_path, {}, {}).workflow_manifest("default")["key"] == "default"


def test_report_auth_failure_writes_marker_with_connection_key(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"node_config": {"connection": "cms-internal"}})

    ctx.report_auth_failure()

    marker = tmp_path / AUTH_FAILURE_MARKER_PATH
    assert marker.read_text(encoding="utf-8") == "cms-internal"


def test_report_auth_failure_without_connection_writes_empty_marker(tmp_path: Path) -> None:
    _ctx(tmp_path, {}).report_auth_failure()

    assert (tmp_path / AUTH_FAILURE_MARKER_PATH).read_text(encoding="utf-8") == ""


def test_logger_named_after_node_key(tmp_path: Path) -> None:
    assert _ctx(tmp_path, {"node_key": "fetch_items"}).logger.name == ("workflow_node.fetch_items")


def test_parse_json_object_tolerates_garbage() -> None:
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object("[1]") == {}
    assert parse_json_object("not json") == {}
    assert parse_json_object(None) == {}
    assert parse_json_object("") == {}


# ---------------------------------------------------------------------------
# batch_payload / root_dir


def test_batch_payload_parses_prefetched_source_payload(tmp_path: Path) -> None:
    batch = {"id": "b-1", "source_payload_json": '{"intake_mode": {"input_field": "ids"}}'}
    ctx = _ctx(tmp_path, {"job_batch": batch})

    assert ctx.batch_payload == {"intake_mode": {"input_field": "ids"}}


def test_batch_payload_defaults_to_empty(tmp_path: Path) -> None:
    assert _ctx(tmp_path, {}).batch_payload == {}
    assert _ctx(tmp_path, {"job_batch": {"source_payload_json": "not json"}}).batch_payload == {}
    assert _ctx(tmp_path, {"job_batch": {"id": "b-1"}}).batch_payload == {}


def test_root_dir_reads_runtime_key(tmp_path: Path) -> None:
    assert _ctx(tmp_path, {"root_dir": "/repo/root"}).root_dir == Path("/repo/root")
    assert _ctx(tmp_path, {}).root_dir is None
    assert _ctx(tmp_path, {"root_dir": ""}).root_dir is None


# ---------------------------------------------------------------------------
# entrypoint


def test_entrypoint_adapts_business_function(tmp_path: Path) -> None:
    seen: dict = {}

    @entrypoint
    def run(ctx: NodeContext) -> None:
        seen["job"] = ctx.job
        seen["node_key"] = ctx.config.get("k")
        ctx.artifacts.write_json("out.json", {"ok": True})

    assert run.__name__ == "run"
    run({"id": "j-1"}, tmp_path, {"node_config": {"k": "v"}})

    assert seen == {"job": {"id": "j-1"}, "node_key": "v"}
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8")) == {"ok": True}


def test_entrypoint_runtime_defaults_to_none(tmp_path: Path) -> None:
    @entrypoint
    def run(ctx: NodeContext) -> None:
        assert ctx.config == {}
        assert ctx.batch is None

    run({"id": "j-2"}, tmp_path)
