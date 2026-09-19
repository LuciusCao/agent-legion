from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.storage_paths import ManagedPathError, resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import downstream_nodes

logger = logging.getLogger(__name__)


class StagedOutputs:
    """Reversible artifact staging for job rerun operations.

    `commit()` permanently removes staged files; `rollback()` restores them to
    their original locations. ``artifact_names`` are the output names staged
    for the affected closure (#508: the same set whose ``job_artifacts``
    manifest rows the rerun transaction deletes); names shared with nodes
    outside the closure are never staged (see ``stage_outputs``).
    """

    def __init__(
        self,
        staged_dir: Path,
        moves: list[tuple[Path, Path]],
        artifact_names: set[str],
    ) -> None:
        self._staged_dir = staged_dir
        self._moves = list(moves)
        self.artifact_names = frozenset(artifact_names)
        self._committed = False
        self._rolled_back = False

    def commit(self) -> None:
        """Permanently delete staged artifacts."""
        if self._committed or self._rolled_back:
            return
        for staged_path, _ in self._moves:
            if staged_path.exists():
                if staged_path.is_dir():
                    shutil.rmtree(staged_path)
                else:
                    staged_path.unlink()
        self._prune_staged_dir()
        self._committed = True

    def rollback(self) -> None:
        """Restore staged artifacts to their original locations."""
        if self._committed or self._rolled_back:
            return
        for staged_path, original_path in self._moves:
            if staged_path.exists():
                original_path.parent.mkdir(parents=True, exist_ok=True)
                if original_path.exists():
                    if original_path.is_dir():
                        shutil.rmtree(original_path)
                    else:
                        original_path.unlink()
                shutil.move(str(staged_path), str(original_path))
        self._prune_staged_dir()
        self._rolled_back = True

    def _prune_staged_dir(self) -> None:
        try:
            if self._staged_dir.exists() and not any(self._staged_dir.iterdir()):
                self._staged_dir.rmdir()
        except OSError:
            pass


class JobArtifactMutationService:
    """Service for reversible artifact mutations during job operations."""

    def __init__(self, jobs_dir: Path | None = None) -> None:
        self.jobs_dir = jobs_dir

    def stage_outputs(
        self,
        job: dict[str, Any],
        node_keys: Sequence[str],
        definition: WorkflowDefinition,
        *,
        closure: set[str] | frozenset[str] | None = None,
        extra_names: frozenset[str] | set[str] = frozenset(),
        extra_run_keys: frozenset[str] | set[str] = frozenset(),
    ) -> StagedOutputs:
        """Move rerun outputs and run histories to reversible staging.

        When ``closure`` is provided, only outputs declared by nodes inside the
        closure are staged. This supports targeted rerun-to operations where
        descendants outside the target closure must keep their artifacts.
        Staging is also name-scoped: an output name declared by any node
        outside the closure is left in place (adversarial review A3 — see
        ``job_artifact_staging_scope.staging_output_names``).

        Read-modify-write artifacts (declared as both an input and an output of
        the same node) are never staged: removing them would leave the node
        waiting forever on an input no rerun producer rewrites (#114). On a
        successful rerun the node rewrites them, so run semantics are unchanged.

        ``extra_names``/``extra_run_keys``（#645 codex 四轮 P1-2，upgrade
        专用）：新 definition 声明面之外、需要一并暂存的旧产物名与被删
        节点的运行历史目录（调用方从旧快照算好并按 A3 口径过滤）。它们
        进入 ``artifact_names``（清单行删除同集合）与文件移动面。

        Returns a :class:`StagedOutputs` handle. Callers should invoke
        ``commit()`` after a successful database transaction, or ``rollback()``
        if the transaction fails.
        """
        if self.jobs_dir is None:
            raise RuntimeError("JobArtifactMutationService requires jobs_dir")
        storage_dir = resolve_job_dir(job, self.jobs_dir)
        if not storage_dir.exists():
            storage_dir.mkdir(parents=True, exist_ok=True)

        affected_keys: set[str] = set(node_keys)
        for node_key in node_keys:
            if node_key not in definition.nodes:
                raise ValueError(f"Unknown node: {node_key}")
            affected_keys.update(downstream_nodes(definition, node_key))

        if closure is not None:
            affected_keys &= set(closure)

        # Node-scoped staging (adversarial review A3): a name shared with a
        # node outside the closure is never staged — see the pure helper.
        outputs = staging_output_names(definition, affected_keys) | set(extra_names)

        paths = set(outputs)
        paths.update(f"runs/{key}" for key in affected_keys | set(extra_run_keys))

        staged_dir = storage_dir / ".staged"
        staged_dir.mkdir(parents=True, exist_ok=True)

        moves: list[tuple[Path, Path]] = []
        try:
            for name in sorted(paths):
                original_path = (storage_dir / name).resolve()
                try:
                    original_path.relative_to(storage_dir)
                except ValueError as exc:
                    raise ValueError(f"Output path escapes artifact directory: {name}") from exc

                if original_path.exists():
                    staged_path = (staged_dir / name).resolve()
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    if staged_path.exists():
                        if staged_path.is_dir():
                            shutil.rmtree(staged_path)
                        else:
                            staged_path.unlink()
                    shutil.move(str(original_path), str(staged_path))
                    moves.append((staged_path, original_path))
        except (OSError, ValueError, ManagedPathError):
            # #204: this loop's escape routes are filesystem moves (OSError:
            # ENOSPC/EPERM/racing eviction) and resolve()/relative_to path
            # discipline (ManagedPathError — a ValueError subclass — from a
            # symlinked storage_dir escaping the job dir; the explicit
            # relative_to ValueError above is converted before it can reach
            # here). Either way the half-staged moves must roll back and the
            # original type propagates to the caller's conflict/failed
            # classification (job_execution, job_rerun).
            StagedOutputs(staged_dir, moves, outputs).rollback()
            raise

        return StagedOutputs(staged_dir, moves, outputs)
