from __future__ import annotations

import pytest

from server.app.executors import registration as _registration  # noqa: F401  # 触发内建 kind 注册
from server.app.executors.config import (
    CodeExecutorConfig,
    LocalExecutorConfig,
    OpenClawExecutorConfig,
    PiExecutorConfig,
)
from server.app.executors.definitions import load_executor_definitions
from server.app.executors.kinds import (
    RuntimeDependencies,
    UnknownExecutorKindError,
    build_executor,
    registered_kind_names,
)
from server.app.executors.pi import PiExecutor


def test_builtin_kinds_registered() -> None:
    assert {"local", "code", "pi", "openclaw"} <= set(registered_kind_names())
    assert "remote" not in registered_kind_names()


def test_unknown_kind_rejected_at_config_load() -> None:
    with pytest.raises(UnknownExecutorKindError, match="unknown kind 'quantum'"):
        load_executor_definitions({"q": {"kind": "quantum", "global_capacity": 1}})


def test_config_parsing_equivalent_to_discriminated_union() -> None:
    raw = {
        "loc": {"kind": "local", "global_capacity": 2, "capabilities": {"c": {"handler": "h"}}},
        "code": {
            "kind": "code",
            "global_capacity": 2,
            "capabilities": {"c": {"path": "workflow_nodes/x.py"}},
        },
        "p": {
            "kind": "pi",
            "global_capacity": 3,
            "capabilities": {"c": {"skill": "a/b", "tools": ["read"]}},
        },
        "oc": {
            "kind": "openclaw",
            "agent_id": "agent",
            "global_capacity": 1,
            "capabilities": {"c": {"skill": "a/b"}},
        },
    }
    defs = load_executor_definitions(raw)
    assert isinstance(defs["loc"], LocalExecutorConfig)
    assert isinstance(defs["code"], CodeExecutorConfig)
    assert isinstance(defs["p"], PiExecutorConfig)
    assert isinstance(defs["oc"], OpenClawExecutorConfig)


def test_build_executor_dispatch_equivalence() -> None:
    defs = load_executor_definitions(
        {
            "p": {"kind": "pi", "global_capacity": 1, "capabilities": {"c": {"skill": "a/b"}}},
        }
    )
    executor = build_executor("p", defs["p"], RuntimeDependencies())
    assert isinstance(executor, PiExecutor)
    assert executor.id == "p"
    assert executor.supports("c")
