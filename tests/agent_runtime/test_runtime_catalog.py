"""Runtime catalog 一致性（issue #75 阶段 1，纯静态）。

AGENT_RUNTIMES 是 Host 侧 runtime 全集的单一事实来源；本文件钉住它与各
消费方全等：AgentDefinition.runtime 三处 Literal（OpenAPI/前端类型来源，
保持字面写法）、Worker 注册白名单（agent_control/registry.py）、Worker 侧
runtime 目录（worker/runtime/catalog.py）与 workerctl choices
（worker/cli_args.py）。
"""

from __future__ import annotations

import argparse
from typing import get_args

import pytest

from server.app.agent_catalog.definition import AgentDefinition
from server.app.agent_control import registry as worker_registry
from server.app.agent_runtime.catalog import AGENT_RUNTIMES, get_adapter
from server.app.routes.agent_catalog_contracts import AgentDefinitionResponse
from server.app.routes.agent_definition_contracts import AgentDefinitionPayload
from worker.cli_args import build_parser
from worker.runtime.catalog import SUPPORTED_RUNTIMES

pytestmark = pytest.mark.no_db


def _literal_values(model: type, field: str) -> set[str]:
    return {str(value) for value in get_args(model.model_fields[field].annotation)}


def _disable_runtime_choices() -> set[str]:
    # argparse 没有查询 choices 的公开 API，遍历子命令 action 拿
    # `configure --disable-runtime` 的 choices。
    for action in build_parser()._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for sub_action in action.choices["configure"]._actions:
            if "--disable-runtime" in sub_action.option_strings:
                return {str(choice) for choice in sub_action.choices}
    raise AssertionError("workerctl configure --disable-runtime not found")


def test_catalog_matches_runtime_literals() -> None:
    assert set(AGENT_RUNTIMES) == {"pi", "openclaw", "velites"}
    assert _literal_values(AgentDefinition, "runtime") == set(AGENT_RUNTIMES)
    assert _literal_values(AgentDefinitionPayload, "runtime") == set(AGENT_RUNTIMES)
    assert _literal_values(AgentDefinitionResponse, "runtime") == set(AGENT_RUNTIMES)


def test_catalog_matches_worker_registration_whitelist() -> None:
    # registry 直接读 catalog；钉住这条接线，防止回退到硬编码集合。
    assert worker_registry.AGENT_RUNTIMES is AGENT_RUNTIMES


def test_catalog_matches_worker_side_runtime_sets() -> None:
    assert set(SUPPORTED_RUNTIMES) == set(AGENT_RUNTIMES)
    assert _disable_runtime_choices() == set(AGENT_RUNTIMES)


def test_get_adapter_unknown_runtime_lists_full_catalog() -> None:
    with pytest.raises(ValueError, match=r"unknown agent runtime 'rust'.*pi, openclaw, velites"):
        get_adapter("rust")


def test_openclaw_registered_and_implemented() -> None:
    # 阶段 3 起 openclaw 已接入：adapter implemented，build_command 产出 argv。
    adapter = get_adapter("openclaw")
    assert adapter.implemented is True
    cmd = adapter.build_command(
        {"execution": {"model": "m"}},
        skill_dir="/s",
        session_dir="/sd",
        session_name="sess",
        prompt_file="/p.md",
        prompt_instruction="x",
    )
    assert cmd[:2] == ["openclaw", "agent"]
