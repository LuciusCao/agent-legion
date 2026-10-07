"""Git-anchored monotonicity for the Agent catalog caller allowlist (#1033).

``agent_definition_callers`` compared only the working tree, so a single
commit could add a direct catalog caller AND list it in
``config/architecture/agent-definition-catalog-callers.json`` (or drop the
symbol it calls from ``symbols``) and pass — the "allowlist only shrinks"
promise was review discipline, not machine-checked (#987 codex R2). This
module reuses the budget / boundary anchor plumbing (``budget_anchors``:
HEAD / HEAD^ by default, HEAD + ``AGENT_LEGION_BUDGET_BASE`` when set, HEAD
only under the release-train opt-out; unresolvable anchors hard-fail, a
non-git directory stays quiet):

- ``files`` only shrinks: an entry absent from any anchor's allowlist is a
  new direct caller (a git rename of an anchored entry carries over, #236
  semantics);
- ``symbols`` is judged as the union with every anchor's symbols: dropping
  a symbol does not release its callers — the caller scan keeps covering it
  until no production file references it any more (then it simply has no
  hits and needs no further ceremony).

An anchor without the allowlist file (the guard predates it) contributes
nothing, so the very first registration is unconstrained.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from .budget_anchors import anchor_revisions, release_train_opt_out, unresolvable_anchors_errors
from .budget_git import BudgetGitUnavailable, GitHelper

__test__ = False

_CHECK = "agent definition caller allowlist"


def committed_allowlist(text: str | None) -> tuple[frozenset[str], frozenset[str]] | None:
    """(symbols, files) from a committed allowlist; lenient, None when absent."""
    if text is None:
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    symbols = raw.get("symbols")
    files = raw.get("files")
    return (
        frozenset(s for s in symbols if isinstance(s, str))
        if isinstance(symbols, list)
        else frozenset(),
        frozenset(str(PurePosixPath(f)) for f in files if isinstance(f, str))
        if isinstance(files, list)
        else frozenset(),
    )


def anchored_allowlist(
    root: Path, baseline_relative_path: str, files: frozenset[str]
) -> tuple[list[str], frozenset[str]]:
    """Return (errors, anchor symbols) for the working-tree allowlist ``files``.

    errors: unresolvable anchors, or ``files`` entries missing from an
    anchor's allowlist. anchor symbols: the union of every anchor's
    ``symbols`` — the caller scan must keep covering them (module docstring).
    """
    git = GitHelper(root)
    try:
        if not git.is_repository():
            return [], frozenset()
        anchors = anchor_revisions(release_train=release_train_opt_out())
        errors = unresolvable_anchors_errors(git, _CHECK, anchors)
        if errors:
            return errors, frozenset()
        anchor_symbols: set[str] = set()
        added: dict[str, str] = {}
        for revision in anchors:
            committed = committed_allowlist(
                git.committed_file_text(revision, baseline_relative_path)
            )
            if committed is None:
                continue
            committed_symbols, committed_files = committed
            anchor_symbols |= committed_symbols
            renames = git.rename_map(revision)
            if renames is None:
                raise BudgetGitUnavailable(
                    f"{_CHECK} monotonicity: rename detection could not run (worktree "
                    "has untracked files and the snapshot index could not be built); "
                    "failing closed rather than missing an unstaged rename"
                )
            for entry in sorted(files - committed_files):
                if renames.get(entry) not in committed_files:
                    added.setdefault(entry, revision)
        for entry, revision in sorted(added.items()):
            errors.append(
                f"{baseline_relative_path}: entry {entry} is not in the allowlist at git "
                f"anchor {revision}; the allowlist only shrinks — resolve agent node "
                "profiles via server/app/services/agent_node_profile instead (#1033)"
            )
        return errors, frozenset(anchor_symbols)
    except BudgetGitUnavailable as exc:
        return [str(exc)], frozenset()
    finally:
        git.cleanup()
