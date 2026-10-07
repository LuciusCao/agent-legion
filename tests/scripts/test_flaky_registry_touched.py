"""touched 条目判定覆盖决定豁免对象与期限语义的全部字段（#1034）。

``touched_entry_ids`` 决定 PR 门禁对哪些条目校验 deadline：只比较 deadline 时，
保留过期条目的 id 与 deadline、只改 nodeid / scope / recurring 即可让新 nodeid 的
rerun 被已过期豁免吸收。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from scripts.check_reruns import evaluate, load_registry
from scripts.quality.flaky_registry_lenient import touched_entry_ids

pytestmark = pytest.mark.no_db

_BASE_ENTRY: dict[str, object] = {
    "id": "FLAKY-200",
    "nodeid": "tests/x/test_a.py::test_a",
    "owner": "test-infra",
    "reason": "known flake",
    "observed": "local run 2026-07-01",
    "registered_on": "2026-07-01",
    "deadline": "2026-08-01",
}


def _write(path: Path, entries: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"entries": entries}), encoding="utf-8")
    return path


def _touched(tmp_path: Path, head: dict, base: dict) -> set[str]:
    registry = _write(tmp_path / "registry.yaml", [head])
    return touched_entry_ids(load_registry(registry), _write(tmp_path / "base.yaml", [base]))


def test_identical_entry_is_untouched(tmp_path: Path) -> None:
    assert _touched(tmp_path, dict(_BASE_ENTRY), dict(_BASE_ENTRY)) == set()


def test_prose_only_edits_stay_untouched(tmp_path: Path) -> None:
    head = {**_BASE_ENTRY, "reason": "reworded", "owner": "someone", "observed": "again"}
    assert _touched(tmp_path, head, dict(_BASE_ENTRY)) == set()


@pytest.mark.parametrize(
    "change",
    [
        {"nodeid": "tests/x/test_new.py::test_new"},
        {"nodeid": None, "scope": "ci-infra:docker-hub"},
        {"registered_on": "2026-07-02"},
        {"extended_on": "2026-07-10"},
        {"deadline": "2026-08-02"},
    ],
    ids=["nodeid", "scope", "registered_on", "extended_on", "deadline"],
)
def test_target_or_period_change_is_touched(tmp_path: Path, change: dict) -> None:
    head = {key: value for key, value in {**_BASE_ENTRY, **change}.items() if value is not None}
    assert _touched(tmp_path, head, dict(_BASE_ENTRY)) == {"FLAKY-200"}


def test_recurring_flip_is_touched(tmp_path: Path) -> None:
    base = {k: v for k, v in _BASE_ENTRY.items() if k not in ("deadline", "registered_on")}
    base["recurring"] = True
    assert _touched(tmp_path, dict(_BASE_ENTRY), base) == {"FLAKY-200"}


def test_old_schema_base_ignores_backfilled_anchor_dates(tmp_path: Path) -> None:
    """旧 schema（#941 前、无 registered_on）的 base：补登记日期是 schema 迁移，不算 touched。"""
    base = {k: v for k, v in _BASE_ENTRY.items() if k != "registered_on"}
    assert _touched(tmp_path, dict(_BASE_ENTRY), base) == set()
    head = {**_BASE_ENTRY, "nodeid": "tests/x/test_new.py::test_new"}
    assert _touched(tmp_path, head, base) == {"FLAKY-200"}


def test_renodeid_of_expired_entry_cannot_absorb_new_rerun(tmp_path: Path) -> None:
    """codex 场景：保留过期条目的 id 与 deadline、只改 nodeid——PR 门禁必须判失败。"""
    head = {**_BASE_ENTRY, "nodeid": "tests/x/test_new.py::test_new"}
    registry = _write(tmp_path / "registry.yaml", [head])
    entries = load_registry(registry)
    touched = touched_entry_ids(entries, _write(tmp_path / "base.yaml", [dict(_BASE_ENTRY)]))

    _, violations = evaluate(
        entries,
        {"tests/x/test_new.py::test_new"},
        date(2026, 8, 3),
        enforce_ids=frozenset(touched),
    )

    assert any("FLAKY-200" in v and "expired" in v for v in violations)
