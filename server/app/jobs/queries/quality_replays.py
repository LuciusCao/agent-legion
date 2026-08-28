"""Quality-replay persistence on the JobQueries facade (BOUNDARY-DATA-001).

The service layer (``services/quality_replays.py``) keeps the replay flow but
reaches the ``quality_replays`` table through these facade methods; the raw
SQL lives here with the rest of the queries layer. Only the writes that the
exception-layering rework (#204) introduced live here for now — the legacy
read/reconcile SQL in the service is grandfathered by the baseline ratchet.
"""

from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class QualityReplayQueriesMixin(ConnectionQueriesMixin):
    def delete_replay_if_active(self, replay_id: str) -> None:
        """Delete one replay row while it is still pending/running.

        Compensation for an unexpected (non-business) error during replay
        setup (#204): a half-created pending row would otherwise block every
        retry at the one-active-replay guard. The status guard keeps terminal
        replays (succeeded/failed) untouched — those are user-visible
        history, not half-created state.
        """
        with self.write() as conn:
            conn.execute(
                "delete from quality_replays where id = %s and status in ('pending', 'running')",
                (replay_id,),
            )
