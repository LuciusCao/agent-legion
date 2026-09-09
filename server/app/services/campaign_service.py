"""CampaignService: the campaign row lifecycle (build / preview / read / pause / resume / cancel).

The service layer of the campaign product (#532 / #505, design §3.1): a
campaign row is the durable state a feeder (PR-B) drains in watermark-gated
batches; PR-A ships the row lifecycle without the feeder — a created campaign
sits at ``pending`` and is fully visible over the API.

Creation is fail-fast: the manifest is normalized
(services/campaign_manifest), the items resolve against the workspace's
materials/bundles/connections (resolve_run_items), the dedup keys are probed
(filter_existing_dedup_keys), and the rerun target resolves through the
existing preview judgements — all BEFORE the campaign row is inserted. A
campaign row therefore never exists with a target known to be invalid.

All database access goes through the JobQueries facade (BOUNDARY-DATA-001):
job_db carries CampaignQueriesMixin (rows) plus the run-item probes and the
workspace/revision reads.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.campaigns import CAMPAIGN_TERMINAL_STATUSES
from server.app.services.campaign_knobs import resolve_batch_size, resolve_watermark
from server.app.services.campaign_manifest import (
    ManifestError,
    load_items_text,
    normalize_item,
    serialize_manifest,
)
from server.app.services.campaign_submit_preflight import preflight_submit_intake
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    JobServiceError,
    NotFoundError,
)
from server.app.services.job_rerun.preview import batch_rerun_preview
from server.app.services.job_rerun.upgrade_preview import batch_upgrade_preview
from server.app.services.job_selection_resolver import EmptyJobSelectionError
from server.app.services.run_item_resolution import resolve_run_items
from server.app.settings import Settings
from server.app.storage import ObjectStorage

CAMPAIGN_MODES = ("rerun", "submit", "upgrade")


# Object-store key convention (design §1.3). The FIXED campaign prefix is
# structural GC isolation (PR #541 round-3 P2): s3_jobs_gc scans ``jobs/`` and
# ``jobs-staging/`` and its reference set is job_artifacts ∪ materials only —
# a key under a bare {workspace_id}/ root would fall into that scan face
# whenever the workspace id collides with "jobs"/"jobs-staging" and be deleted
# as an orphan by --apply. The dedicated prefix keeps campaign manifests
# outside both GC scan faces by construction (no GC reference-set change
# needed); the {workspace}/... sub-path still rides the materials bucket's
# per-workspace prefix rule. 0.8.0 unreleased — no legacy keys to migrate.
CAMPAIGN_MANIFEST_KEY_PREFIX = "campaign-manifests"


def campaign_manifest_key(workspace_id: str, campaign_id: str) -> str:
    return f"{CAMPAIGN_MANIFEST_KEY_PREFIX}/{workspace_id}/campaigns/{campaign_id}/manifest.jsonl"


class CampaignStorageUnavailableError(JobServiceError):
    """Object storage not configured (routes map to 503)."""


class CampaignManifestTooLargeError(JobServiceError):
    """Upload/spec exceeds the configured byte ceilings (routes map to 413)."""


class CampaignService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        *,
        run_service: Any | None = None,
        rerun_service: Any | None = None,
        object_storage: ObjectStorage | None = None,
    ) -> None:
        self.job_db = job_db
        self.settings = settings
        self.run_service = run_service
        self.rerun_service = rerun_service
        self.object_storage = object_storage

    # ------------------------------------------------------------------
    # Config accessors
    # ------------------------------------------------------------------

    @property
    def _campaigns_config(self) -> Any:
        return self.settings.executor_runtime.campaigns

    @property
    def manifest_max_bytes(self) -> int:
        """Multipart upload ceiling; routes bound their reads by it (413)."""
        return int(self._campaigns_config.manifest_max_bytes)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_campaign(
        self,
        workspace_id: str,
        mode: str,
        *,
        created_by: str = "",
        # rerun/upgrade target
        job_ids: list[str] | None = None,
        job_filter: Any | None = None,
        node_key: str | None = None,
        from_failed_node: bool = False,
        # submit target
        items: list[dict[str, Any]] | None = None,
        manifest_filename: str | None = None,
        manifest_bytes: bytes | None = None,
        # knobs
        watermark: int | None = None,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        """Normalize + validate + persist a pending campaign row.

        Fail-fast order: mode → target shape → knobs → target resolution
        (rerun resolves its selection; submit resolves items + dedup probe)
        → storage decision (inline vs object store; the bucket branch runs
        an advisory quota precheck AHEAD of the PUT so a full workspace
        leaks no manifest object) → the quota-checked row write
        (create_campaign_guarded: count + row write share one locked
        transaction, so concurrent creates cannot each land a row below a
        stale count — PR #541 P2).
        """
        if mode not in CAMPAIGN_MODES:
            raise InvalidOperationError(
                f"Unsupported campaign mode {mode!r} (supported: {CAMPAIGN_MODES})"
            )
        effective_watermark = self._resolve_watermark(watermark)
        effective_batch_size = self._resolve_batch_size(mode, batch_size)

        if mode == "submit":
            campaign_id = self.job_db.generate_campaign_id()
            target_spec = self._prepare_submit_target(
                workspace_id,
                campaign_id,
                items=items,
                manifest_filename=manifest_filename,
                manifest_bytes=manifest_bytes,
            )
            # Cursor form (design §1.4): submit = manifest line offset.
            progress: dict[str, Any] = {"item_offset": 0}
        else:
            campaign_id = None
            target_spec = self._prepare_rerun_target(
                workspace_id,
                mode,
                job_ids=job_ids,
                job_filter=job_filter,
                node_key=node_key,
                from_failed_node=from_failed_node,
            )
            # Cursor form (design §1.4): explicit ids = list offset; filter =
            # keyset "created_at|id" cursor plus a processed count.
            progress = {"cursor": None, "processed": 0} if job_filter is not None else {"offset": 0}

        return self.job_db.create_campaign_guarded(
            workspace_id,
            mode,
            target_spec,
            watermark=effective_watermark,
            batch_size=effective_batch_size,
            created_by=created_by,
            campaign_id=campaign_id,
            progress=progress,
            max_active=self._campaigns_config.max_active_per_workspace,
        )

    def _resolve_watermark(self, watermark: int | None) -> int:
        """Watermark with the default applied and the >= 1 guard."""
        return resolve_watermark(self._campaigns_config, watermark)

    def _resolve_batch_size(self, mode: str, batch_size: int | None) -> int:
        """batch_size 护栏（PR #541 二轮 P2）：创建与 preview 共用同一判定。
        rerun/upgrade ≤ rerun_max_batch_size；submit ≤ workflows.max_items_per_run。"""
        return resolve_batch_size(
            self._campaigns_config,
            self.settings.executor_runtime.workflows,
            mode,
            batch_size,
        )

    def _prepare_submit_target(
        self,
        workspace_id: str,
        campaign_id: str,
        *,
        items: list[dict[str, Any]] | None,
        manifest_filename: str | None,
        manifest_bytes: bytes | None,
    ) -> dict[str, Any]:
        """Normalize the manifest, validate items, decide inline vs bucket.

        Inline channel: items passed as a JSON array in the request body, or
        an upload whose serialized form fits manifest_inline_max_bytes — the
        spec lands in target_spec_json.items and needs no object store (the
        small-campaign path for instances without S3). Larger uploads are
        serialized to the object store under
        campaign-manifests/{workspace_id}/campaigns/{campaign_id}/manifest.jsonl;
        the spec then carries manifest_storage_key + manifest_item_count only.
        """
        config = self._campaigns_config
        normalized = self._normalize_submit_items(
            items, manifest_bytes, manifest_filename, context="campaign"
        )

        # 入口契约预检（二轮 P1）：与 feeder 的 create_run 同判定，建行前
        # 拒绝已知不可投递的 manifest（见 campaign_submit_preflight）。
        preflight_submit_intake(self.job_db, self.settings, workspace_id, normalized)

        # Fail-fast item validation against the workspace (same resolver the
        # write path uses); a campaign row never exists with unresolvable
        # items. The dedup probe is NOT part of creation validation (already
        # submitted items are skips, not errors — that is the product's
        # idempotent-resume semantics), it belongs to preview.
        resolve_run_items(self.job_db, workspace_id, normalized)

        payload = serialize_manifest(normalized)
        # The size cap applies to both manifest channels (multipart upload
        # checks the same limit before reading the body): a JSON body whose
        # serialized items exceed it must fail-fast rather than silently
        # bypass the declared product boundary into the bucket.
        check_manifest_bytes(payload, config.manifest_max_bytes)
        if len(payload.encode("utf-8")) <= config.manifest_inline_max_bytes:
            return {"items": normalized}
        if self.object_storage is None:
            raise CampaignStorageUnavailableError(
                "Campaign manifest exceeds the inline limit"
                f" ({config.manifest_inline_max_bytes} bytes) and object storage"
                " is not configured on this instance (AGENT_LEGION_S3_BUCKET is"
                " unset)"
            )
        # Quota BEFORE the PUT (round-3 P1): a full workspace used to leave
        # an unreferenced 50 MB object per refused create (no campaign GC
        # face reaps it — s3_jobs_gc only scans jobs/ + jobs-staging/ and
        # knows nothing of campaign references). The check here is advisory:
        # the authoritative count+insert stays in the one-transaction
        # create_campaign_guarded; between this precheck and the guarded
        # write another create may legitimately take the last slot, in which
        # case the guarded 409 wins and the PUT'd object is the residual
        # leak window — one bounded race, not one object per quota-refused
        # request. The campaign id is pre-allocated so the manifest object,
        # validation, and insert complete inside this one fail-fast request.
        self._precheck_active_quota(workspace_id)
        storage_key = campaign_manifest_key(workspace_id, campaign_id)
        self.object_storage.put_object(
            storage_key, payload.encode("utf-8"), content_type="application/x-ndjson"
        )
        return {
            "manifest_storage_key": storage_key,
            "manifest_item_count": len(normalized),
        }

    def _precheck_active_quota(self, workspace_id: str) -> None:
        """Advisory active-campaign cap check ahead of the bucket PUT.

        The authoritative judgement is the guarded write's locked
        count+row-write pair; this precheck only moves the common case
        (workspace already at/above the cap) ahead of the object write so
        repeated over-quota creates stop leaking manifest objects.
        """
        if self.job_db.count_active_campaigns(workspace_id) >= int(
            self._campaigns_config.max_active_per_workspace
        ):
            raise ConflictError(
                "Workspace already has"
                f" {self._campaigns_config.max_active_per_workspace} active"
                " campaigns (pending/running); cancel or complete one first"
            )

    def _normalize_submit_items(
        self,
        items: list[dict[str, Any]] | None,
        manifest_bytes: bytes | None,
        manifest_filename: str | None,
        *,
        context: str,
    ) -> list[dict[str, Any]]:
        """Item normalization shared by the persist and preview paths:
        exactly one channel, size-ceilinged manifests, canonical shapes."""
        config = self._campaigns_config
        if items is not None and manifest_bytes is not None:
            raise InvalidOperationError("Provide either inline items or a manifest file, not both")
        if items is not None:
            if not items:
                raise InvalidOperationError("At least one item is required")
            normalized: list[dict[str, Any]] = []
            for index, raw in enumerate(items, start=1):
                try:
                    # The API contract (RunItem) already enforces the shape;
                    # normalize for storage canonicalization.
                    normalized.append(_normalize_api_item(raw, source=f"items[{index}]"))
                except ManifestError as exc:
                    # 服务层输入（非文件清单）仍按 400 形 InvalidOperationError
                    # （操作员可直接改 body）；与文件通道的 422 语义分开。
                    raise InvalidOperationError(str(exc)) from exc
            return normalized
        if manifest_bytes is not None:
            if len(manifest_bytes) > config.manifest_max_bytes:
                raise CampaignManifestTooLargeError(
                    f"Manifest is {len(manifest_bytes)} bytes, exceeding the"
                    f" {config.manifest_max_bytes} byte limit"
                )
            filename = manifest_filename or "manifest.jsonl"
            try:
                text = manifest_bytes.decode("utf-8-sig")
                # ManifestError 原样穿透（二轮 P2）：文件清单合同错按 422 映射。
                return load_items_text(text, filename=filename)
            except UnicodeDecodeError as exc:
                raise InvalidOperationError(f"{filename}: manifest must be UTF-8 text") from exc
        raise InvalidOperationError(f"submit {context} requires items or a manifest file")

    def _prepare_rerun_target(
        self,
        workspace_id: str,
        mode: str,
        *,
        job_ids: list[str] | None,
        job_filter: Any,
        node_key: str | None,
        from_failed_node: bool,
    ) -> dict[str, Any]:
        """Validate the rerun/upgrade target shape and resolve it once.

        Mirrors JobBatchRerunRequest's validation (node_key and
        from_failed_node are mutually exclusive, exactly one required) and
        the batch endpoints' selection resolution, so an empty/absent
        selection fails here instead of at the feeder.
        """
        if (job_ids is None) == (job_filter is None):
            raise InvalidOperationError("Provide exactly one of job_ids or filter")
        if mode == "rerun":
            if from_failed_node:
                if node_key is not None:
                    raise InvalidOperationError(
                        "node_key must be None when from_failed_node is True"
                    )
            elif not node_key:
                raise InvalidOperationError("node_key is required when from_failed_node is False")
        from server.app.services.job_selection_resolver import resolve_batch_selection

        try:
            resolved = resolve_batch_selection(self.job_db, workspace_id, job_ids, job_filter)
        except EmptyJobSelectionError as exc:
            raise InvalidOperationError(f"Campaign selection is empty: {exc}") from exc
        if not resolved:
            raise InvalidOperationError("Campaign selection resolved to zero jobs")
        # Target shape (design §1.3/§1.4): the filter form stores ONLY the
        # filter — the feeder re-resolves it with a keyset cursor, and a
        # materialized 10^5-id snapshot in the row would blow the row width
        # (exactly what the keyset-cursor design avoids). Explicit ids are
        # the snapshot form by definition (bounded by the request size).
        spec: dict[str, Any] = (
            {"filter": _filter_to_dict(job_filter)}
            if job_filter is not None
            else {"job_ids": sorted(set(resolved))}
        )
        if mode == "rerun":
            if from_failed_node:
                spec["from_failed_node"] = True
            else:
                spec["node_key"] = node_key
        return spec

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview_campaign(
        self,
        workspace_id: str,
        mode: str,
        *,
        job_ids: list[str] | None = None,
        job_filter: Any | None = None,
        node_key: str | None = None,
        from_failed_node: bool = False,
        items: list[dict[str, Any]] | None = None,
        manifest_filename: str | None = None,
        manifest_bytes: bytes | None = None,
        watermark: int | None = None,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        """Dry-run the creation judgements; no row, no write.

        rerun: the SAME batch_rerun_preview the existing preview endpoint
        runs — same function, same numbers, zero drift by construction.
        upgrade: batch_upgrade_preview, the bulk-data equivalent of the
        upgrade write path's eligibility window (not-current against the
        active revision — PR #541 P2: the node_key-shaped rerun preview
        answers 0 for every job of an upgrade selection). submit:
        resolve_run_items + the dedup probe over the whole manifest, the
        same probes the feeder's create_run batch path applies per batch.
        The knob guards (batch_size ceilings) are the creation path's own
        resolvers — a dry-run cannot confirm what creation would refuse.
        """
        if mode not in ("rerun", "upgrade", "submit"):
            raise InvalidOperationError(
                f"Unsupported campaign mode {mode!r} (supported: {CAMPAIGN_MODES})"
            )
        # Same batch_size guard as creation (round-2 P2): a dry-run that
        # accepts a batch_size the create path would refuse confirms a
        # campaign that cannot exist.
        effective_batch_size = self._resolve_batch_size(mode, batch_size)
        if mode == "submit":
            counts = self._preview_submit(
                workspace_id,
                items=items,
                manifest_filename=manifest_filename,
                manifest_bytes=manifest_bytes,
            )
            total = counts["total_items"]
            would_create = counts["would_create"]
            return {
                "mode": "submit",
                "total_items": total,
                "would_create": would_create,
                "would_skip": counts["would_skip"],
                "estimated_batches": _ceil_div(total, effective_batch_size),
                "batch_size": effective_batch_size,
            }
        if self.rerun_service is None:
            raise InvalidOperationError("Rerun service is not wired on this instance")
        if mode == "upgrade":
            counts = batch_upgrade_preview(
                self.rerun_service,
                workspace_id,
                job_ids,
                job_filter=job_filter,
            )
        else:
            counts = batch_rerun_preview(
                self.rerun_service,
                workspace_id,
                job_ids,
                node_key,
                from_failed_node=from_failed_node,
                job_filter=job_filter,
            )
        return {
            "mode": mode,
            "total_count": counts["total_count"],
            "eligible_count": counts["eligible_count"],
            "estimated_batches": _ceil_div(counts["total_count"], effective_batch_size),
            "batch_size": effective_batch_size,
        }

    def _preview_submit(
        self,
        workspace_id: str,
        *,
        items: list[dict[str, Any]] | None,
        manifest_filename: str | None,
        manifest_bytes: bytes | None,
    ) -> dict[str, int]:
        normalized = self._normalize_submit_items(
            items, manifest_bytes, manifest_filename, context="preview"
        )
        # 入口契约与创建同判定（preview 与真实路径共享）：不可投递的 item
        # 不计 would_create，dry-run 直接报创建时的错误。
        preflight_submit_intake(self.job_db, self.settings, workspace_id, normalized)
        candidates = resolve_run_items(self.job_db, workspace_id, normalized)
        # Serialized-bytes ceiling shared with the create path (round-3 P2):
        # a preview that 200s a payload creation would 413 confirms a
        # campaign that cannot exist — the same single-item-huge-params
        # shape passes item-level contracts.
        check_manifest_bytes(
            serialize_manifest(normalized), self._campaigns_config.manifest_max_bytes
        )
        existing = self.job_db.filter_existing_dedup_keys(
            workspace_id,
            ((str(c["entity_type"]), str(c["entity_id"])) for c in candidates),
        )
        seen: set[tuple[str, str]] = set()
        would_create = 0
        for candidate in candidates:
            key = (str(candidate["entity_type"]), str(candidate["entity_id"]))
            if key in existing or key in seen:
                continue
            seen.add(key)
            would_create += 1
        return {
            "total_items": len(normalized),
            "would_create": would_create,
            "would_skip": len(normalized) - would_create,
        }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_campaigns(self, workspace_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.job_db.list_campaigns(workspace_id, limit=limit)

    def get_campaign(self, workspace_id: str, campaign_id: str) -> dict[str, Any]:
        row = self.job_db.get_campaign_in_workspace(workspace_id, campaign_id)
        if row is None:
            raise NotFoundError("Campaign not found")
        return row

    # ------------------------------------------------------------------
    # State transitions (CAS)
    # ------------------------------------------------------------------

    def pause_campaign(self, workspace_id: str, campaign_id: str) -> dict[str, Any]:
        row = self.get_campaign(workspace_id, campaign_id)
        if row["status"] in CAMPAIGN_TERMINAL_STATUSES:
            raise ConflictError(
                f"Campaign is {row['status']} (terminal); pause applies to"
                " pending/running campaigns"
            )
        updated = self.job_db.transition_campaign_status(
            campaign_id, ("pending", "running"), "paused"
        )
        if updated is None:
            raise ConflictError("Campaign state changed concurrently; retry")
        return updated

    def resume_campaign(self, workspace_id: str, campaign_id: str) -> dict[str, Any]:
        row = self.get_campaign(workspace_id, campaign_id)
        if row["status"] in CAMPAIGN_TERMINAL_STATUSES:
            raise ConflictError(
                f"Campaign is {row['status']} (terminal); resume applies to a paused campaign"
            )
        if row["status"] != "paused":
            raise ConflictError(f"Campaign is {row['status']}; resume applies to a paused campaign")
        # Guarded resume (PR #541 P2): a paused row is outside the active
        # count, so freed slots may have been refilled; the count check and
        # the paused→running transition share one transaction under the
        # same workspace lock create takes.
        updated = self.job_db.resume_campaign_guarded(
            workspace_id,
            campaign_id,
            max_active=self._campaigns_config.max_active_per_workspace,
        )
        if updated is None:
            raise NotFoundError("Campaign not found")
        if updated["status"] != "running":
            # The row moved between the read above and the guarded
            # transition (a cancel raced us to a terminal status).
            raise ConflictError("Campaign state changed concurrently; retry")
        return updated

    def cancel_campaign(self, workspace_id: str, campaign_id: str) -> dict[str, Any]:
        row = self.get_campaign(workspace_id, campaign_id)
        if row["status"] in CAMPAIGN_TERMINAL_STATUSES:
            raise ConflictError(f"Campaign is already {row['status']}")
        updated = self.job_db.transition_campaign_status(
            campaign_id, ("pending", "running", "paused"), "cancelled"
        )
        if updated is None:
            raise ConflictError("Campaign state changed concurrently; retry")
        return updated


def _ceil_div(total: int, batch: int) -> int:
    if batch < 1:
        return 0
    return (total + batch - 1) // batch


def check_manifest_bytes(payload: str, manifest_max_bytes: int) -> None:
    """The serialized-manifest byte ceiling, used by both the persist and
    dry-run paths (PR #541 round-3 P2): the write path refuses oversized
    payloads before the inline-vs-bucket decision, and the preview dry-run
    reports the same error instead of confirming a campaign that cannot
    exist."""
    size = len(payload.encode("utf-8"))
    if size > manifest_max_bytes:
        raise CampaignManifestTooLargeError(
            f"Campaign manifest is {size} bytes; the limit is {manifest_max_bytes} bytes"
        )


def _normalize_api_item(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Normalize one API-supplied item (same contract as manifest lines)."""
    return normalize_item(raw, source=source)


def _filter_to_dict(job_filter: Any) -> dict[str, Any]:
    """JobListFilter → the target_spec filter dict (round-trip via to_filter)."""
    return {
        key: getattr(job_filter, key)
        for key in (
            "status",
            "search",
            "workflow_version",
            "workflow_version_none",
            "active_node_key",
            "packed",
            "paused",
        )
    }
