"""Candidate skill directory listing for the Studio skill picker (#327).

The picker offers the directories directly under ``<skills_root>/<scope>/``
as autocomplete candidates; a selected or typed name still goes through
``SkillValidator`` (POST /api/skills/validate) before it becomes a binding —
this service only lists directory names, it never inspects content.
"""

from __future__ import annotations

from pathlib import Path


class SkillBrowser:
    """List candidate skill directories one scope level under the base dir."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir.expanduser()

    def list_directories(self, scope: str) -> tuple[str, ...]:
        """Sorted directory names under ``<base_dir>/<scope>/``.

        Invalid scopes (empty, absolute, or escaping the base dir) and
        missing/unreadable directories all degrade to an empty listing: this
        is a picker convenience, not an assertion about the filesystem.
        """
        scope_dir = self._resolve_scope(scope)
        if scope_dir is None or not scope_dir.is_dir():
            return ()
        try:
            return tuple(sorted(child.name for child in scope_dir.iterdir() if child.is_dir()))
        except OSError:
            # Directory vanished or turned unreadable between the checks and
            # the listing: degrade to "no candidates" instead of a 500.
            return ()

    def _resolve_scope(self, scope: str) -> Path | None:
        if not scope or not scope.strip():
            return None
        candidate = (self._base_dir / scope.strip()).resolve()
        # Strictly-inside only: the base dir itself is never a valid scope.
        if self._base_dir.resolve() not in candidate.parents:
            return None
        return candidate
