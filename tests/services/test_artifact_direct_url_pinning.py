"""#853：外部产物直连 URL 固定到不可变产物版本。

验收：同名产物被重跑覆盖后，此前签发的 presigned URL 返回旧字节或
404/403，绝不返回新字节；raw 端点「当前产物」语义不变。

机制（docs/architecture/artifact-direct-url-pinning.md）：每次写入落一次性
版本 key ``jobs/{ws}/{job}/.v/{version}/{name}``，清单行改指新 key；被取代的
旧 key 在登记提交后经 artifact-authority 锁复核删除。FakeObjectStorage 的
presign_get 按签名目标 key 派生 URL，「URL 返回什么」即「该 key 上的对象」
——对象存储侧的真实行为（SigV4 绑定 key、旧 key 删除即 404）已在 SeaweedFS
4.45 上实测，见设计文档 §3。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from server.app.agent_broker.remote_artifact_promote import promote_all
from server.app.services.external_artifact_access import ExternalArtifactAccessService
from server.app.services.job_artifact_names import is_downloadable_artifact_name
from server.app.services.job_artifact_objects import (
    JobArtifactObjectStore,
    artifact_staging_key,
    artifact_storage_key,
)
from server.app.services.job_artifact_versions import artifact_version_key
from server.app.services.job_artifacts import JobArtifactService
from tests.fakes.storage import FakeObjectStorage

URL_PREFIX = "https://s3.test/download/"
VERSION_KEY = re.compile(r"^jobs/(?P<ws>[^/]+)/(?P<job>[^/]+)/\.v/[0-9a-f]{32}/(?P<name>.+)$")


def _seed_job(job_db, workspace_id: str = "pin-ws") -> dict:
    """生产 intake 形态的 job（带冻结快照，外部清单的声明门读它）。"""
    from tests.helpers import publish_legacy_intake_revision

    job_db.create_workspace(workspace_id, default_workflow_key=workspace_id)
    revision = publish_legacy_intake_revision(job_db, workspace_id)
    batch = job_db.create_run(
        workspace_id, "batch_by_ids", {"question_ids": ["Q1"]}, workspace_id=workspace_id
    )
    job_ids = job_db.create_jobs_bulk(
        candidates=[{"entity_id": "Q1", "entity_type": "question", "title": "Question 1"}],
        workflow_key=workspace_id,
        run_id=batch["id"],
        node_keys=["question_understanding"],
        workspace_id=workspace_id,
        revision=revision,
    )
    return job_db.get_job(job_ids[0])


def _seed_lease(job_db, job: dict, lease_id: str, node_key: str = "question_understanding") -> None:
    with job_db.connect() as conn:
        run_id = conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json, log_path,"
            " run_dir, session_dir, started_at)"
            " values (%s, %s, 'running', '[]', '', '', '', current_timestamp) returning id",
            (job["id"], node_key),
        ).fetchone()["id"]
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id,"
            " job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at,"
            " execution_generation)"
            " values (%s, %s, 'code', %s, %s, %s, %s, 'active', current_timestamp,"
            " current_timestamp, current_timestamp + interval '1 hour', 0)",
            (lease_id, f"exec-{lease_id}", job["workspace_id"], job["id"], node_key, run_id),
        )


def _upload(store, job, tmp_path: Path, payload: bytes, *, name="report.json", **kwargs):
    local = tmp_path / f"{hashlib.sha256(payload).hexdigest()[:8]}-{name}"
    local.write_bytes(payload)
    return store.upload(
        workspace_id=job["workspace_id"],
        job_id=job["id"],
        node_key=kwargs.pop("node_key", "question_understanding"),
        name=name,
        local_path=local,
        **kwargs,
    )


def _url_key(url: str) -> str:
    assert url.startswith(URL_PREFIX)
    return url.removeprefix(URL_PREFIX)


def _entry(listing: dict, name: str) -> dict:
    return next(e for e in listing["artifacts"] if e["name"] == name)


def test_version_segment_can_never_be_a_servable_artifact_name():
    """``.v`` 段是点前缀段：下载白名单拒绝点前缀段，任何可服务的产物名都
    不可能与版本命名空间撞名；版本 key 仍在读侧 job 前缀内。"""
    key = artifact_version_key("ws", "job", "abc", "report.json")
    assert key == "jobs/ws/job/.v/abc/report.json"
    assert key.startswith(artifact_storage_key("ws", "job", ""))
    assert not is_downloadable_artifact_name(".v")
    assert not is_downloadable_artifact_name(".v/abc/report.json")


def test_rerun_overwrite_never_reaches_a_previously_signed_url(job_db, settings, tmp_path):
    """验收主案：签发 → 同名重写 → 旧 URL 的签名目标不再承载任何字节
    （对象存储答 404），绝不是新字节；新清单签发新 URL 指向新字节；raw
    端点仍答「当前」产物。修复前两次写入同一个固定 key，旧 URL 返回新字节
    （本测试 ``old_key not in objects`` 与 key 不等断言变红）。"""
    job = _seed_job(job_db)
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(job_db, storage)
    access = ExternalArtifactAccessService(
        job_db, settings, object_store=store, artifact_service=JobArtifactService(job_db, store)
    )
    _upload(store, job, tmp_path, b'{"run": 1}')
    old_url = _entry(access.list_artifacts(job["workspace_id"], job["id"]), "report.json")[
        "download_url"
    ]
    old_key = _url_key(old_url)
    assert VERSION_KEY.match(old_key)
    assert storage.objects[old_key] == b'{"run": 1}'

    _upload(store, job, tmp_path, b'{"run": 2}')  # 重跑覆盖同名产物

    assert old_key not in storage.objects  # 旧 URL → 404，绝不是新字节
    assert b'{"run": 2}' not in {storage.objects.get(old_key)}
    entry = _entry(access.list_artifacts(job["workspace_id"], job["id"]), "report.json")
    new_key = _url_key(entry["download_url"])
    assert new_key != old_key
    assert storage.objects[new_key] == b'{"run": 2}'
    assert entry["content_hash"] == hashlib.sha256(b'{"run": 2}').hexdigest()
    # raw 端点「当前产物」语义不变。
    raw = access.open_raw_current(job["workspace_id"], job["id"], "report.json")
    assert raw.stream is not None
    assert raw.stream.read() == b'{"run": 2}'


def test_superseded_cleanup_failure_leaves_only_old_bytes(job_db, settings, tmp_path):
    """被取代对象删除失败（存储故障）不让已提交的上传失败；残留对象仍只
    承载旧字节——旧 URL 返回旧字节，依然满足验收（孤儿交 GC）。"""
    job = _seed_job(job_db)
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(job_db, storage)
    _upload(store, job, tmp_path, b"old")
    old_key = str(store.lookup(job["id"], "report.json")["storage_key"])
    storage.fail_deletes = True

    assert _upload(store, job, tmp_path, b"new") is not None

    assert storage.objects[old_key] == b"old"
    assert storage.objects[str(store.lookup(job["id"], "report.json")["storage_key"])] == b"new"


def test_lease_arm_lands_on_fresh_version_keys(job_db, settings, tmp_path):
    """lease 臂（D12 镜像上传）同样每次落新版本 key、从不覆盖：共享 promote
    primitive 的备份臂找不到既有对象（无 .rollback 备份产生），旧版本在登记
    提交后删除，staging 不留残留。"""
    job = _seed_job(job_db)
    _seed_lease(job_db, job, "pin-lease")
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(job_db, storage)

    first = _upload(store, job, tmp_path, b"first", lease_id="pin-lease")
    second = _upload(store, job, tmp_path, b"second", lease_id="pin-lease")

    assert first is not None and second is not None
    assert first["storage_key"] != second["storage_key"]
    assert VERSION_KEY.match(str(second["storage_key"]))
    assert storage.objects == {str(second["storage_key"]): b"second"}
    assert not [key for key in storage.deleted if "/.rollback/" in key]


def test_worker_promote_lands_on_version_keys_and_retires_previous(job_db, settings, tmp_path):
    """Worker 回传 promote：authority 落版本 key（``.gz`` 形态标记保留在
    key 末尾，#338），同名重 promote 改指新 key 并删除被取代对象；staging
    源照旧不在 promote 内删除（#774）。"""
    job = _seed_job(job_db)
    _seed_lease(job_db, job, "pin-remote")
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(job_db, storage)
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    def _promote(execution_id: str, payload: bytes, suffix: str) -> str:
        staging = artifact_staging_key(job["workspace_id"], job["id"], execution_id, "out.json")
        storage.objects[staging + suffix] = payload
        ref = {"storage_key": staging + suffix, "size_bytes": len(payload)}
        assert promote_all(
            store,
            job["workspace_id"],
            job["id"],
            "question_understanding",
            job_dir,
            {"out.json": ref},
            {},
            {"out.json": hashlib.sha256(payload).hexdigest()},
            execution_id,
            "pin-remote",
        )
        return str(store.lookup(job["id"], "out.json")["storage_key"])

    first = _promote("exec-1", b"bare-bytes", "")
    second = _promote("exec-2", b"gz-bytes", ".gz")

    assert VERSION_KEY.match(first) and not first.endswith(".gz")
    assert VERSION_KEY.match(second) and second.endswith("/out.json.gz")
    assert first not in storage.objects
    assert storage.objects[second] == b"gz-bytes"


def test_legacy_fixed_key_row_is_retired_not_overwritten(job_db, settings, tmp_path):
    """存量（#853 前）行指向固定 key：新写入落版本 key、从不回写固定 key，
    固定 key 作为被取代对象删除——此前对固定 key 签发的 URL 只会 404。"""
    job = _seed_job(job_db)
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(job_db, storage)
    legacy_key = artifact_storage_key(job["workspace_id"], job["id"], "report.json")
    storage.objects[legacy_key] = b"legacy"
    store.record_remote(
        workspace_id=job["workspace_id"],
        job_id=job["id"],
        node_key="question_understanding",
        name="report.json",
        storage_key=legacy_key,
        size_bytes=6,
        content_hash=hashlib.sha256(b"legacy").hexdigest(),
    )

    _upload(store, job, tmp_path, b"fresh")

    assert legacy_key not in storage.objects
    assert VERSION_KEY.match(str(store.lookup(job["id"], "report.json")["storage_key"]))


def test_legacy_key_shared_by_another_node_row_is_kept(job_db, settings, tmp_path):
    """#853 前跨节点同名产物共用一个固定 key：一个节点改指版本 key 后，仍被
    另一节点清单行引用的固定 key 不删（锁内清单复核），也不再被任何写入覆盖。"""
    job = _seed_job(job_db)
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(job_db, storage)
    legacy_key = artifact_storage_key(job["workspace_id"], job["id"], "shared.json")
    storage.objects[legacy_key] = b"shared"
    for node_key in ("question_understanding", "other_node"):
        store.record_remote(
            workspace_id=job["workspace_id"],
            job_id=job["id"],
            node_key=node_key,
            name="shared.json",
            storage_key=legacy_key,
            size_bytes=6,
            content_hash=hashlib.sha256(b"shared").hexdigest(),
        )

    _upload(store, job, tmp_path, b"fresh", name="shared.json")

    assert storage.objects[legacy_key] == b"shared"
    other = store.row_for_node(job["id"], "other_node", "shared.json")
    assert other is not None and other["storage_key"] == legacy_key
