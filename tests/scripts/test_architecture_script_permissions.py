"""Tests for the tracked-shell-script exec-bit guard (#623).

scripts/architecture/script_permissions.py: every ``*.sh`` in the git index
must be 100755 — a 100644 entry breaks ``./scripts/...`` invocations on
fresh clones (core.fileMode=true) while locally chmod-ed checkouts hide it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.architecture.script_permissions import (
    check_script_exec_bits,
    check_script_permissions,
    parse_index_modes,
)

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_parse_index_modes_splits_on_tab_not_spaces() -> None:
    lines = [
        "100755 abcdef 0\tscripts/ok.sh",
        "100644 abcdef 0\tscripts/with space.sh",
        "100644 abcdef 2\tscripts/conflicted.sh",
        "garbage line without tab",
    ]
    assert parse_index_modes(lines) == {
        "scripts/ok.sh": "100755",
        "scripts/with space.sh": "100644",
        "scripts/conflicted.sh": "100644",
    }


def test_only_shell_scripts_and_only_non_exec_modes_flagged() -> None:
    errors = check_script_exec_bits(
        {
            "scripts/install-deps.sh": "100644",
            "scripts/gate-jobs.sh": "100644",
            "scripts/check.sh": "100755",
            "server/app/main.py": "100644",
        }
    )
    assert len(errors) == 2
    assert all("must be 100755" in error for error in errors)
    assert errors[0].startswith("scripts/gate-jobs.sh")


def test_repo_current_index_is_all_exec() -> None:
    """修复后的仓库基线：所有 tracked .sh 都带执行位（#623 回归钉）。"""
    assert check_script_permissions(REPO_ROOT) == []


def test_check_skips_gracefully_without_git(tmp_path: Path) -> None:
    """无 git 元数据（合成布局/导出目录）时静默跳过，不误报。"""
    assert check_script_permissions(tmp_path) == []


def test_symlink_and_submodule_modes_exempt() -> None:
    """subagent review P2：symlink（120000）与 submodule（160000）条目
    不携带可 chmod 的 mode——标记它们是不可处置的误报，豁免。"""
    errors = check_script_exec_bits(
        {
            "scripts/real.sh": "100644",
            "scripts/link.sh": "120000",
            "vendor/sub.sh": "160000",
            "scripts/fine.sh": "100755",
        }
    )
    assert len(errors) == 1
    assert errors[0].startswith("scripts/real.sh")
