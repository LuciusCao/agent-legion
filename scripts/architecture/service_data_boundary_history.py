"""Floor computation and rename carry for the boundary baseline (#292).

Split from ``service_data_boundary_monotonicity.py`` for the file-size
budget: the anchor parsing (HEAD / HEAD^ by default, or HEAD + the
``AGENT_LEGION_BUDGET_BASE`` override), entry-wise-min floor merge and the
#236-style rename floor carry live here; the guard itself (which errors to
raise) stays in the parent module.
"""

from __future__ import annotations

import json

from .budget_git import BudgetGitUnavailable, GitHelper

__test__ = False

BOUNDARY_BASELINE_RELATIVE_PATH = "config/architecture/service-data-boundary-baseline.json"


def committed_boundary_entries(text: str | None) -> dict[str, tuple[int, int, int]]:
    """Path -> (sql, primitives, dsn) from a committed baseline JSON (lenient)."""
    if text is None:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {}
    files = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(files, dict):
        return {}
    entries: dict[str, tuple[int, int, int]] = {}
    for key, value in files.items():
        if not isinstance(key, str):
            continue
        if (
            isinstance(value, list)
            and len(value) == 3
            and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            entries[key] = (value[0], value[1], value[2])
    return entries


def boundary_floors_and_history(
    git: GitHelper, anchors: tuple[str, ...]
) -> tuple[dict[str, tuple[int, int, int]], set[str]]:
    """Return (floors, historic_paths) from the anchor revisions.

    floors: per path the entry-wise minimum across anchors. historic_paths:
    paths already registered in the last (pre-change) anchor — HEAD^ by
    default, the base override when configured — the pre-change evidence
    for the new-entry test, which deliberately EXCLUDES HEAD so a committed
    attack (new bypasses + new baseline entry in one commit) cannot smuggle
    its own entry in as pre-existing (codex review on #305). Rename floor
    carry (#236 semantics, subagent review on #305): the old path's floor
    follows the file onto its new path, so a rename does not reset counts.
    """
    committed_by_revision = {
        revision: committed_boundary_entries(
            git.committed_file_text(revision, BOUNDARY_BASELINE_RELATIVE_PATH)
        )
        for revision in anchors
    }
    floors: dict[str, tuple[int, int, int]] = {}
    for committed in committed_by_revision.values():
        for path, triple in committed.items():
            if path in floors:
                current = floors[path]
                floors[path] = (
                    min(triple[0], current[0]),
                    min(triple[1], current[1]),
                    min(triple[2], current[2]),
                )
            else:
                floors[path] = triple

    rename_maps = [git.rename_map(revision) for revision in anchors]
    if any(rename_map is None for rename_map in rename_maps):
        raise BudgetGitUnavailable(
            "boundary monotonicity: rename detection could not run (worktree "
            "has untracked files and the snapshot index could not be built); "
            "failing closed rather than missing an unstaged rename"
        )
    historic_paths = set(committed_by_revision[anchors[-1]])
    renames = {n: o for m in reversed(rename_maps) if m is not None for n, o in m.items()}
    for new_path, old_path in renames.items():
        if old_path in floors:
            carried = floors[old_path]
            if new_path in floors:
                current = floors[new_path]
                floors[new_path] = (
                    min(carried[0], current[0]),
                    min(carried[1], current[1]),
                    min(carried[2], current[2]),
                )
            else:
                floors[new_path] = carried
            # The renamed file is as tracked as it was under its old name.
            historic_paths.add(new_path)

    return floors, historic_paths
