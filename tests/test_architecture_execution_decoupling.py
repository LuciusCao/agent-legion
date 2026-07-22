"""Contract tests for execution-decoupling invariants.

EXEC-KIND-001: local executor kinds resolve through the kinds registry
(``register_kind``/``build_executor``); ``isinstance`` dispatch chains over
executor config types are forbidden in ``registry.py``.
"""

from __future__ import annotations

from pathlib import Path

import server.app.executors  # noqa: F401  # 触发内建 kind 注册
from server.app.executors.kinds import registered_kind_names

ROOT = Path(__file__).resolve().parents[1]

REGISTRY_PY = ROOT / "server" / "app" / "executors" / "registry.py"


def test_registry_has_no_isinstance_kind_dispatch() -> None:
    text = REGISTRY_PY.read_text(encoding="utf-8")
    assert "isinstance(config," not in text, (
        "registry.py must dispatch through the kinds registry, "
        "not isinstance chains over executor config types"
    )


def test_builtin_kinds_exclude_removed_remote_executor() -> None:
    kinds = set(registered_kind_names())
    assert {"local", "pi", "openclaw"} <= kinds
    assert "remote" not in kinds
