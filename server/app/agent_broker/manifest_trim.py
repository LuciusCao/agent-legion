"""Terminal-state manifest trim fragments (issues #142 code / #354 agent).

Every terminal transition of an ``agent_execution_requests`` row — broker
``mark_done``, ``cancel_request``, the requeue-limit-exceeded and
zombie-claim sweeps, the agent-disabled / unclaimable sweeps, and the
rerun-path queued cancellation — applies ``MANIFEST_TRIM`` to
``manifest_json`` in the same UPDATE, so no heavy payload survives the
execution it belonged to:

- kind='code' (#142): the heavy ``runtime_context`` collapses back to the
  lightweight audit stub (legacy rows enqueued before the fix still carry
  the full ~1.7MB context);
- kind='agent' (#354): the whole 2-10KB manifest (prompt, config, inputs,
  command_spec, input_artifact refs) is replaced by an identity-skeleton
  stub. The full evidence remains available from the immutable sources the
  manifest was derived from — the job's pinned ``workflow_revisions`` row,
  the versioned_entities Agent definition, and ``node_runs`` /
  ``node_run_token_usage`` / events.jsonl. Post-trim readers verified:
  quality sampling reads only ``capability`` (quality_sampling.py), the
  bundle reaper reads only ``execution_id``, and terminal manifests are
  never re-claimed.

Both branches are idempotent: a stub passes through unchanged. The SQL
lives here (not in code_manifest.py) for the file-size budget; the stub
builders for the enqueue side stay in their per-kind modules.
"""

from __future__ import annotations

from typing import Any

# The kind='code' trim body — replace the heavy runtime_context with the
# lightweight audit stub. The qualified table name keeps the correlated jobs
# lookup unambiguous inside each UPDATE.
_CODE_TRIM_BODY = """
  jsonb_set(
    manifest_json::jsonb,
    '{runtime_context}',
    jsonb_build_object(
      'job_id', agent_execution_requests.job_id,
      'workspace_id', agent_execution_requests.workspace_id,
      'batch_id', coalesce(
        nullif(manifest_json::jsonb #>> '{runtime_context,batch_id}', ''),
        nullif(manifest_json::jsonb #>> '{runtime_context,job,batch_id}', ''),
        nullif(manifest_json::jsonb #>> '{runtime_context,job,run_id}', ''),
        (select nullif(j.run_id, '') from jobs j where j.id = agent_execution_requests.job_id)
      ),
      'batch_hash', nullif(manifest_json::jsonb #>> '{runtime_context,batch_hash}', '')
    )
  )::text
"""

# The kind='agent' trim body — replace the whole manifest with the audit
# stub. The stub keeps the identity skeleton (execution/job/workspace/node/
# agent ids, capability, log_path) plus the skill pins and marks itself
# ``trimmed``; the structured ``inputs`` list, the rendered prompt/
# command_spec, config and input_artifact sha256 refs are the heavy halves
# and are dropped (the bundle they refer to is reaped by reaper.py on the
# same terminal transition, so the refs outlive their target anyway).
_AGENT_TRIM_BODY = """
  jsonb_build_object(
    'execution_id', agent_execution_requests.execution_id,
    'job_id', agent_execution_requests.job_id,
    'workspace_id', agent_execution_requests.workspace_id,
    'node_key', agent_execution_requests.node_key,
    'agent_id', agent_execution_requests.agent_id,
    'capability', manifest_json::jsonb ->> 'capability',
    'runtime', manifest_json::jsonb ->> 'runtime',
    'log_path', manifest_json::jsonb ->> 'log_path',
    'skill', manifest_json::jsonb ->> 'skill',
    'skill_version', manifest_json::jsonb ->> 'skill_version',
    'skill_commit', manifest_json::jsonb ->> 'skill_commit',
    'agent_version', manifest_json::jsonb ->> 'agent_version',
    'trimmed', true
  )::text
"""

# The kind-dispatching trim every terminal transition applies to
# ``manifest_json``: code rows keep their runtime_context stub logic, agent
# rows are replaced wholesale by the audit stub. ``kind='agent'`` is the
# column default, so rows without an explicit kind (legacy) take the agent
# branch too.
MANIFEST_TRIM = (
    "case when kind = 'code' then\n" + _CODE_TRIM_BODY + "else\n" + _AGENT_TRIM_BODY + "end\n"
)


def cancel_request(conn: Any, execution_id: str) -> None:
    """Cancel a queued request (terminal transition + manifest trim).

    Moved from claim_evaluate (#389 budget): cancel IS a terminal transition,
    so the trim SQL lives beside it."""
    conn.execute(
        "update agent_execution_requests set state='cancelled',"
        " finished_at=current_timestamp, manifest_json=" + MANIFEST_TRIM + " where execution_id=%s",
        (execution_id,),
    )
