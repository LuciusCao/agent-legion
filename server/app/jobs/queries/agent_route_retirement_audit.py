"""Consistent, least-privilege reads for the historical Agent-route audit (#936).

The caller binds the facade to a server-enforced read-only DSN. All rows share
one repeatable-read transaction, so routes, pins, definitions and queued
requests cannot be read from different database epochs. Only the definition
bodies required for in-memory validation are selected; manifests, inputs,
runtime configs and creator metadata are never read.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class AgentRouteRetirementQueriesMixin(ConnectionQueriesMixin):
    def agent_route_retirement_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Read every object whose legacy Agent identity route retirement could affect."""
        with self._connect_read() as conn:
            conn.execute("begin transaction isolation level repeatable read read only")
            jobs = conn.execute(
                """
                select id, workspace_id, run_id, status, workflow_revision_id,
                       workflow_definition_snapshot_json
                from jobs order by workspace_id, id
                """
            ).fetchall()
            revisions = conn.execute(
                """
                select id, workspace_id, version, definition_json
                from workflow_revisions where status='active'
                order by workspace_id, version, id
                """
            ).fetchall()
            runs = conn.execute(
                """
                select id, workspace_id, status, frozen_pins_json
                from runs where frozen_pins_json <> '{}' order by workspace_id, id
                """
            ).fetchall()
            routes = conn.execute(
                """
                select workspace_id, node_key, target_kind, target_id
                from workspace_node_routes order by workspace_id, node_key
                """
            ).fetchall()
            definitions = conn.execute(
                """
                select workspace_id, entity_key, version, status,
                       definition_json, definition_hash
                from versioned_entities where entity_type='agent'
                order by workspace_id, entity_key, version
                """
            ).fetchall()
            requests = conn.execute(
                """
                select execution_id, workspace_id, job_id, node_key, kind,
                       profile_source, agent_id, agent_definition_hash, pinned_agent_version
                from agent_execution_requests where state='queued'
                order by workspace_id, job_id, node_key, execution_id
                """
            ).fetchall()
        return {
            "jobs": [dict(row) for row in jobs],
            "active_revisions": [dict(row) for row in revisions],
            "runs": [dict(row) for row in runs],
            "routes": [dict(row) for row in routes],
            "agent_versions": [dict(row) for row in definitions],
            "queued_requests": [dict(row) for row in requests],
        }
