"""Tests for scripts/architecture/frontend_emoji.py (#1354)."""

import json
from pathlib import Path

import pytest

from scripts.architecture.frontend_emoji import check_frontend_emoji, find_emoji

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_exemptions(root: Path, entries: list[dict[str, object]] | None = None) -> None:
    payload = {"version": 1, "exemptions": entries or []}
    write(
        root / "config/architecture/frontend-emoji-exemptions.json",
        json.dumps(payload, indent=2) + "\n",
    )


def test_find_emoji_covers_issue_ranges_and_spares_typography():
    source = (
        "const a = '😀 ⚠ ✓ ⬤';\n"  # U+1F600, U+26A0, U+2713, U+2B24
        "const b = '→ ← ≠ ± 中文';\n"  # arrows / math / CJK stay legal
        "const c = 'VS16️⃣ here';\n"  # bare U+FE0F variation selector
    )

    hits = find_emoji(source)

    assert (1, "U+1F600") in hits
    assert (1, "U+26A0") in hits
    assert (1, "U+2713") in hits
    assert (1, "U+2B24") in hits
    assert (3, "U+FE0F") in hits
    assert not any(lineno == 2 for lineno, _cp in hits)


def test_flags_emoji_in_scanned_frontend_roots(tmp_path):
    write_exemptions(tmp_path)
    write(tmp_path / "frontend/src/components/Card.tsx", "export const C = () => <span>⚠️</span>;\n")

    errors = check_frontend_emoji(tmp_path)

    assert any("Card.tsx:1" in error and "U+26A0" in error for error in errors)
    assert any("Card.tsx:1" in error and "U+FE0F" in error for error in errors)


def test_scans_e2e_stress_and_scripts_roots(tmp_path):
    write_exemptions(tmp_path)
    write(tmp_path / "frontend/e2e/smoke.spec.ts", "const s = '😀';\n")
    write(tmp_path / "frontend/scripts/tool.mjs", "const s = '⚠';\n")

    errors = check_frontend_emoji(tmp_path)

    assert any("smoke.spec.ts" in error for error in errors)
    assert any("tool.mjs" in error for error in errors)


def test_ignores_non_code_files(tmp_path):
    write_exemptions(tmp_path)
    write(tmp_path / "frontend/src/README.md", "# ⚠ docs are not code\n")
    write(tmp_path / "frontend/src/styles.css", "/* 😀 */\n")

    assert not check_frontend_emoji(tmp_path)


def test_exemption_suppresses_and_stale_exemption_fails(tmp_path):
    write(tmp_path / "frontend/src/legacy.tsx", "export const mark = '✓';\n")
    entry = {
        "path": "frontend/src/legacy.tsx",
        "code_point": "U+2713",
        "occurrences": 1,
        "reason": "存量字形",
    }
    write_exemptions(tmp_path, [entry])

    assert not check_frontend_emoji(tmp_path)

    # A different code point in the same pinned file is still a violation.
    write(tmp_path / "frontend/src/legacy.tsx", "export const mark = '✓';\nexport const w = '⚠';\n")
    errors = check_frontend_emoji(tmp_path)
    assert any("legacy.tsx" in error and "U+26A0" in error for error in errors)

    # Once the file is clean, the pin is stale and must be removed.
    write(tmp_path / "frontend/src/legacy.tsx", "export const mark = 'ok';\n")
    errors = check_frontend_emoji(tmp_path)
    assert any("stale" in error and "legacy.tsx" in error for error in errors)


def test_same_code_point_occurrence_count_is_pinned(tmp_path):
    """#1374 E1: a new line with an already-exempt code point must not escape."""
    path = tmp_path / "frontend/src/legacy.tsx"
    entry = {
        "path": "frontend/src/legacy.tsx",
        "code_point": "U+2713",
        "occurrences": 1,
        "reason": "one legacy glyph",
    }
    write(path, "export const oldMark = '✓';\n")
    write_exemptions(tmp_path, [entry])
    assert not check_frontend_emoji(tmp_path)

    write(path, "export const oldMark = '✓';\nexport const newMark = '✓';\n")
    errors = check_frontend_emoji(tmp_path)
    assert any("occurs 2 times" in error and "pins 1" in error for error in errors)

    write(path, "export const oldMark = '✓✓';\n")
    errors = check_frontend_emoji(tmp_path)
    assert any("occurs 2 times" in error for error in errors)


def test_exemption_entries_require_reason(tmp_path):
    write(tmp_path / "frontend/src/x.ts", "export const x = 1;\n")
    write_exemptions(
        tmp_path,
        [
            {
                "path": "frontend/src/x.ts",
                "code_point": "U+26A0",
                "occurrences": 1,
                "reason": " ",
            }
        ],
    )

    errors = check_frontend_emoji(tmp_path)

    assert any("configuration" in error for error in errors)


def test_missing_exemptions_file_behaves_as_empty(tmp_path):
    """No config file: clean trees pass, hits still fail unexempted."""
    write(tmp_path / "frontend/src/x.ts", "export const x = 1;\n")
    assert not check_frontend_emoji(tmp_path)

    write(tmp_path / "frontend/src/y.ts", "export const y = '😀';\n")
    errors = check_frontend_emoji(tmp_path)
    assert any("y.ts" in error and "U+1F600" in error for error in errors)
