from __future__ import annotations


class WorkspaceRoundRobin:
    """Deterministic round-robin ordering of runnable workspace IDs.

    The scheduler maintains only the ID of the workspace that most recently
    successfully claimed work.  On each call to ``order`` it deduplicates the
    supplied runnable workspace IDs while preserving their original input order,
    then rotates that list so the workspace following the previous winner is
    considered first.

    This component intentionally knows nothing about executor capacities,
    futures, thread pools, or database state.
    """

    def __init__(self) -> None:
        self._last_winner: str | None = None

    def order(self, workspace_ids: list[str]) -> list[str]:
        """Return deduplicated ``workspace_ids`` rotated after the last winner."""
        seen: set[str] = set()
        deduped: list[str] = []
        for ws_id in workspace_ids:
            if ws_id not in seen:
                seen.add(ws_id)
                deduped.append(ws_id)

        if not deduped or self._last_winner is None:
            return deduped

        try:
            start = deduped.index(self._last_winner)
        except ValueError:
            return deduped

        # Rotate so the workspace after the previous winner comes first.
        return deduped[start + 1 :] + deduped[: start + 1]

    def complete_pass(self, workspace_id: str) -> None:
        """Record ``workspace_id`` as the most recent successful claimant."""
        self._last_winner = workspace_id
