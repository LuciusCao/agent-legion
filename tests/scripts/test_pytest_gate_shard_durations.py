"""Contract tests for duration-weighted PostgreSQL gate sharding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.pytest_gate_shard import pytest_configure
from scripts.pytest_gate_shard_durations import (
    DurationGateShardFilter,
    load_duration_manifest,
    parse_reserved_seconds,
    weighted_shard_nodeids,
)

pytestmark = pytest.mark.no_db


def _nodeids(files: dict[str, int]) -> list[str]:
    return [
        f"{filename}::test_{index}" for filename, count in files.items() for index in range(count)
    ]


def _config() -> SimpleNamespace:
    deselected: list = []
    hook = SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items))
    return SimpleNamespace(hook=hook, _deselected=deselected)


def test_weighted_shards_balance_duration_instead_of_test_count() -> None:
    files = {
        "tests/test_a.py": 1,
        "tests/test_b.py": 1,
        "tests/test_c.py": 1,
        "tests/test_d.py": 1,
    }
    timings = {
        "tests/test_a.py": (10.0, 1),
        "tests/test_b.py": (8.0, 1),
        "tests/test_c.py": (6.0, 1),
        "tests/test_d.py": (4.0, 1),
    }

    shards, loads = weighted_shard_nodeids(_nodeids(files), 2, timings)

    assert loads == (14.0, 14.0)
    assert shards[0] == {"tests/test_a.py::test_0", "tests/test_d.py::test_0"}
    assert shards[1] == {"tests/test_b.py::test_0", "tests/test_c.py::test_0"}


def test_reserved_capacity_accounts_for_extra_work_on_one_shard() -> None:
    files = {f"tests/test_{name}.py": 1 for name in "abcd"}
    timings = {
        "tests/test_a.py": (8.0, 1),
        "tests/test_b.py": (6.0, 1),
        "tests/test_c.py": (4.0, 1),
        "tests/test_d.py": (2.0, 1),
    }

    _, loads = weighted_shard_nodeids(_nodeids(files), 2, timings, (0.0, 5.0))

    assert loads == (12.0, 13.0)


def test_unknown_files_use_average_and_known_files_scale_with_collection() -> None:
    nodeids = _nodeids({"tests/test_known.py": 3, "tests/test_new.py": 2})
    timings = {"tests/test_known.py": (10.0, 2)}

    shards, loads = weighted_shard_nodeids(nodeids, 2, timings)

    assert loads == (15.0, 10.0)
    assert set().union(*shards) == set(nodeids)


def test_weighted_plan_is_independent_of_collection_order() -> None:
    nodeids = _nodeids({f"tests/test_{index}.py": index + 1 for index in range(6)})
    timings = {f"tests/test_{index}.py": (float(index + 1), index + 1) for index in range(6)}

    forward, forward_loads = weighted_shard_nodeids(nodeids, 3, timings)
    reverse, reverse_loads = weighted_shard_nodeids(list(reversed(nodeids)), 3, timings)

    assert forward == reverse
    assert forward_loads == reverse_loads


def test_weighted_filters_partition_every_item_and_report_deselected() -> None:
    nodeids = _nodeids({f"tests/test_{index}.py": 2 for index in range(8)})
    timings = {f"tests/test_{index}.py": (float(index + 1), 2) for index in range(8)}
    kept: list[set[str]] = []
    load_reports: list[tuple[float, ...]] = []

    for index in range(1, 5):
        items = [SimpleNamespace(nodeid=nodeid) for nodeid in nodeids]
        config = _config()
        shard = DurationGateShardFilter(index, 4, timings, ())
        shard.pytest_collection_modifyitems(None, config, items)
        kept.append({item.nodeid for item in items})
        load_reports.append(shard.estimated_loads)
        assert {item.nodeid for item in config._deselected} == set(nodeids) - kept[-1]

    assert set().union(*kept) == set(nodeids)
    assert sum((len(shard) for shard in kept), 0) == len(nodeids)
    assert all(loads == load_reports[0] for loads in load_reports)


def test_duration_manifest_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "durations.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "source_run": "123",
                "files": {"tests/test_example.py": {"seconds": 2.5, "tests": 2}},
            }
        ),
        encoding="utf-8",
    )

    assert load_duration_manifest(path) == {"tests/test_example.py": (2.5, 2)}


@pytest.mark.parametrize(
    "entry",
    [
        {"seconds": -1, "tests": 1},
        {"seconds": float("nan"), "tests": 1},
        {"seconds": 1, "tests": 0},
        {"seconds": True, "tests": 1},
        {"seconds": 1, "tests": True},
    ],
)
def test_duration_manifest_rejects_invalid_values(tmp_path: Path, entry: dict) -> None:
    path = tmp_path / "durations.json"
    path.write_text(
        json.dumps({"version": 1, "files": {"tests/test_example.py": entry}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid timing values"):
        load_duration_manifest(path)


def test_duration_manifest_accepts_zero_duration_file(tmp_path: Path) -> None:
    path = tmp_path / "durations.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "tests/test_zero.py": {"seconds": 0, "tests": 1},
                    "tests/test_example.py": {"seconds": 2.5, "tests": 2},
                },
            }
        ),
        encoding="utf-8",
    )

    assert load_duration_manifest(path)["tests/test_zero.py"] == (0.0, 1)


def test_reserved_seconds_require_one_value_per_shard() -> None:
    assert parse_reserved_seconds("0,65,0,0", 4) == (0.0, 65.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="exactly 4"):
        parse_reserved_seconds("0,65", 4)
    with pytest.raises(ValueError, match="non-negative"):
        parse_reserved_seconds("0,-1,0,0", 4)


def test_configure_rejects_reservation_without_duration_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_SHARD", "1/2")
    monkeypatch.setenv("GATE_SHARD_RESERVED_SECONDS", "0,65")
    monkeypatch.delenv("GATE_SHARD_DURATIONS", raising=False)

    with pytest.raises(pytest.UsageError, match="requires GATE_SHARD_DURATIONS"):
        pytest_configure(SimpleNamespace())


def test_configure_rejects_unreadable_duration_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GATE_SHARD", "1/2")
    monkeypatch.setenv("GATE_SHARD_DURATIONS", str(tmp_path / "missing.json"))
    monkeypatch.delenv("GATE_SHARD_RESERVED_SECONDS", raising=False)

    with pytest.raises(pytest.UsageError):
        pytest_configure(SimpleNamespace())


def test_duration_filter_runs_after_marker_selection(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for filename in ("test_alpha.py", "test_beta.py"):
        (tests_dir / filename).write_text(
            "import pytest\n"
            "@pytest.mark.postgres\n"
            "def test_pg():\n"
            "    pass\n"
            "def test_unit():\n"
            "    pass\n",
            encoding="utf-8",
        )
    manifest = tmp_path / "durations.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "tests/test_alpha.py": {"seconds": 10, "tests": 1},
                    "tests/test_beta.py": {"seconds": 5, "tests": 1},
                },
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "GATE_SHARD": "1/2",
            "GATE_SHARD_DURATIONS": str(manifest),
            "GATE_SHARD_RESERVED_SECONDS": "0,0",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "postgres",
            "-p",
            "scripts.pytest_gate_shard",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "GATE_SHARD estimated loads (seconds): 10.0, 5.0" in result.stdout + result.stderr
