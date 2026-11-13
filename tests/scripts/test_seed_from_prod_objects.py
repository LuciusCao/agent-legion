"""Local fakes for seed_from_prod cross-bucket copy and failure semantics."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.seed_from_prod_objects import (
    ObjectHead,
    ObjectSeedError,
    S3CrossBucketStorage,
    S3Settings,
    _batches,
    copy_rows_with_objects,
    expected_dev_resource_names,
    guard_object_copy,
    source_s3_settings,
    target_s3_settings,
)

pytestmark = pytest.mark.no_db

MATERIAL_COLUMNS = ["id", "workspace_id", "size_bytes", "storage_key", "object_etag", "expires_at"]
ARTIFACT_COLUMNS = ["job_id", "node_key", "name", "storage_key", "size_bytes"]


class FakeCrossBucketStorage:
    def __init__(self, source: dict[str, tuple[int, str]]) -> None:
        self.source = source
        self.target: dict[str, tuple[int, str]] = {}
        self.copy_calls: list[str] = []
        self.source_heads: list[str] = []
        self.target_heads: list[str] = []
        self.fail_copies: set[str] = set()
        self.fail_source_heads: set[str] = set()

    def head_source(self, key: str) -> ObjectHead | None:
        self.source_heads.append(key)
        if key in self.fail_source_heads:
            raise RuntimeError("must-not-appear-in-report")
        value = self.source.get(key)
        return ObjectHead(*value) if value is not None else None

    def head_target(self, key: str) -> ObjectHead | None:
        self.target_heads.append(key)
        value = self.target.get(key)
        return ObjectHead(*value) if value is not None else None

    def copy_to_target(self, key: str) -> None:
        self.copy_calls.append(key)
        if key in self.fail_copies:
            raise RuntimeError("must-not-appear-in-report")
        size, _etag = self.source[key]
        self.target[key] = (size, f"dev-etag-{len(self.copy_calls)}")


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def material(mid: str, key: str, size: int = 3, workspace: str = "ws") -> dict[str, str]:
    return {
        "id": mid,
        "workspace_id": workspace,
        "size_bytes": str(size),
        "storage_key": key,
        "object_etag": "prod-etag",
        "expires_at": "\\N",
    }


def artifact(job: str, key: str, size: int = 3) -> dict[str, str]:
    return {
        "job_id": job,
        "node_key": "node",
        "name": "out.json",
        "storage_key": key,
        "size_bytes": str(size),
    }


def copy_materials(
    tmp_path: Path,
    rows: list[dict[str, str]],
    storage: FakeCrossBucketStorage,
    **kwargs: object,
):
    return copy_rows_with_objects(
        "materials",
        write_csv(tmp_path / "materials.csv", MATERIAL_COLUMNS, rows),
        tmp_path / "materials.ready.csv",
        MATERIAL_COLUMNS,
        storage,
        job_workspaces={},
        dry_run=bool(kwargs.get("dry_run", False)),
        overwrite=bool(kwargs.get("overwrite", False)),
        batch_size=int(kwargs.get("batch_size", 100)),
    )


def test_mixed_material_and_artifact_keep_ids_keys_and_rebind_etag(tmp_path: Path):
    material_key = "ws/v2g1/m_m1/input.txt"
    artifact_key = "jobs/ws/job-1/.v/attempt/out.json"
    storage = FakeCrossBucketStorage({material_key: (3, "prod-m"), artifact_key: (3, "prod-a")})

    material_report = copy_materials(tmp_path, [material("m1", material_key)], storage)
    artifact_report = copy_rows_with_objects(
        "job_artifacts",
        write_csv(tmp_path / "artifacts.csv", ARTIFACT_COLUMNS, [artifact("job-1", artifact_key)]),
        tmp_path / "artifacts.ready.csv",
        ARTIFACT_COLUMNS,
        storage,
        job_workspaces={"job-1": "ws"},
        dry_run=False,
        overwrite=False,
    )

    copied_material = read_csv(tmp_path / "materials.ready.csv")
    copied_artifact = read_csv(tmp_path / "artifacts.ready.csv")
    assert copied_material == [{**material("m1", material_key), "object_etag": "dev-etag-1"}]
    assert copied_artifact == [artifact("job-1", artifact_key)]
    assert copied_material[0]["expires_at"] == "\\N"
    assert storage.copy_calls == [material_key, artifact_key]
    assert material_report.rows_ready == artifact_report.rows_ready == 1
    assert material_report.ok and artifact_report.ok


def test_batches_are_bounded_and_cover_tail(tmp_path: Path):
    rows = iter([material(str(index), f"ws/key-{index}") for index in range(5)])
    assert [len(batch) for batch in _batches(rows, 2)] == [2, 2, 1]

    storage = FakeCrossBucketStorage({f"ws/key-{i}": (3, "prod") for i in range(5)})
    report = copy_materials(
        tmp_path, [material(str(i), f"ws/key-{i}") for i in range(5)], storage, batch_size=2
    )
    assert report.rows_ready == 5
    assert len(storage.copy_calls) == 5


def test_repeat_run_head_skips_existing_object(tmp_path: Path):
    key = "ws/v2g1/m_m1/input.txt"
    storage = FakeCrossBucketStorage({key: (3, "prod")})
    first = copy_materials(tmp_path, [material("m1", key)], storage)
    second = copy_materials(tmp_path, [material("m1", key)], storage)

    assert first.copied == 1
    assert second.existing == 1
    assert second.rows_ready == 1
    assert storage.copy_calls == [key]
    assert read_csv(tmp_path / "materials.ready.csv")[0]["object_etag"] == "dev-etag-1"


def test_missing_object_skips_row_and_is_diagnostic(tmp_path: Path):
    report = copy_materials(tmp_path, [material("m1", "ws/missing")], FakeCrossBucketStorage({}))

    assert report.rows_ready == 0
    assert read_csv(tmp_path / "materials.ready.csv") == []
    assert [(f.operation, f.reason) for f in report.failures] == [("head_source", "missing_source")]


def test_copy_failure_keeps_good_subset_and_sanitizes_error(tmp_path: Path):
    good = material("good", "ws/good")
    bad = material("bad", "ws/bad")
    storage = FakeCrossBucketStorage({"ws/good": (3, "p"), "ws/bad": (3, "p")})
    storage.fail_copies.add("ws/bad")

    report = copy_materials(tmp_path, [good, bad], storage)

    assert [row["id"] for row in read_csv(tmp_path / "materials.ready.csv")] == ["good"]
    assert report.rows_ready == 1
    assert report.failures[0].reason == "RuntimeError"
    assert "must-not-appear" not in report.failures[0].reason


def test_source_head_failure_is_not_misreported_as_missing(tmp_path: Path):
    storage = FakeCrossBucketStorage({"ws/key": (3, "p")})
    storage.fail_source_heads.add("ws/key")
    report = copy_materials(tmp_path, [material("m1", "ws/key")], storage)
    assert [(f.operation, f.reason) for f in report.failures] == [("head_source", "RuntimeError")]


def test_source_size_mismatch_skips_row(tmp_path: Path):
    storage = FakeCrossBucketStorage({"ws/key": (9, "p")})
    report = copy_materials(tmp_path, [material("m1", "ws/key", size=3)], storage)
    assert report.failures[0].reason == "source_size_mismatch"
    assert storage.copy_calls == []


def test_target_conflict_requires_explicit_overwrite(tmp_path: Path):
    key = "ws/key"
    storage = FakeCrossBucketStorage({key: (3, "prod")})
    storage.target[key] = (9, "other-dev-data")

    conflict = copy_materials(tmp_path, [material("m1", key)], storage)
    assert conflict.failures[0].reason == "target_size_conflict"
    assert storage.target[key] == (9, "other-dev-data")

    overwritten = copy_materials(tmp_path, [material("m1", key)], storage, overwrite=True)
    assert overwritten.rows_ready == 1
    assert storage.target[key][0] == 3


def test_dry_run_heads_without_copy_or_target_mutation(tmp_path: Path):
    key = "ws/key"
    storage = FakeCrossBucketStorage({key: (3, "prod")})
    report = copy_materials(tmp_path, [material("m1", key)], storage, dry_run=True)

    assert report.planned == report.rows_ready == 1
    assert storage.copy_calls == []
    assert storage.target == {}


def test_artifact_workspace_job_mapping_is_fail_closed(tmp_path: Path):
    row = artifact("job-1", "jobs/other-ws/job-1/out.json")
    report = copy_rows_with_objects(
        "job_artifacts",
        write_csv(tmp_path / "artifacts.csv", ARTIFACT_COLUMNS, [row]),
        tmp_path / "artifacts.ready.csv",
        ARTIFACT_COLUMNS,
        FakeCrossBucketStorage({row["storage_key"]: (3, "p")}),
        job_workspaces={"job-1": "ws"},
        dry_run=False,
        overwrite=False,
    )
    assert report.failures[0].reason == "job_key_mismatch"
    assert report.rows_ready == 0


def settings(bucket: str, endpoint: str = "http://127.0.0.1:8333") -> S3Settings:
    return S3Settings(bucket=bucket, endpoint_url=endpoint)


@pytest.mark.parametrize(
    ("source", "target", "match"),
    [
        ("agent-legion-dev", "agent-legion-develop", "源 bucket"),
        ("agent-legion", "agent-legion", "目标 bucket"),
        ("agent-legion", "agent-legion-other", "目标 bucket"),
    ],
)
def test_bucket_guards_reject_reverse_shared_and_other_worktree(
    source: str, target: str, match: str
):
    with pytest.raises(ObjectSeedError, match=match):
        guard_object_copy(
            settings(source), settings(target), expected_target_bucket="agent-legion-develop"
        )


def test_bucket_guard_requires_same_loopback_endpoint():
    with pytest.raises(ObjectSeedError, match="endpoint 不一致"):
        guard_object_copy(
            settings("agent-legion", "http://127.0.0.1:8333"),
            settings("agent-legion-develop", "http://127.0.0.1:9000"),
            expected_target_bucket="agent-legion-develop",
        )
    with pytest.raises(ObjectSeedError, match="不是本机回环地址"):
        guard_object_copy(
            settings("agent-legion", "https://example.invalid"),
            settings("agent-legion-develop", "https://example.invalid"),
            expected_target_bucket="agent-legion-develop",
        )


def test_expected_dev_resources_match_worktree_and_tower_parent(tmp_path: Path):
    assert expected_dev_resource_names(tmp_path / "Feature.A") == (
        "agent_legion_Feature_A",
        "agent-legion-feature-a",
    )
    tower = tmp_path / "develop" / ".tower" / "worktrees" / "wt-207"
    assert expected_dev_resource_names(tower) == ("agent_legion_develop", "agent-legion-develop")


def test_env_resolution_separates_prod_and_dev_credentials(tmp_path: Path):
    prod_env = tmp_path / "prod.env"
    dev_env = tmp_path / "dev.env"
    prod_env.write_text(
        "AGENT_LEGION_S3_ACCESS_KEY=prod-access\n"
        "AGENT_LEGION_S3_SECRET_KEY=prod-secret\n"
        "AGENT_LEGION_S3_ENDPOINT=http://seaweedfs:8333\n"
    )
    dev_env.write_text(
        "AGENT_LEGION_S3_BUCKET=agent-legion-develop\n"
        "AGENT_LEGION_S3_ENDPOINT=http://127.0.0.1:8333\n"
        "AGENT_LEGION_S3_ACCESS_KEY=dev-access\n"
        "AGENT_LEGION_S3_SECRET_KEY=dev-secret\n"
    )
    target = target_s3_settings({}, dev_env)
    assert target is not None
    source = source_s3_settings(
        {"SEED_PROD_S3_ENDPOINT": "http://127.0.0.1:8333"}, prod_env, target
    )
    assert (source.access_key, source.secret_key) == ("prod-access", "prod-secret")
    assert (target.access_key, target.secret_key) == ("dev-access", "dev-secret")
    assert source.endpoint_url == target.endpoint_url


def test_real_adapter_emits_cross_bucket_copy_source():
    class RawClient:
        def __init__(self):
            self.copy_kwargs = None

        def copy_object(self, **kwargs):
            self.copy_kwargs = kwargs

    source_client = RawClient()
    target_client = RawClient()
    storage = S3CrossBucketStorage(
        settings("agent-legion"),
        settings("agent-legion-develop"),
        source_client=source_client,
        target_client=target_client,
    )
    storage.copy_to_target("jobs/ws/j/out.json")
    assert target_client.copy_kwargs == {
        "Bucket": "agent-legion-develop",
        "CopySource": {"Bucket": "agent-legion", "Key": "jobs/ws/j/out.json"},
        "Key": "jobs/ws/j/out.json",
    }
    assert source_client.copy_kwargs is None
