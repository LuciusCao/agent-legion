"""Git plumbing for the budget checks: anchors, renames, diagnostics.

Thin wrappers around the ``git`` CLI for the monotonic ceiling check: anchor
resolution (``HEAD`` / ``HEAD^``), committed file content, and rename
detection between a revision and the current worktree. Execution failures
(OSError, timeout) are remembered with their real reason so callers can
report the actual cause instead of guessing "shallow clone" (#236).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

__test__ = False

_GIT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class GitFailure:
    """A git invocation that could not run or timed out.

    ``reason`` is the underlying OSError / TimeoutExpired text; callers fold
    it into their diagnostics instead of flattening it into a shallow-clone
    guess.
    """

    args: tuple[str, ...]
    reason: str


class BudgetGitUnavailable(RuntimeError):
    """Rename detection cannot run; the check must fail closed (#238).

    Raised when untracked files exist but the worktree snapshot index could
    not be built: a plain diff is blind to untracked paths, so silently
    falling back would let an unstaged rename pass as a first-time
    registration — reopening the rename bypass the snapshot exists to close.
    """


class GitHelper:
    """Run git in ``root``, remembering OS-level failures for diagnostics."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.failures: list[GitFailure] = []
        self._rename_cache: dict[str, dict[str, str]] = {}
        self._snapshot_index: Path | None = None
        self._snapshot_failed = False
        self._untracked: bool | None = None

    def run(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str] | None:
        """Run git; None records an OSError/timeout, never a git exit code."""
        try:
            return subprocess.run(
                ["git", "-C", str(self._root), *args],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.failures.append(GitFailure(args=args, reason=str(exc)))
            return None

    def is_repository(self) -> bool:
        proc = self.run("rev-parse", "--git-dir")
        return proc is not None and proc.returncode == 0

    def is_worktree_root(self) -> bool:
        """True when ``root`` is itself the top of a git worktree.

        ``git -C <subdir>`` walks up to the enclosing repository, so a
        non-git scratch directory nested inside a git checkout (pytest
        tmp_path) would otherwise look like a repo and run rename detection
        against the *outer* repository's files. Callers gate the snapshot
        machinery on this.
        """
        proc = self.run("rev-parse", "--show-toplevel")
        return (
            proc is not None
            and proc.returncode == 0
            and (Path(proc.stdout.strip()).resolve() == self._root.resolve())
        )

    def has_git_failures(self) -> bool:
        """True when any git invocation failed to run or timed out.

        A failed ``is_repository`` from a missing binary or a timeout must
        not be mistaken for a plain non-git checkout (#236): callers use
        this to fail loudly instead of silently skipping the check.
        """
        return bool(self.failures)

    def revision_resolvable(self, revision: str) -> bool:
        proc = self.run("rev-parse", "--verify", f"{revision}^{{commit}}")
        return proc is not None and proc.returncode == 0

    def committed_file_text(self, revision: str, rel_path: str) -> str | None:
        """File content at a revision; None when unavailable (non-git
        checkout, path predating the registry). A missing anchor never
        errors — the caller only fires when an entry exists on both sides."""
        proc = self.run("show", f"{revision}:{rel_path}")
        if proc is None or proc.returncode != 0:
            return None
        return proc.stdout

    def _has_untracked_files(self) -> bool:
        # Cached: every rename_map call (one per anchor) asks, and a single
        # check invocation sees one immutable worktree state.
        if self._untracked is None:
            proc = self.run("ls-files", "--others", "--exclude-standard")
            self._untracked = bool(
                proc is not None and proc.returncode == 0 and proc.stdout.strip()
            )
        return self._untracked

    def _worktree_snapshot_index(self) -> Path | None:
        """Temp index snapshotting the full worktree (tracked + untracked).

        A plain ``git diff <rev>`` cannot pair a deleted path with an
        untracked new one — an unstaged rename shows as D + untracked and
        rename detection never fires. Staging the worktree into a throwaway
        index (``GIT_INDEX_FILE``) lets git's own similarity engine see the
        new file; the caller's real index is never touched. Built once and
        shared across anchors; None when the snapshot could not be built
        (callers fall back to the plain diff).
        """
        if self._snapshot_index is not None or self._snapshot_failed:
            return self._snapshot_index
        handle, name = tempfile.mkstemp(prefix="budget-monotonicity-index-")
        os.close(handle)
        index_path = Path(name)
        # A zero-byte index file is rejected by git ("index file smaller than
        # expected"); an absent one is a valid empty index, so remove the
        # placeholder and let git create it.
        index_path.unlink()
        env = {**os.environ, "GIT_INDEX_FILE": str(index_path)}
        add = self.run("add", "-A", env=env)
        if add is None or add.returncode != 0:
            self._snapshot_failed = True
            index_path.unlink(missing_ok=True)
            return None
        self._snapshot_index = index_path
        return index_path

    def _rename_pairs(
        self, args: tuple[str, ...], env: dict[str, str] | None
    ) -> dict[str, str] | None:
        """Parse ``-z --name-status`` rename records; None when git failed."""
        proc = self.run(*args, env=env)
        if proc is None or proc.returncode != 0:
            return None
        renames: dict[str, str] = {}
        fields = proc.stdout.split("\0")
        index = 0
        while index + 2 < len(fields):
            status, old, new = fields[index], fields[index + 1], fields[index + 2]
            if status.startswith("R") and old and new:
                renames[new] = old
            index += 3
        return renames

    def rename_map(self, revision: str) -> dict[str, str] | None:
        """Rename map {new_path: old_path} from ``revision`` to the current
        worktree; None when untracked files exist but the worktree snapshot
        could not be built — the caller must treat that as a hard error, not
        fall back to a plain diff (a plain diff is blind to untracked files,
        so an unstaged rename would be missed and the new path would pass as
        a first-time registration, reopening the rename bypass — codex review
        on PR #238). A failed plain diff in a tracked-only worktree is NOT
        None-worthy: with no untracked files there is nothing a plain diff
        could miss, and an unresolvable anchor is already reported by
        ``_unresolvable_anchor_errors`` — an empty map is the right answer.
        """
        cached = self._rename_cache.get(revision)
        if cached is not None:
            return cached
        # A scratch directory nested inside a git checkout is not a repo of
        # its own: git -C would walk up to the outer repository and "detect"
        # renames against its files. No rename detection outside a real
        # worktree root.
        if not self.is_worktree_root():
            self._rename_cache[revision] = {}
            return {}
        if self._has_untracked_files():
            snapshot = self._worktree_snapshot_index()
            if snapshot is None:
                # Fail closed: without the snapshot we cannot see untracked
                # files, and a rename whose new path is untracked is exactly
                # the shape the snapshot exists to catch.
                return None
            env = {**os.environ, "GIT_INDEX_FILE": str(snapshot)}
            snapshot_renames = self._rename_pairs(
                (
                    "diff",
                    "--find-renames",
                    "--diff-filter=R",
                    "--name-status",
                    "-z",
                    "--cached",
                    revision,
                ),
                env,
            )
            if snapshot_renames is None:
                return None
            self._rename_cache[revision] = snapshot_renames
            return snapshot_renames
        fallback = self._rename_pairs(
            (
                "diff",
                "--find-renames",
                "--diff-filter=R",
                "--name-status",
                "-z",
                revision,
            ),
            None,
        )
        if fallback is None:
            self._rename_cache[revision] = {}
            return {}
        self._rename_cache[revision] = fallback
        return fallback

    def cleanup(self) -> None:
        """Discard the worktree snapshot index; safe to call repeatedly."""
        if self._snapshot_index is not None:
            self._snapshot_index.unlink(missing_ok=True)
            self._snapshot_index = None

    def diagnostics(self) -> str:
        """One-line summary of recorded git failures, '' when none."""
        if not self.failures:
            return ""
        seen: list[str] = []
        for failure in self.failures:
            summary = f"git {' '.join(failure.args[:2])}: {failure.reason}"
            if summary not in seen:
                seen.append(summary)
        return "; ".join(seen)
