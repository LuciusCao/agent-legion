"""Claim response assembly for the Agent Worker claim route (issue #546).

Split from ``agent_worker_claims.py`` for the file budget: the single claim
and the batch claim (``limit`` > 1) share the per-claim manifest injection —
code claims resolve secrets + the runtime context on the response path, agent
claims get the object-storage artifact block — and the ``AgentClaimResponse``
build. The single-claim path keeps its exact pre-#546 failure semantics
(a code-manifest resolution failure answers 500 after the committed claim);
the batch path downgrades that to dropping the one bad item so N-1 siblings
still cross the wire.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Response

from server.app.agent_broker.artifact_object_block import inject_artifact_object_block
from server.app.agent_broker.claim_scan import AgentClaim
from server.app.agent_broker.code_manifest import resolve_code_runtime_context
from server.app.agent_broker.code_manifest_config import resolve_code_manifest_config
from server.app.routes.agent_worker_claim_contracts import (
    AgentClaimResponse,
    BatchAgentClaimResponse,
)

logger = logging.getLogger(__name__)


def build_claim_response(
    broker: Any,
    settings: Any,
    job_artifact_objects: Any,
    worker: dict[str, Any],
    claimed: AgentClaim,
) -> AgentClaimResponse:
    """Assemble one claim response, injecting the response-path-only blocks."""
    manifest = claimed.manifest
    if claimed.kind == "code":
        # Secret injection happens on the response path only: the queued
        # manifest keeps vault references, the resolved plaintext crosses
        # the HTTPS channel and is never persisted (VAULT-SECRET-001).
        # Issue #142: the queued manifest persists only the lightweight
        # runtime_context audit stub — rebuild the full DB-derived
        # payloads here, in memory, never persisted.
        try:
            manifest = resolve_code_manifest_config(manifest, broker.database_dsn, settings.config)
            manifest = resolve_code_runtime_context(
                manifest,
                broker.database_dsn,
                settings.config,
                job_artifact_objects,
                worker_protocol_version=int(worker["protocol_version"]),
            )
        except Exception as exc:
            # #204 broad-except audit: claim-time manifest resolution that
            # CONVERTS to a retryable 500, never silently swallows. The
            # outcome space is deliberately wide: secret resolution
            # (VaultError families), connection-token injection, the DB
            # re-fetches in resolve_code_runtime_context (its own
            # documented strict reads), and material/bundle claim blocks —
            # none is a business family the response layer could
            # enumerate, and any of them means "this Worker cannot run
            # this execution with a well-formed manifest". Raising 500
            # after the committed claim is the pinned recovery loop: the
            # Worker drops the attempt, the lease expires, the sweeper
            # requeues. logger.exception keeps the traceback for the
            # operator; HTTPException carries a non-leaking detail.
            # The claim already committed; a 500 lets the Worker drop the
            # attempt and the sweeper requeues after the lease expires.
            logger.exception("code manifest resolution failed for %s", claimed.execution_id)
            raise HTTPException(status_code=500, detail="code manifest resolution failed") from exc
    else:
        # #160 D12: agent manifests persist only CAS refs (dispatch never
        # embeds URLs); the object-storage artifact channel (presigned
        # PUT for outputs, presigned GET for staged inputs) is injected
        # here, on the per-claim freshly deserialized manifest — memory
        # only, so URLs never persist and never expire in the queue. A
        # storage error degrades to the legacy CAS channel inside the
        # helper; the claim never fails over injection.
        inject_artifact_object_block(
            job_artifact_objects,
            manifest,
            worker_protocol_version=int(worker["protocol_version"]),
        )
    return AgentClaimResponse(
        execution_id=claimed.execution_id,
        lease_id=claimed.lease_id,
        workspace_id=claimed.workspace_id,
        job_id=claimed.job_id,
        # #211 M2: the column is gone — the deprecated response field
        # keeps returning the identity value until the M3 contract drop.
        workflow_key=claimed.workspace_id,
        node_key=claimed.node_key,
        agent_id=claimed.agent_id,
        kind=claimed.kind,
        manifest=manifest,
        bundle_url=f"/api/agent-executions/{claimed.execution_id}/bundle",
    )


def build_batch_claim_response(
    broker: Any,
    settings: Any,
    job_artifact_objects: Any,
    worker: dict[str, Any],
    claims: list[AgentClaim],
) -> Response | BatchAgentClaimResponse:
    """Assemble the batch answer (#546); an empty batch stays the 204.

    Per-item injection failures drop ONLY that item: the claim already
    committed, and the lease-expiry sweeper requeues it — the same recovery
    the single path's 500 relies on, without sacrificing the N-1 well-formed
    siblings of the batch.
    """
    responses: list[AgentClaimResponse] = []
    for claimed in claims:
        try:
            responses.append(
                build_claim_response(broker, settings, job_artifact_objects, worker, claimed)
            )
        except Exception:
            # #204 broad-except audit: per-item containment of the batch
            # analogue of the single path's 500. The outcome space is
            # exactly build_claim_response's documented one (code manifest
            # resolution failures, surfaced as its HTTPException(500), plus
            # any programming slip in response assembly); the swallowed
            # item is a COMMITTED claim whose recovery is the lease-expiry
            # sweeper, identical to the single path's post-commit 500.
            # Dropping must not fail the batch — the remaining claims are
            # committed too and only a delivered response lets the Worker
            # run them. logger.exception preserves the traceback.
            logger.exception("batch claim item dropped after commit for %s", claimed.execution_id)
    if not responses:
        return Response(status_code=204)
    return BatchAgentClaimResponse(claims=responses)
