from __future__ import annotations

from typing import TypedDict


class JobPackageItemResult(TypedDict):
    job_id: str
    status: str
    reason_code: str | None
    message: str | None


class JobPackageResult(TypedDict):
    results: list[JobPackageItemResult]
    succeeded_count: int
    failed_count: int
    package_filename: str | None
    download_url: str | None
