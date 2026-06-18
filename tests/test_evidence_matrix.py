"""Consistency tests for the Phase 1-5 reverse evidence matrix."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.quality.invariants import load_registry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PROJECT_ROOT / "docs" / "architecture" / "workspace-executor-evidence-matrix.md"
REGISTRY_PATH = PROJECT_ROOT / "config" / "architecture-invariants.yaml"

REQUIRED_COLUMNS = [
    "Promise",
    "Boundary",
    "Invariant ID",
    "Quick evidence",
    "Full evidence",
    "Result",
    "Follow-up task",
]
ALLOWED_RESULTS = {"Verified", "Gap", "Deferred", "Behavior-only"}


def _parse_matrix_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Locate and parse the first Markdown table with the required columns."""
    assert path.exists(), f"evidence matrix file not found: {path}"
    lines = path.read_text(encoding="utf-8").splitlines()

    for idx, line in enumerate(lines):
        if "|" not in line:
            continue
        header_cells = [cell.strip() for cell in line.split("|")]
        # Drop outer empty cells produced by leading/trailing pipes.
        if header_cells and header_cells[0] == "":
            header_cells = header_cells[1:]
        if header_cells and header_cells[-1] == "":
            header_cells = header_cells[:-1]

        if header_cells != REQUIRED_COLUMNS:
            continue

        if idx + 1 >= len(lines) or "|" not in lines[idx + 1]:
            continue

        separator_cells = [cell.strip() for cell in lines[idx + 1].split("|")]
        if separator_cells and separator_cells[0] == "":
            separator_cells = separator_cells[1:]
        if separator_cells and separator_cells[-1] == "":
            separator_cells = separator_cells[:-1]

        if len(separator_cells) != len(header_cells):
            continue
        if not all(set(cell) <= {"-", ":"} for cell in separator_cells):
            continue

        rows: list[dict[str, str]] = []
        for data_line in lines[idx + 2 :]:
            if "|" not in data_line:
                break
            data_cells = [cell.strip() for cell in data_line.split("|")]
            if data_cells and data_cells[0] == "":
                data_cells = data_cells[1:]
            if data_cells and data_cells[-1] == "":
                data_cells = data_cells[:-1]
            if all(cell == "" for cell in data_cells):
                continue
            if len(data_cells) != len(header_cells):
                pytest.fail(
                    f"row has {len(data_cells)} columns but header has {len(header_cells)}: "
                    f"{data_line!r}"
                )
            rows.append(dict(zip(header_cells, data_cells, strict=True)))

        return header_cells, rows

    pytest.fail(f"could not find a table with columns {REQUIRED_COLUMNS!r} in {path}")


def test_matrix_file_exists():
    assert MATRIX_PATH.exists(), "evidence matrix file is missing"


def test_matrix_has_required_columns():
    headers, rows = _parse_matrix_table(MATRIX_PATH)
    assert headers == REQUIRED_COLUMNS
    assert rows, "matrix table has no data rows"


def test_matrix_rows_have_valid_results():
    _, rows = _parse_matrix_table(MATRIX_PATH)
    for row in rows:
        result = row["Result"]
        assert result in ALLOWED_RESULTS, f"invalid result {result!r} in row {row!r}"


def test_non_deferred_rows_have_unique_invariant_id_or_behavior_only():
    _, rows = _parse_matrix_table(MATRIX_PATH)
    seen_ids: set[str] = set()
    for row in rows:
        if row["Result"] == "Deferred":
            continue

        invariant_id = row["Invariant ID"]
        if invariant_id == "Behavior-only":
            continue

        assert invariant_id, f"non-deferred row missing invariant ID: {row!r}"
        assert invariant_id not in seen_ids, f"duplicate invariant ID in matrix: {invariant_id}"
        seen_ids.add(invariant_id)


def test_follow_up_task_matches_result():
    _, rows = _parse_matrix_table(MATRIX_PATH)
    for row in rows:
        result = row["Result"]
        follow_up = row["Follow-up task"]
        if result in {"Verified", "Behavior-only"}:
            assert follow_up == "N/A", (
                f"row with result {result!r} must use 'N/A' follow-up task, got {follow_up!r}"
            )
        elif result == "Gap":
            assert follow_up and follow_up != "N/A", (
                f"gap row must reference a follow-up task, got {follow_up!r}"
            )
        elif result == "Deferred":
            assert follow_up and follow_up != "N/A", (
                f"deferred row must reference a later spec/issue, got {follow_up!r}"
            )


def test_every_registered_invariant_appears_in_matrix():
    invariants = load_registry(REGISTRY_PATH)
    _, rows = _parse_matrix_table(MATRIX_PATH)
    matrix_ids = {row["Invariant ID"] for row in rows if row["Invariant ID"] != "Behavior-only"}

    missing = [inv.id for inv in invariants if inv.id not in matrix_ids]
    assert not missing, f"registered invariant(s) missing from evidence matrix: {missing}"


def test_verified_rows_are_registered_invariants():
    invariants = load_registry(REGISTRY_PATH)
    registered_ids = {inv.id for inv in invariants}
    _, rows = _parse_matrix_table(MATRIX_PATH)

    for row in rows:
        if row["Result"] != "Verified":
            continue
        invariant_id = row["Invariant ID"]
        if invariant_id == "Behavior-only":
            continue
        assert invariant_id in registered_ids, (
            f"matrix row with result 'Verified' references unregistered invariant: {invariant_id}"
        )
