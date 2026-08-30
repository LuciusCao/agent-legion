"""Contract tests for the #298 broad-except audit guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.architecture.broad_except_audit import (
    check_broad_except_audit,
    find_unaudited_broad_excepts,
)

pytestmark = pytest.mark.no_db


def test_broad_except_with_inline_audit_passes() -> None:
    source = (
        "try:\n    boom()\nexcept Exception:\n    # #204 broad-except audit: deliberate\n    pass\n"
    )
    assert find_unaudited_broad_excepts(source) == []


def test_broad_except_with_method_header_audit_passes() -> None:
    # Block audit at the method head governs every catch in the method
    # (the executors/sweeper.py pattern).
    source = (
        "def sweep(self):\n"
        "    # #204 broad-except audit: the loop must survive anything.\n"
        "    try:\n"
        "        self.a()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        self.b()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    assert find_unaudited_broad_excepts(source) == []


def test_broad_except_without_audit_fails() -> None:
    source = "try:\n    boom()\nexcept Exception:\n    pass\n"
    assert find_unaudited_broad_excepts(source) == [3]


def test_bare_except_is_also_broad() -> None:
    source = "try:\n    boom()\nexcept:\n    pass\n"
    assert find_unaudited_broad_excepts(source) == [3]


def test_narrow_except_needs_no_audit() -> None:
    source = "try:\n    boom()\nexcept OSError:\n    pass\n"
    assert find_unaudited_broad_excepts(source) == []


def test_current_repo_is_fully_audited() -> None:
    # #298 end state: after the tail cleanup, every broad catch in the scan
    # roots carries its audit note — this is the regression pin.
    root = Path(__file__).resolve().parents[2]
    assert check_broad_except_audit(root) == []


def test_exception_in_tuple_is_broad() -> None:
    # codex review on #308: `except (Exception, OSError)` catches every
    # Exception and must be audited like the plain form.
    source = "try:\n    boom()\nexcept (Exception, OSError):\n    pass\n"
    assert find_unaudited_broad_excepts(source) == [3]


def test_bare_204_comment_does_not_bless() -> None:
    # codex review on #308: a bare "#204" elsewhere in the function must
    # not satisfy the guard — only the full marker counts.
    source = (
        "def f():\n"
        "    # #204: unrelated historical note\n"
        "    try:\n"
        "        boom()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    assert find_unaudited_broad_excepts(source) == [5]


def test_async_def_header_audit_passes() -> None:
    # subagent review on #308: method-head block audits must govern catches
    # inside async methods too (the sweeper pattern is not sync-only).
    source = (
        "async def loop():\n"
        "    # #204 broad-except audit: the loop must survive anything.\n"
        "    try:\n"
        "        await self.a()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    assert find_unaudited_broad_excepts(source) == []


def test_string_literal_cannot_forge_audit() -> None:
    # subagent review on #308: the marker inside a log("...") string must
    # not count — only real comments bless a broad catch.
    source = 'try:\n    boom()\nexcept Exception:\n    log("#204 broad-except audit: forged")\n'
    assert find_unaudited_broad_excepts(source) == [3]


def test_base_exception_is_broad() -> None:
    # subagent review on #308: BaseException is wider than Exception and
    # needs the same audit.
    source = "try:\n    boom()\nexcept BaseException:\n    pass\n"
    assert find_unaudited_broad_excepts(source) == [3]


def test_neighbor_arm_audit_does_not_leak() -> None:
    # codex review round 3 on #308: two broad arms close together where
    # only the SECOND carries the audit — the first must still fail. The
    # near window is bounded by the handler's own end line.
    source = (
        "try:\n"
        "    a()\n"
        "except Exception:\n"  # arm 1: no audit
        "    pass\n"
        "try:\n"
        "    b()\n"
        "except Exception:\n"
        "    # #204 broad-except audit: deliberate\n"
        "    pass\n"
    )
    assert find_unaudited_broad_excepts(source) == [3]


def test_long_method_header_audit_still_covers() -> None:
    # codex review round 3 on #308: a catch >120 lines from its def keeps
    # the method-head block audit — ownership is AST-based, not a line
    # window.
    filler = "\n".join(f"        x{i} = {i}" for i in range(130))
    source = (
        "def sweep():\n"
        "    # #204 broad-except audit: the loop survives anything.\n"
        "    try:\n" + filler + "\n"
        "    except Exception:\n"
        "        pass\n"
    )
    assert find_unaudited_broad_excepts(source) == []
