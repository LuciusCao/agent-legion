"""Frontend emoji lint (#1354): UI glyphs are MaterialIcon, never emoji.

M48 visual acceptance established the convention: UI glyphs come from
``@mui/icons-material`` (MaterialIcon), and emoji characters in frontend code
are a regression vector that used to slip through review. This check scans
frontend source files for the emoji code-point ranges from the issue —
U+1F300–1FAFF, U+2600–26FF, U+2700–27BF, U+2B00–2BFF and the U+FE0F variation
selector — and rejects any hit. Non-emoji typography (arrows like → U+2192,
box-drawing, CJK) sits outside these ranges and is deliberately unaffected.

The scan is full-content rather than diff-based (the issue's minimal form):
deterministic without a git base ref, and stricter per file — the pre-existing
workflowStudio status glyphs (⚠/✓/✗/✕/🧩, the 存量 convention the issue
exempts) are pinned as (path, code point) entries with reasons in
``config/architecture/frontend-emoji-exemptions.json`` instead. The pin is
matched both directions: a hit outside the pins fails, and a pin whose
character no longer appears in the file fails as stale (the exemption set
only shrinks — cleaning a file's glyphs forces the pin out).
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

__test__ = False

EXEMPTIONS_RELATIVE_PATH = "config/architecture/frontend-emoji-exemptions.json"

_SCAN_ROOTS = ("frontend/src", "frontend/scripts", "frontend/e2e", "frontend/stress")
_SCAN_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

_EMOJI = re.compile(
    "[\U0001f300-\U0001faff"  # U+1F300–U+1FAFF
    "\u2600-\u26ff"
    "\u2700-\u27bf"
    "\u2b00-\u2bff"
    "\ufe0f]"
)


class FrontendEmojiConfigurationError(ValueError):
    """Exemption configuration error captured by check_frontend_emoji."""


def find_emoji(source: str) -> list[tuple[int, str]]:
    """Return (lineno, code point) for every emoji-range char in the source."""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        for match in _EMOJI.finditer(line):
            hits.append((lineno, f"U+{ord(match.group()):04X}"))
    return hits


def load_frontend_emoji_exemptions(path: Path) -> dict[tuple[str, str], str]:
    """Load the pinned pre-existing glyphs: (path, code point) -> reason."""
    # Missing exemptions file == no exemptions: hits still fail unexempted,
    # so deleting the file equals emptying it (fixture-friendly).
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FrontendEmojiConfigurationError(f"Malformed JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "exemptions"}:
        raise FrontendEmojiConfigurationError(
            "Exemptions root must be a mapping with exactly 'version' and 'exemptions'"
        )
    if raw["version"] != 1:
        raise FrontendEmojiConfigurationError(f"Unsupported exemptions version: {raw['version']!r}")
    entries = raw["exemptions"]
    if not isinstance(entries, list):
        raise FrontendEmojiConfigurationError("exemptions must be a list")
    normalized: dict[tuple[str, str], str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "code_point", "reason"}:
            raise FrontendEmojiConfigurationError(
                f"exemption entries need exactly path/code_point/reason: {entry!r}"
            )
        key = (str(PurePosixPath(entry["path"])), str(entry["code_point"]))
        if not str(entry["reason"]).strip():
            raise FrontendEmojiConfigurationError(f"exemption reason must be non-empty: {key!r}")
        if not re.fullmatch(r"U\+[0-9A-F]{4,6}", key[1]):
            raise FrontendEmojiConfigurationError(f"code_point must look like 'U+26A0': {key!r}")
        normalized[key] = entry["reason"]
    return normalized


def scan_frontend_emoji(root: Path) -> dict[tuple[str, str], list[int]]:
    """All emoji-range hits as (path, code point) -> line numbers."""
    hits: dict[tuple[str, str], list[int]] = {}
    for scan_root in _SCAN_ROOTS:
        base = root / scan_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() not in _SCAN_EXTENSIONS or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for lineno, code_point in find_emoji(source):
                hits.setdefault((relative, code_point), []).append(lineno)
    return hits


def check_frontend_emoji(root: Path) -> list[str]:
    """Reject emoji-range characters in frontend source files (#1354)."""
    try:
        exemptions = load_frontend_emoji_exemptions(root / EXEMPTIONS_RELATIVE_PATH)
    except FrontendEmojiConfigurationError as exc:
        return [f"frontend emoji configuration: {exc}"]

    hits = scan_frontend_emoji(root)
    errors: list[str] = []
    for (relative, code_point), linenos in sorted(hits.items()):
        if (relative, code_point) in exemptions:
            continue
        lines = ",".join(str(lineno) for lineno in linenos)
        errors.append(
            f"{relative}:{lines}: emoji-range character {code_point} in frontend code; "
            "use MaterialIcon (@mui/icons-material) for UI glyphs (#1354)"
        )
    for relative, code_point in sorted(set(exemptions) - set(hits)):
        errors.append(
            f"{relative}: stale frontend-emoji exemption for {code_point}; "
            "the glyph no longer appears — remove the entry (exemptions only shrink)"
        )
    return errors
