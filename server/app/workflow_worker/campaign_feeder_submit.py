"""CampaignFeeder's submit-mode branch (#532 PR-C, design §2.3).

Split from campaign_feeder.py at its budget ceiling (the sibling-module
ratchet precedent): the PR-B core loop (tick, gates, fairness, CAS, rerun/
upgrade dispatch) stays there; this mixin carries the submit-mode mechanics
— the per-campaign manifest cache (inline spec or the object-store
manifest object), item_offset slicing, the ``create_run(campaign_id=...)``
call, and the all-duplicates absorb that lets a re-fed batch advance the
cursor without creating anything.

The absorb contract (design §1.5, PR-C review P2): the ONLY exception the
feeder absorbs is ``AllItemsAlreadyResolvedError`` — the dedicated subclass
``run_service`` raises when the dedup filter emptied an otherwise-valid
batch. Catching the broad ``InvalidOperationError`` family instead would
also swallow state-drift failures (material expired by TTL mid-campaign,
connection disabled, active revision vanished) that must fail the campaign
rather than complete it as silent skips; the #531 CLI's message-string
hack is resolved in-process by the subclass, not by widening the catch.
``PartialRunCreationError`` (real partial failure) stays outside the
absorb and re-raises into the deterministic-failed path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.services.campaign_manifest import ManifestError, parse_manifest_text
from server.app.services.job_errors import AllItemsAlreadyResolvedError, InvalidOperationError
from server.app.workflow_worker.campaign_feeder_types import (
    BatchOutcome,
    copy_progress,
    target_spec,
)

if TYPE_CHECKING:
    from server.app.services.run_service import RunService
    from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


class CampaignSubmitMixin:
    """The submit-mode manifest machinery, composed into CampaignFeeder."""

    # Attribute contract with the composing CampaignFeeder (declared for the
    # type checker; the feeder owns the instances).
    run_service: RunService | None
    object_storage: ObjectStorage | None
    _manifest_cache: dict[str, list[dict[str, Any]]]

    def _load_manifest(self, campaign: dict[str, Any]) -> list[dict[str, Any]]:
        """The campaign's full item list, cached per process (design §2.2).

        Inline channel: the spec's ``items`` array (small campaigns; the
        service decided inline at create time). Bucket channel: the
        normalized jsonl object read ONCE via open_stream and parsed with
        the same ``parse_manifest_text`` that round-trips the service's
        serialize_manifest — the stored object is always the canonical
        serialization, so this never sees CSV. Nothing is cached until the
        whole object parses: a transient read failure simply retries next
        tick from scratch.
        """
        campaign_id = str(campaign["id"])
        cached = self._manifest_cache.get(campaign_id)
        if cached is not None:
            return list(cached)
        spec = target_spec(campaign)
        if "items" in spec:
            raw = spec.get("items")
            if not isinstance(raw, list):
                raise InvalidOperationError(
                    "Campaign inline manifest is corrupt (expected an items array)"
                )
            items: list[dict[str, Any]] = [dict(item) for item in raw if isinstance(item, dict)]
        else:
            storage_key = str(spec.get("manifest_storage_key") or "")
            if not storage_key or self.object_storage is None:
                raise InvalidOperationError(
                    "Campaign target spec is corrupt (no inline items and no"
                    " readable manifest storage key)"
                )
            try:
                # A manifest is bounded at create time (manifest_max_bytes,
                # currently 50MB — the multipart ceiling applies to the
                # serialized form stored here), so reading the stream whole
                # is bounded by construction, not an unbounded read.
                text = self.object_storage.open_stream(storage_key).read().decode("utf-8")
                items = parse_manifest_text(text)
            except ManifestError as exc:
                # Corrupt stored manifest: deterministic — retrying cannot
                # fix a broken object (repair = a fresh campaign, §2.3).
                raise InvalidOperationError(f"Campaign manifest is corrupt: {exc}") from exc
        if not items:
            raise InvalidOperationError("Campaign manifest is empty")
        self._manifest_cache[campaign_id] = items
        return items

    def _submit_manifest_batch(self, campaign: dict[str, Any]) -> BatchOutcome:
        """Slice the manifest at item_offset and create the run (design §2.3).

        The absorb (see the module docstring) has two shapes, both cursor-
        advancing:
        - no failed run under the deterministic id (the batch already
          succeeded, or a manual run completed the items): the raised
          InvalidOperationError is caught here, created 0;
        - #501 healing (the batch previously failed partway and its jobs
          were completed by a retry elsewhere): create_run itself returns
          created_count 0.

        Either way the batch is DONE from the campaign's perspective (its
        jobs exist), and re-slicing it later can never create anything new
        — the dedup key space is shared with the manual runs that completed
        it. PartialRunCreationError subclasses InvalidOperationError but is
        a genuine per-item failure (created_so_far in the detail) and is
        NOT absorbed.
        """
        items = self._load_manifest(campaign)
        progress = copy_progress(campaign)
        offset = int(progress.get("item_offset") or 0)
        batch_size = int(campaign["batch_size"])
        slice_items = items[offset : offset + batch_size]
        exhausted = offset + len(slice_items) >= len(items)
        if not slice_items:
            # Past the end (post-completion crash window): nothing to
            # create; finishing without a phantom batch is the caller's
            # contract for an empty exhausted slice.
            return BatchOutcome([], 0, 0, 0, exhausted, None)
        workspace_id = str(campaign["workspace_id"])
        if self.run_service is None:
            # The optional-constructor seam (tests assemble minimal feeders
            # for rerun/upgrade); a submit campaign on such a feeder is a
            # wiring bug, deterministic by nature.
            raise InvalidOperationError("Submit campaign found on a feeder without a run service")
        try:
            result = self.run_service.create_run(
                workspace_id,
                workflow_key=workspace_id,
                items=slice_items,
                campaign_id=str(campaign["id"]),
                created_by=str(campaign.get("created_by") or ""),
            )
            created = int(result.get("created_count") or 0)
            # The run created everything fresh (created == slice) or healed
            # (created 0, healed run) — both mean the batch is done; the
            # healed shape reports nothing as skipped (the jobs exist and
            # belong to this run).
            return BatchOutcome(
                ids=[str(campaign["id"])],
                succeeded=created,
                skipped=0,
                failed=0,
                exhausted=exhausted,
                next_cursor=None,
            )
        except AllItemsAlreadyResolvedError as exc:
            # The dedicated all-duplicates signal (PR-C review P2): catching
            # only this subclass keeps the state-drift InvalidOperationError
            # family (expired materials, disabled connections, vanished
            # revision) on the deterministic-failed path instead of being
            # absorbed as silent skips. PartialRunCreationError — a sibling
            # subclass, not a child of AllItemsAlreadyResolvedError — never
            # reaches this handler and re-raises naturally.
            logger.info(
                "campaign feeder: batch of campaign %s fully absorbed as duplicates (%s)",
                campaign["id"],
                exc,
            )
            return BatchOutcome(
                ids=[str(campaign["id"])],
                succeeded=0,
                skipped=len(slice_items),
                failed=0,
                exhausted=exhausted,
                next_cursor=None,
            )
