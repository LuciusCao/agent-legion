"""Contract tests for the Phase 3 execution-decoupling invariants.

EXEC-REMOTE-001: remote execution completion is driven by broker completion
callbacks; the executor path must not block on ``wait_result`` polling.
EXEC-KIND-001: executor kinds resolve through the kinds registry
(``register_kind``/``build_executor``); ``isinstance`` dispatch chains over
executor config types are forbidden in ``registry.py``.
"""

from __future__ import annotations

from pathlib import Path

import server.app.executors  # noqa: F401  # 触发四个内建 kind 注册
from server.app.executors.kinds import registered_kind_names

ROOT = Path(__file__).resolve().parents[1]

REGISTRY_PY = ROOT / "server" / "app" / "executors" / "registry.py"
REMOTE_PY = ROOT / "server" / "app" / "executors" / "remote.py"


def test_registry_has_no_isinstance_kind_dispatch() -> None:
    text = REGISTRY_PY.read_text(encoding="utf-8")
    assert "isinstance(config," not in text, (
        "registry.py must dispatch through the kinds registry, "
        "not isinstance chains over executor config types"
    )


def test_remote_executor_never_waits_for_result() -> None:
    text = REMOTE_PY.read_text(encoding="utf-8")
    assert "wait_result" not in text, (
        "remote.py must stay submit-only; completion is driven by broker "
        "completion callbacks, not wait_result polling"
    )


def test_builtin_kinds_cover_all_four() -> None:
    assert {"local", "pi", "openclaw", "remote"} <= set(registered_kind_names())
