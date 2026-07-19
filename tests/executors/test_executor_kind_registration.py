from __future__ import annotations

from pathlib import Path

import pytest

import server.app.executors  # noqa: F401  # 触发四个内建 kind 注册
from server.app.db.schema import init_db
from server.app.executors.config import (
    LocalExecutorConfig,
    OpenClawExecutorConfig,
    PiExecutorConfig,
    RemoteExecutorConfig,
    load_executor_definitions,
)
from server.app.executors.kinds import (
    RuntimeDependencies,
    UnknownExecutorKindError,
    build_executor,
    registered_kind_names,
)
from server.app.executors.pi import PiExecutor
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.remote_broker import RemoteExecutionBroker
from server.app.skills.manager import SkillManager


def test_builtin_kinds_registered() -> None:
    assert {"local", "pi", "openclaw", "remote"} <= set(registered_kind_names())


def test_unknown_kind_rejected_at_config_load() -> None:
    with pytest.raises(UnknownExecutorKindError, match="unknown kind 'quantum'"):
        load_executor_definitions({"q": {"kind": "quantum", "global_capacity": 1}})


def test_config_parsing_equivalent_to_discriminated_union() -> None:
    raw = {
        "loc": {"kind": "local", "global_capacity": 2, "capabilities": {"c": {"handler": "h"}}},
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
        "rem": {
            "kind": "remote",
            "global_capacity": 5,
            "capabilities": {"c": {"skill": "a/b"}},
        },
    }
    defs = load_executor_definitions(raw)
    assert isinstance(defs["loc"], LocalExecutorConfig)
    assert isinstance(defs["p"], PiExecutorConfig)
    assert isinstance(defs["oc"], OpenClawExecutorConfig)
    assert isinstance(defs["rem"], RemoteExecutorConfig)
    assert defs["rem"].capabilities["c"].tools == ("read", "write", "bash")


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


def test_registry_build_matches_previous_behavior(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    runtime = RuntimeDependencies(
        skill_manager=SkillManager(
            config_path=tmp_path / "skills.yaml",
            lock_path=tmp_path / "skills.lock",
            base_dir=tmp_path / "skills",
        ),
        remote_broker=broker,
    )
    definitions = load_executor_definitions(
        {
            "rem": {
                "kind": "remote",
                "global_capacity": 5,
                "capabilities": {"c": {"skill": "a/b"}},
            },
        }
    )

    registry = ExecutorRegistry.build(definitions, runtime)

    executor = registry.get("rem")
    assert executor is not None
    assert executor.kind == "remote"
    assert registry.global_capacity("rem") == 5
