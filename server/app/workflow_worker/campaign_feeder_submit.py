"""CampaignFeeder's submit-mode branch (#532 PR-C, design §2.3).

Split from campaign_feeder.py at its budget ceiling (the sibling-module
ratchet precedent): the PR-B core loop (tick, gates, fairness, CAS, rerun/
upgrade dispatch) stays there; this mixin carries the submit-mode mechanics
— the per-campaign manifest cache (inline spec or the object-store
manifest object), item_offset slicing, the ``create_run(campaign_id=...)``
call, and the all-duplicates absorb that lets a re-fed batch advance the
cursor without creating anything.

The cache is byte-budgeted process-wide (PR-C review P1): the sum of the
cached manifests' serialized bytes stays under
``campaigns.manifest_cache_max_bytes`` by evicting least-recently-used
entries, so many workspaces × watermark-blocked running campaigns cannot
grow the process heap without bound (``max_active_per_workspace`` bounds
only one workspace's share, and a blocked running campaign keeps its
cache). Eviction is cheap by construction: the campaign's next feed
reloads its manifest (inline spec re-read or one object-store GET) from
the row's stored item_offset — exactly the paused-and-resumed shape, so
no progress is lost.

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

from server.app.services.campaign_manifest import (
    ManifestError,
    parse_manifest_text,
    serialize_manifest,
)
from server.app.services.job_errors import AllItemsAlreadyResolvedError, InvalidOperationError
from server.app.workflow_worker.campaign_feeder_types import (
    BatchOutcome,
    copy_progress,
    target_spec,
)

if TYPE_CHECKING:
    from server.app.services.run_service import RunService
    from server.app.settings import Settings
    from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


class CampaignSubmitMixin:
    """The submit-mode manifest machinery, composed into CampaignFeeder."""

    # Attribute contract with the composing CampaignFeeder (declared for the
    # type checker; the feeder owns the instances).
    settings: Settings
    run_service: RunService | None
    object_storage: ObjectStorage | None
    _manifest_cache: dict[str, list[dict[str, Any]]]
    _manifest_cache_bytes: dict[str, int]

    # ------------------------------------------------------------------
    # Byte-budgeted manifest cache (PR-C review P1)
    # ------------------------------------------------------------------

    def _cache_get(self, campaign_id: str) -> list[dict[str, Any]] | None:
        """Cache lookup that marks the entry most recently used.

        Move-to-back on a plain dict (pop + re-insert): the FIRST key is
        then always the least-recently-used — the eviction victim.
        """
        cached = self._manifest_cache.pop(campaign_id, None)
        if cached is None:
            return None
        self._manifest_cache[campaign_id] = cached
        return cached

    def _cache_put(self, campaign_id: str, items: list[dict[str, Any]]) -> None:
        """Cache a loaded manifest and enforce the global byte budget.

        The accounting unit is the canonical serialized form's bytes — the
        exact size of the stored bucket object for the spill channel, and
        the same bound for the inline channel (what it WOULD occupy), so
        the budget measures one uniform thing regardless of channel.
        """
        self._manifest_cache[campaign_id] = items
        self._manifest_cache_bytes[campaign_id] = len(serialize_manifest(items).encode("utf-8"))
        budget = int(self.settings.executor_runtime.campaigns.manifest_cache_max_bytes)
        if budget <= 0:
            return  # 0 = unlimited (the max_items_per_run convention)
        # Evict LRU-first until under budget; at least one entry always
        # stays (len > 1), so a budget below a single manifest's bytes
        # degrades to cache-size-1 (one reload per feed) instead of a
        # load-evict thrash. Reload cost is bounded: one inline re-read or
        # one object-store GET per evicted campaign per feed.
        while len(self._manifest_cache) > 1 and sum(self._manifest_cache_bytes.values()) > budget:
            evicted = next(iter(self._manifest_cache))
            del self._manifest_cache[evicted]
            del self._manifest_cache_bytes[evicted]

    def _load_manifest(self, campaign: dict[str, Any]) -> list[dict[str, Any]]:
        """The campaign's full item list, cached per process under the
        global byte budget (design §2.2; the budget mechanics above).

        Inline channel: the spec's ``items`` array (small campaigns; the
        service decided inline at create time). Bucket channel: the
        normalized jsonl object read via open_stream and parsed with the
        same ``parse_manifest_text`` that round-trips the service's
        serialize_manifest — the stored object is always the canonical
        serialization, so this never sees CSV. Nothing is cached until the
        whole object parses: a transient read failure simply retries next
        tick from scratch.
        """
        campaign_id = str(campaign["id"])
        cached = self._cache_get(campaign_id)
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
            # 有界读（PR #559 二轮）：创建时虽过了 manifest_max_bytes 校验，但
            # 对象可能在 campaign 创建后被覆盖成更大的内容——无界 .read() 会在
            # 任何校验前把整个对象拉进内存（巨大对象无论后续合法与否都能先
            # 耗尽 Host 内存）。最多读 manifest_max_bytes+1 字节（+1 区分「恰好
            # 等于上限」与「超限」）；读毕即关流，存储层异常照旧原样上抛（瞬态
            # 族，下个 tick 从头重试）。
            limit = int(self.settings.executor_runtime.campaigns.manifest_max_bytes)
            try:
                with self.object_storage.open_stream(storage_key) as stream:
                    data = stream.read(limit + 1)
                if len(data) > limit:
                    # 超限 = 对象已不是创建时那个有界对象（被覆盖/换内容），与
                    # 损坏同类：重试无法修复，确定性 failed（修复 = 新建 campaign）。
                    raise ManifestError(f"object exceeds the {limit} byte limit")
                items = parse_manifest_text(data.decode("utf-8"))
            except (ManifestError, UnicodeDecodeError) as exc:
                # Corrupt stored manifest — unparsable text AND non-UTF-8
                # bytes (an overwritten/truncated object fails DECODE
                # before parsing ever runs; PR-C review P2: without this
                # catch the decode error escapes as the transient family
                # and the campaign backs off forever instead of failing).
                # Deterministic: retrying cannot fix a broken object
                # (repair = a fresh campaign, §2.3).
                raise InvalidOperationError(f"Campaign manifest is corrupt: {exc}") from exc
        if not items:
            raise InvalidOperationError("Campaign manifest is empty")
        self._cache_put(campaign_id, items)
        return items

    def _submit_manifest_batch(self, campaign: dict[str, Any]) -> BatchOutcome:
        """Slice the manifest at item_offset and create the run (design §2.3).

        The absorb (see the module docstring) has two shapes, both cursor-
        advancing:
        - no failed run under the deterministic id (the batch already
          succeeded, or a manual run completed the items): the raised
          AllItemsAlreadyResolvedError is caught here, created 0;
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
            # A partial-dedup batch reports the dropped remainder as skipped
            # (PR-C review P2-1): [A, A] → created 1, skipped 1 — the
            # counters sum to the slice the offset advances, instead of the
            # old succeeded=1/skipped=0 with offset +2. The #501 heal return
            # (created_count 0) folds into the same rule: the whole slice
            # counts as skipped — the jobs exist and belong to this run,
            # nothing was created this pass.
            return BatchOutcome(
                ids=[str(campaign["id"])],
                succeeded=created,
                skipped=len(slice_items) - created,
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
