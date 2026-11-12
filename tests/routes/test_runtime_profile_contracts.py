"""Runtime profile model split preserves the generated response schema."""

from __future__ import annotations

import pytest

from server.app.routes.runtime_profile_bucket import ProfileBucket as SplitProfileBucket
from server.app.routes.runtime_profile_contracts import ProfileBucket, RuntimeProfileResponse

pytestmark = pytest.mark.no_db

_BUCKET_FIELDS = (
    "bucket_start",
    "intake_runs",
    "intake_items",
    "pass_count",
    "pass_seconds_total",
    "pass_scan_seconds_max",
    "pass_slow_count",
    "enqueue_submitted",
    "enqueue_pool_skipped",
    "enqueue_pending",
    "enqueue_stock_gated",
    "claim_count",
    "claim_empty_count",
    "claim_seconds_total",
    "claim_seconds_max",
    "claim_queue_wait_seconds_total",
    "claim_queue_wait_seconds_max",
    "claim_scan_seconds_total",
    "claim_scan_seconds_max",
    "claim_evaluate_seconds_total",
    "claim_evaluate_seconds_max",
    "claim_writes_seconds_total",
    "claim_writes_seconds_max",
    "execute_active",
    "execute_done",
    "execute_requeued",
    "result_count",
    "result_seconds_total",
    "result_seconds_max",
    "result_unpack_seconds_total",
    "result_unpack_seconds_max",
    "result_artifacts_verify_seconds_total",
    "result_artifacts_verify_seconds_max",
    "result_validate_seconds_total",
    "result_validate_seconds_max",
    "result_artifacts_upload_seconds_total",
    "result_artifacts_upload_seconds_max",
    "result_lease_write_seconds_total",
    "result_lease_write_seconds_max",
    "result_events_seconds_total",
    "result_events_seconds_max",
    "result_mark_done_seconds_total",
    "result_mark_done_seconds_max",
    "db_pool_waiting",
    "db_pool_wait_seconds_total",
)


def test_profile_bucket_public_model_and_response_schema_are_unchanged() -> None:
    assert ProfileBucket is SplitProfileBucket
    assert tuple(ProfileBucket.model_fields) == _BUCKET_FIELDS

    bucket_schema = RuntimeProfileResponse.model_json_schema()["$defs"]["ProfileBucket"]
    assert tuple(bucket_schema["properties"]) == _BUCKET_FIELDS
    assert tuple(bucket_schema["required"]) == _BUCKET_FIELDS
    assert bucket_schema["properties"]["bucket_start"]["type"] == "string"
    assert bucket_schema["properties"]["result_count"]["type"] == "integer"
    assert bucket_schema["properties"]["result_mark_done_seconds_total"]["type"] == "number"
