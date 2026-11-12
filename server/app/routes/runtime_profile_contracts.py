"""Contract models for the runtime-profile response (#359 L1/L2).

One row per minute bucket mirroring ``ops_runtime_profile_samples`` plus
the L2 classifier verdict computed from the newest bucket. Depth/rate
fields are ints; latency fields derive from ``*_seconds_total`` over the
bucket's counts (null when the stage saw no traffic).
"""

from __future__ import annotations

from pydantic import BaseModel

from server.app.routes.runtime_profile_bucket import ProfileBucket


class ProfileVerdict(BaseModel):
    stage: str
    conclusion: str
    evidence: dict[str, object]


class RuntimeProfileResponse(BaseModel):
    buckets: list[ProfileBucket]
    verdict: ProfileVerdict
