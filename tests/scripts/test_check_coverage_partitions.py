from __future__ import annotations

import json
from pathlib import Path

from scripts.check_coverage_partitions import (
    PARTITIONS,
    Partition,
    evaluate,
    frontend_line_totals,
    main,
)


def _write_coverage_final(path: Path, files: dict[str, list[tuple[int, int, int]]]) -> None:
    """Write a minimal istanbul report: {file: [(start_line, end_line, hits)]}."""
    payload = {}
    for name, statements in files.items():
        payload[name] = {
            "s": {str(i): hits for i, (_s, _e, hits) in enumerate(statements)},
            "statementMap": {
                str(i): {
                    "start": {"line": start, "column": 0},
                    "end": {"line": end, "column": 1},
                }
                for i, (start, end, _hits) in enumerate(statements)
            },
        }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_frontend_line_totals_derives_lines_from_statement_spans(tmp_path: Path) -> None:
    report = tmp_path / "coverage-final.json"
    _write_coverage_final(
        report,
        {
            "/repo/src/api/a.ts": [(1, 2, 1), (3, 5, 0)],
            "/repo/src/pages/B.tsx": [(10, 10, 3)],
        },
    )

    totals = frontend_line_totals(report)

    assert totals["src/api/a.ts"] == (2, 5)
    assert totals["src/pages/B.tsx"] == (1, 1)


def test_evaluate_flags_partitions_below_floor() -> None:
    partitions = (
        Partition("api", "frontend", ("src/api/",), 80.0),
        Partition("missing", "backend", ("server/app/nope.py",), 70.0),
    )
    totals = {
        "frontend": {"src/api/a.ts": (5, 10), "src/pages/B.tsx": (0, 10)},
        "backend": {},
    }

    rows, violations = evaluate(partitions, totals, {"frontend", "backend"})

    assert any("api: 50.0% lines (5/10, floor 80%) BELOW FLOOR" in row for row in rows)
    assert any("missing: NO DATA" in row for row in rows)
    assert len(violations) == 2


def test_evaluate_skips_unprovided_sources() -> None:
    partitions = (Partition("backend x", "backend", ("server/app/x.py",), 70.0),)

    rows, violations = evaluate(partitions, {"backend": {}, "frontend": {}}, {"frontend"})

    assert rows == ["backend x: SKIP (no backend coverage data)"]
    assert violations == []


def test_evaluate_passes_partitions_at_floor() -> None:
    partitions = (Partition("api", "frontend", ("src/api/",), 80.0),)
    totals = {"frontend": {"src/api/a.ts": (8, 10)}, "backend": {}}

    _rows, violations = evaluate(partitions, totals)

    assert violations == []


def test_worker_execution_plane_partition_covers_worker_tree() -> None:
    """Issue #275: the worker/ execution plane needs its own floor — the
    server-scoped global 85% never measured it. The partition matches every
    file under worker/ (directory prefix), and the baseline-derived floor
    must hold against a representative coverage snapshot (89.15% measured on
    the full quick suite, 2026-08-30)."""
    worker_partition = next(p for p in PARTITIONS if p.prefixes == ("worker/",))

    assert worker_partition.source == "backend"
    assert worker_partition.min_lines == 85.0

    totals = {
        "backend": {
            "worker/supervisor.py": (159, 181),
            "worker/upload/queue.py": (168, 181),
            "server/app/services/other.py": (0, 100),
        },
        "frontend": {},
    }
    rows, violations = evaluate((worker_partition,), totals, {"backend", "frontend"})

    # Only worker/ files count toward the partition (server files excluded).
    assert any(
        "backend worker execution plane: 90.3% lines (327/362, floor 85%) OK" in row for row in rows
    )
    assert violations == []


def test_worker_execution_plane_partition_flags_regression() -> None:
    """A worker/ regression below the floor must be flagged — the partition
    is what makes the execution plane unable to regress invisibly."""
    worker_partition = next(p for p in PARTITIONS if p.prefixes == ("worker/",))
    totals = {"backend": {"worker/supervisor.py": (100, 181)}, "frontend": {}}

    _rows, violations = evaluate((worker_partition,), totals, {"backend"})

    assert violations == ["backend worker execution plane: 55.2% lines below floor 85%"]


def test_worker_partition_reports_no_data_without_worker_files() -> None:
    """Backend data without any worker/ files (the pre-#275 shard shape)
    is a violation in enforce mode, not a silent skip."""
    worker_partition = next(p for p in PARTITIONS if p.prefixes == ("worker/",))
    totals = {"backend": {"server/app/services/other.py": (90, 100)}, "frontend": {}}

    rows, violations = evaluate((worker_partition,), totals, {"backend"})

    assert any("NO DATA" in row for row in rows)
    assert violations == ["backend worker execution plane: no matching files in coverage data"]


def test_main_reports_violations_without_failing(tmp_path: Path, capsys) -> None:
    report = tmp_path / "coverage-final.json"
    _write_coverage_final(report, {"/repo/src/api/a.ts": [(1, 10, 0)]})

    exit_code = main(["--frontend", str(report)])

    assert exit_code == 0
    assert "BELOW FLOOR" in capsys.readouterr().out


def test_main_enforce_fails_on_violation(tmp_path: Path) -> None:
    report = tmp_path / "coverage-final.json"
    _write_coverage_final(report, {"/repo/src/api/a.ts": [(1, 10, 0)]})

    assert main(["--frontend", str(report), "--enforce"]) == 1


def test_main_enforce_passes_when_all_partitions_meet_floor(tmp_path: Path) -> None:
    report = tmp_path / "coverage-final.json"
    files = {}
    for partition in PARTITIONS:
        if partition.source != "frontend":
            continue
        for prefix in partition.prefixes:
            path = f"{prefix}placeholder.ts" if prefix.endswith("/") else prefix
            files[f"/repo/{path}"] = [(1, 10, 1)]
    _write_coverage_final(report, files)

    exit_code = main(["--frontend", str(report), "--enforce"])

    assert exit_code == 0
