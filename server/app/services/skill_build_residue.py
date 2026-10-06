"""Build-residue ignore rule shared by skill/_shared authoring (#1038).

Running a skill's ``scripts/validate_output.py`` locally makes Python write
``__pycache__/*.pyc`` next to the scripts it imports. That bytecode is
never authored content, yet it used to wedge the whole authoring surface:
the lossless ``_shared`` editing snapshot refused the non-UTF-8 file (422,
all-or-nothing) and ``save_skill_version`` refused the dirty tree (409).

One rule, one place: a path is build residue when any component is
``__pycache__`` or its name ends in ``.pyc`` (case-insensitive). Callers
SKIP residue (exports, dirty checks, ``_shared`` swap carries it over) —
they never remove it, and never stage it into a commit or the git index.
"""

from __future__ import annotations

from pathlib import PurePosixPath

PYCACHE_DIR = "__pycache__"
PYC_SUFFIX = ".pyc"

# Seeded into a newly created skill repo (create_skill) when the payload
# does not declare its own .gitignore: future residue stays untracked.
BUILD_RESIDUE_GITIGNORE = f"{PYCACHE_DIR}/\n*{PYC_SUFFIX}\n"

BUILD_RESIDUE_HINT = (
    "this is build residue (Python bytecode cache, __pycache__/ or *.pyc), "
    "not authored content; it is safe to remove"
)


def binary_file_error(exc: UnicodeError) -> str:
    """422 detail for a NON-residue file that is not UTF-8: say what it is
    and what to do, not just the codec error (#1038)."""
    return (
        f"not UTF-8 text ({exc}); only UTF-8 text files are editable — remove or "
        "convert this binary file (if it is a build artifact or cache, removing "
        "it is safe)"
    )


def is_residue_name(name: str) -> bool:
    """One path component: a ``__pycache__`` dir or a ``*.pyc`` file."""
    lowered = name.lower()
    return lowered == PYCACHE_DIR or lowered.endswith(PYC_SUFFIX)


def is_build_residue(path: str) -> bool:
    """A relative posix path lies inside / is build residue."""
    return any(is_residue_name(part) for part in PurePosixPath(path).parts)


def residue_payload_errors(paths: list[str]) -> list[dict[str, str]]:
    """422 entries for payload paths that are build residue: create_skill
    and save_skill_version refuse them before any write (the editing
    snapshots skip residue, so a stored one could never be read back)."""
    return [
        {"path": raw, "error": f"{BUILD_RESIDUE_HINT}; omit it from the payload"}
        for raw in paths
        if is_build_residue(raw)
    ]
