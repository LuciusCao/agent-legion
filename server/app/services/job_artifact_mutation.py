from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import downstream_nodes

logger = logging.getLogger(__name__)


class StagedOutputs:
    """Reversible artifact staging for job rerun operations.

    `commit()` permanently removes staged files; `rollback()` restores them to
    their original locations.
    """

    def __init__(self, staged_dir: Path, moves: list[tuple[Path, Path]]) -> None:
        self._staged_dir = staged_dir
        self._moves = list(moves)
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
    ) -> StagedOutputs:
        """Move rerun outputs and run histories to reversible staging.

        When ``closure`` is provided, only outputs declared by nodes inside the
        closure are staged. This supports targeted rerun-to operations where
        descendants outside the target closure must keep their artifacts.

        Read-modify-write artifacts (declared as both an input and an output of
        the same node) are never staged: removing them would leave the node
        waiting forever on an input no rerun producer rewrites (#114). On a
        successful rerun the node rewrites them, so run semantics are unchanged.

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

        outputs: set[str] = set()
        for key in affected_keys:
            node = definition.nodes[key]
            outputs.update(set(node.outputs) - set(node.inputs))

        paths = set(outputs)
        paths.update(f"runs/{key}" for key in affected_keys)

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
        except Exception:
            StagedOutputs(staged_dir, moves).rollback()
            raise

        return StagedOutputs(staged_dir, moves)
