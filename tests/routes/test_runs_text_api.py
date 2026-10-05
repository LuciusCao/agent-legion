"""Runs API × text 条目：入口契约、落成材料、dedup、形状校验、存储未配置。"""

from __future__ import annotations

import copy
import hashlib

import pytest

from tests.fakes.storage import FakeObjectStorage

WORKFLOW_KEY = "education_video_problems_generation"

FakeStorage = FakeObjectStorage

REQUIREMENT = "# 歌曲创作需求\n- 参考歌曲：Anti-Hero\n- 新歌语言：中文\n"


@pytest.fixture
def storage(client, monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def _create_workspace(client) -> str:
    response = client.post(
        "/api/workspaces",
        json={"id": WORKFLOW_KEY, "name": "runs-text-ws"},
    )
    assert response.status_code == 200, response.text
    from tests.helpers import publish_builtin_revision

    publish_builtin_revision(client.app.state.job_db, WORKFLOW_KEY)
    return response.json()["workspace"]["id"]


def _accept_text_items(job_db, workspace_id: str) -> None:
    """Republish the demo workflow declaring ``[material, text]``."""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "text"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )


def _insert_ready_material(job_db, workspace_id: str, material_id: str) -> None:
    """A ready upload row (doc.txt) for mixing stored items with text items."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, 'ready', 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                f"{workspace_id}/hash-{material_id}/doc.txt",
            ),
        )


def _create_run(client, workspace_id: str, items: list[dict]):
    return client.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"workflow_key": WORKFLOW_KEY, "items": items},
    )


def _materials(client, workspace_id: str) -> list[dict]:
    return client.get(f"/api/workspaces/{workspace_id}/materials").json()["materials"]


def _storage_keys(job_db, workspace_id: str) -> set[str]:
    with job_db.read() as conn:
        rows = conn.execute(
            "select storage_key from materials where workspace_id=%s", (workspace_id,)
        ).fetchall()
    return {row["storage_key"] for row in rows}


def test_text_item_rejected_by_default_contract(client, storage, job_db) -> None:
    """Seeded demo revision accepts materials only: text is opt-in, and the
    rejection happens before the material is written (no row, no object)."""
    workspace_id = _create_workspace(client)

    response = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])

    assert response.status_code == 400
    assert "not accepted by this workflow" in response.json()["detail"]
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []
    assert _materials(client, workspace_id) == []
    assert storage.objects == {}


def test_text_item_becomes_ready_material_and_job(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)

    response = _create_run(
        client,
        workspace_id,
        [{"type": "text", "content": REQUIREMENT, "filename": "创作需求.md"}],
    )

    assert response.status_code == 200, response.text
    assert response.json()["created_count"] == 1
    digest = hashlib.sha256(REQUIREMENT.encode("utf-8")).hexdigest()
    # Object first, row second: the ready row points at the stored bytes.
    (material,) = _materials(client, workspace_id)
    (key,) = _storage_keys(job_db, workspace_id)
    assert storage.objects[key] == REQUIREMENT.encode("utf-8")
    assert material["status"] == "ready"
    assert material["filename"] == "创作需求.md"
    assert material["content_hash"] == digest
    assert material["content_type"] == "text/markdown; charset=utf-8"
    assert material["size_bytes"] == len(REQUIREMENT.encode("utf-8"))
    # Downstream sees an ordinary material job (input_json / source columns).
    (job,) = client.get(f"/api/workspaces/{workspace_id}/jobs").json()["jobs"]
    assert job["source_type"] == "material"
    assert job["source_id"] == material["id"]
    assert job["title"] == "创作需求.md"
    with job_db.connect() as conn:
        row = conn.execute("select input_json from jobs where id=%s", (job["id"],)).fetchone()
    assert '"type": "material"' in str(row["input_json"])
    assert material["id"] in str(row["input_json"])


def test_text_item_default_filename_and_dedup(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)

    first = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])
    assert first.status_code == 200, first.text
    (material,) = _materials(client, workspace_id)
    assert material["filename"] == "需求.md"

    # Identical text → same content-addressed material → same job dedup key.
    second = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])
    assert second.status_code == 400
    assert "No tasks were resolved" in second.json()["detail"]
    assert len(_materials(client, workspace_id)) == 1
    # Different text is a new material and a new job.
    third = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT + "x"}])
    assert third.status_code == 200, third.text
    assert len(_materials(client, workspace_id)) == 2


@pytest.mark.parametrize("status", ["uploading", "failed", "expired"])
@pytest.mark.parametrize("old", [False, True])
def test_text_item_never_takes_over_upload_rows(client, storage, job_db, status, old) -> None:
    """Neither age nor non-ready status proves the last presign has expired."""
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    digest = hashlib.sha256(REQUIREMENT.encode("utf-8")).hexdigest()
    presign = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={
            "filename": "old.txt",
            "size_bytes": len(REQUIREMENT.encode("utf-8")),
            "content_type": "text/plain",
            "content_hash": digest,
        },
    )
    assert presign.status_code == 200, presign.text
    material_id = presign.json()["material"]["id"]
    with job_db.write() as conn:
        conn.execute("update materials set status=%s where id=%s", (status, material_id))
        if old:
            conn.execute(
                "update materials set created_at=now()-interval '7 days' where id=%s",
                (material_id,),
            )
    before = _materials(client, workspace_id)
    (key,) = _storage_keys(job_db, workspace_id)

    response = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])

    assert response.status_code == 409, response.text
    assert _materials(client, workspace_id) == before
    assert storage.put_calls == 0
    assert _storage_keys(job_db, workspace_id) == {key}
    if status != "expired":
        # A delayed browser PUT still targets its original row and must be verified.
        storage.put_object(key, b"x" * len(REQUIREMENT.encode("utf-8")))
        complete = client.post(f"/api/workspaces/{workspace_id}/materials/{material_id}/complete")
        assert complete.status_code == 422
        storage.put_object(key, REQUIREMENT.encode("utf-8"))
        complete = client.post(f"/api/workspaces/{workspace_id}/materials/{material_id}/complete")
        assert complete.status_code == 200
        retry = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])
        assert retry.status_code == 200, retry.text
        assert set(storage.objects) == {key}


@pytest.mark.parametrize(
    ("item", "detail"),
    [
        ({"type": "text", "content": "   \n"}, "non-empty content"),
        ({"type": "text", "content": "x", "filename": "../需求.md"}, "invalid"),
        ({"type": "text", "content": "x", "filename": "需求.exe"}, ".md, .txt or .json"),
        # 30k CJK chars pass the contract's character cap but exceed 64 KiB of UTF-8.
        ({"type": "text", "content": "需" * 30000}, "exceeds"),
        ({"type": "text", "content": "x", "filename": "bad\x00.md"}, "invalid"),
    ],
)
def test_text_item_shape_errors_write_nothing(client, storage, job_db, item, detail) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)

    response = _create_run(client, workspace_id, [item])

    assert response.status_code == 400, response.text
    assert detail in response.json()["detail"]
    assert _materials(client, workspace_id) == []
    assert storage.objects == {}


@pytest.mark.parametrize("failure", ["put", "database"])
def test_text_batch_failure_rolls_back_every_material(
    client, storage, job_db, monkeypatch, failure
) -> None:
    from server.app.db.connection import DatabaseConnection
    from server.app.services.run_text_items import materialize_text_items

    workspace_id = _create_workspace(client)
    # Unrelated upload rows must remain untouched by either failure path.
    digest = hashlib.sha256(b"unrelated upload").hexdigest()
    presign = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={
            "filename": "old.txt",
            "size_bytes": 1,
            "content_type": "text/plain",
            "content_hash": digest,
        },
    )
    assert presign.status_code == 200
    before = _materials(client, workspace_id)
    original_put = storage.put_object
    original_execute = DatabaseConnection.execute
    calls = 0

    def put(key, data, content_type=""):
        nonlocal calls
        original_put(key, data, content_type)
        if failure == "put":
            calls += 1
            if calls == 2:
                raise OSError("PUT acknowledged late")

    def execute(conn, sql, params=None):
        nonlocal calls
        result = original_execute(conn, sql, params)
        if failure == "database" and sql.startswith("insert into materials"):
            calls += 1
            if calls == 2:
                raise RuntimeError("second row failed")
        return result

    monkeypatch.setattr(storage, "put_object", put)
    monkeypatch.setattr(DatabaseConnection, "execute", execute)
    from server.app.services.materials import MaterialStorageUnavailableError

    expected = MaterialStorageUnavailableError if failure == "put" else RuntimeError
    with pytest.raises(expected):
        materialize_text_items(
            job_db,
            client.app.state.materials_service,
            workspace_id,
            [{"type": "text", "content": "A"}, {"type": "text", "content": "B"}],
        )
    assert calls == 2
    assert _materials(client, workspace_id) == before
    assert storage.objects == {}
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []


@pytest.mark.parametrize("same_filename", [True, False])
def test_concurrent_text_batches_keep_only_winner_objects(
    client, storage, job_db, monkeypatch, same_filename
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from server.app.services.run_text_items import materialize_text_items

    workspace_id = _create_workspace(client)
    barrier = Barrier(2)
    original_put = storage.put_object

    def put(key, data, content_type=""):
        original_put(key, data, content_type)
        barrier.wait(timeout=10)

    monkeypatch.setattr(storage, "put_object", put)

    def submit(filename, contents):
        return materialize_text_items(
            job_db,
            client.app.state.materials_service,
            workspace_id,
            [{"type": "text", "content": content, "filename": filename} for content in contents],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, "a.md", ["A", "B"])
        second = pool.submit(submit, "a.md" if same_filename else "b.txt", ["B", "A"])
        one, two = first.result(timeout=20), second.result(timeout=20)
    assert one == list(reversed(two))
    rows = _materials(client, workspace_id)
    assert len(rows) == 2
    assert set(storage.objects) == _storage_keys(job_db, workspace_id)
    assert set(storage.objects.values()) == {b"A", b"B"}


def test_commit_acknowledgement_failure_preserves_committed_objects(
    client, storage, job_db, monkeypatch
) -> None:
    from server.app.services.run_text_items import materialize_text_items

    workspace_id = _create_workspace(client)
    original = job_db.publish_inline_materials

    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("commit acknowledgement lost")

    monkeypatch.setattr(job_db, "publish_inline_materials", commit_then_fail)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        materialize_text_items(
            job_db,
            client.app.state.materials_service,
            workspace_id,
            [{"type": "text", "content": "A"}, {"type": "text", "content": "B"}],
        )
    rows = _materials(client, workspace_id)
    assert len(rows) == 2
    assert set(storage.objects) == _storage_keys(job_db, workspace_id)


def test_failed_batch_does_not_delete_concurrent_winner(client, storage, job_db, monkeypatch):
    from server.app.services.run_text_items import materialize_text_items

    workspace_id = _create_workspace(client)
    original = job_db.publish_inline_materials

    def concurrent_winner_then_fail(*args, **kwargs):
        monkeypatch.setattr(job_db, "publish_inline_materials", original)
        materialize_text_items(
            job_db,
            client.app.state.materials_service,
            workspace_id,
            [{"type": "text", "content": "A"}],
        )
        raise RuntimeError("losing request failed")

    monkeypatch.setattr(job_db, "publish_inline_materials", concurrent_winner_then_fail)
    with pytest.raises(RuntimeError, match="losing request"):
        materialize_text_items(
            job_db,
            client.app.state.materials_service,
            workspace_id,
            [{"type": "text", "content": "A"}, {"type": "text", "content": "B"}],
        )
    (key,) = _storage_keys(job_db, workspace_id)
    assert storage.objects == {key: b"A"}


@pytest.mark.parametrize("field", ["content", "filename"])
def test_invalid_unicode_in_later_item_writes_nothing(client, storage, job_db, field):
    from server.app.services.job_errors import InvalidOperationError
    from server.app.services.run_text_items import materialize_text_items

    workspace_id = _create_workspace(client)
    invalid = {"type": "text", "content": "B", "filename": "b.md"}
    invalid[field] = "\ud800.md"
    with pytest.raises(InvalidOperationError, match="UTF-8"):
        materialize_text_items(
            job_db,
            client.app.state.materials_service,
            workspace_id,
            [{"type": "text", "content": "A"}, invalid],
        )
    assert storage.put_calls == 0
    assert _materials(client, workspace_id) == []


@pytest.mark.parametrize("cleanup_failure", ["lookup", "delete"])
def test_cleanup_failure_preserves_original_error_and_logs_recovery_keys(
    client, storage, job_db, monkeypatch, caplog, cleanup_failure
):
    from server.app.services.run_text_items import materialize_text_items

    workspace_id = _create_workspace(client)

    def fail_commit(*args, **kwargs):
        raise RuntimeError("original failure")

    def fail_lookup(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(job_db, "publish_inline_materials", fail_commit)
    if cleanup_failure == "lookup":
        monkeypatch.setattr(job_db, "referenced_inline_objects", fail_lookup)
    else:
        storage.fail_deletes = True
    with pytest.raises(RuntimeError, match="original failure"):
        materialize_text_items(
            job_db,
            client.app.state.materials_service,
            workspace_id,
            [{"type": "text", "content": "A"}],
        )
    assert _materials(client, workspace_id) == []
    (key,) = storage.objects
    assert key in caplog.text


def test_text_item_without_storage_returns_503(client, job_db, monkeypatch) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    monkeypatch.setattr(client.app.state.materials_service, "storage", None)

    response = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])

    assert response.status_code == 503
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []


def test_text_items_mixed_with_materials_keep_order_and_dedup(client, storage, job_db) -> None:
    """[text A, material, text A, text B]: one material per distinct text, job
    order follows item order, the duplicate text dedups like a re-uploaded file."""
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    _insert_ready_material(job_db, workspace_id, "m-doc")

    response = _create_run(
        client,
        workspace_id,
        [
            {"type": "text", "content": "A", "filename": "a.md"},
            {"type": "material", "material_id": "m-doc"},
            {"type": "text", "content": "A", "filename": "a.md"},
            {"type": "text", "content": "B", "filename": "b.txt"},
        ],
    )

    assert response.status_code == 200, response.text
    assert response.json()["created_count"] == 3
    materials = {m["filename"]: m for m in _materials(client, workspace_id)}
    assert set(materials) == {"a.md", "b.txt", "doc.txt"}
    assert materials["b.txt"]["content_type"] == "text/plain; charset=utf-8"
    # Uploads record the user; text materials must too.
    assert materials["a.md"]["created_by"] != ""
    jobs = client.get(f"/api/workspaces/{workspace_id}/jobs?limit=10").json()["jobs"]
    titles = sorted(job["title"] for job in jobs)
    assert titles == ["a.md", "b.txt", "doc.txt"]


def test_text_item_reuses_ready_row_without_writing_a_second_object(
    client, storage, job_db
) -> None:
    """Same bytes already ready under another filename: reuse the row, no new object."""
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    first = _create_run(
        client, workspace_id, [{"type": "text", "content": REQUIREMENT, "filename": "a.md"}]
    )
    assert first.status_code == 200, first.text
    puts_before = storage.put_calls

    second = _create_run(
        client, workspace_id, [{"type": "text", "content": REQUIREMENT, "filename": "b.md"}]
    )

    # Same material → same job dedup key → nothing new, and no orphan object.
    assert second.status_code == 400
    assert "No tasks were resolved" in second.json()["detail"]
    assert storage.put_calls == puts_before
    assert set(storage.objects) == _storage_keys(job_db, workspace_id)
    assert len(storage.objects) == 1
    assert _materials(client, workspace_id)[0]["filename"] == "a.md"


def test_text_item_storage_failure_returns_503_without_a_run(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    storage.fail_put = True

    response = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])

    assert response.status_code == 503
    assert "unreachable" in response.json()["detail"]
    assert _materials(client, workspace_id) == []
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []


def test_missing_material_is_reported_before_text_is_stored(client, storage, job_db) -> None:
    """Stored items are validated first: an unknown material id 404s before any
    text material is written (error precedence of the pre-text path)."""
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)

    response = _create_run(
        client,
        workspace_id,
        [
            {"type": "text", "content": REQUIREMENT},
            {"type": "material", "material_id": "m-missing"},
        ],
    )

    assert response.status_code == 404
    assert _materials(client, workspace_id) == []
    assert storage.objects == {}


@pytest.mark.parametrize("upload_state", ["uploading", "ready", "failed", "expired"])
def test_presign_wins_after_text_precheck(client, storage, job_db, monkeypatch, upload_state):
    """Real upload protocol runs while text is paused after its missing-row read."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from server.app.services.material_ttl import expire_due_materials
    from server.app.services.materials import MaterialVerificationError

    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    service = client.app.state.materials_service
    staged, resume = Event(), Event()
    original_put = storage.put_object

    def paused_put(key, data, content_type=""):
        original_put(key, data, content_type)
        if "/inline-" in key and data == b"B":
            staged.set()
            assert resume.wait(10), "upload did not release text publication"

    monkeypatch.setattr(storage, "put_object", paused_put)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _create_run,
            client,
            workspace_id,
            [{"type": "text", "content": "A"}, {"type": "text", "content": "B"}],
        )
        try:
            assert staged.wait(10)
            upload = service.presign(
                workspace_id,
                filename="browser.txt",
                size_bytes=1,
                content_hash=hashlib.sha256(b"B").hexdigest(),
            )
            material_id = upload["material"]["id"]
            (key,) = _storage_keys(job_db, workspace_id)
            storage.put_object(key, b"X" if upload_state == "failed" else b"B")
            if upload_state == "failed":
                with pytest.raises(MaterialVerificationError):
                    service.complete(workspace_id, material_id)
            elif upload_state != "uploading":
                service.complete(workspace_id, material_id)
                if upload_state == "expired":
                    with job_db.write() as conn:
                        conn.execute(
                            "update materials set expires_at=now()-interval '1 day' where id=%s",
                            (material_id,),
                        )
                    assert expire_due_materials(job_db) == 1
            before = service.get(workspace_id, material_id)
        finally:
            resume.set()
        response = future.result(timeout=15)
    assert response.status_code == (200 if upload_state == "ready" else 409), response.text
    assert service.get(workspace_id, material_id) == before
    assert set(storage.objects) == _storage_keys(job_db, workspace_id)
    if upload_state != "ready":
        # A's hash sorts before B: publication rolls back its earlier INSERT too.
        assert _materials(client, workspace_id) == [before]
        assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []
    if upload_state == "uploading":
        assert service.complete(workspace_id, material_id)["status"] == "ready"


def test_text_wins_after_presign_precheck(client, storage, job_db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread

    from server.app.db.connection import DatabaseConnection

    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    service = client.app.state.materials_service
    checked, resume = Event(), Event()
    original_execute = DatabaseConnection.execute

    def paused_lookup(conn, sql, params=None):
        result = original_execute(conn, sql, params)
        if current_thread().name.startswith("browser") and sql.startswith(
            "select * from materials"
        ):
            checked.set()
            assert resume.wait(10), "text did not release browser presign"
        return result

    monkeypatch.setattr(DatabaseConnection, "execute", paused_lookup)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser") as pool:
        future = pool.submit(
            service.presign,
            workspace_id,
            filename="browser.txt",
            size_bytes=1,
            content_hash=hashlib.sha256(b"B").hexdigest(),
        )
        try:
            assert checked.wait(10)
            response = _create_run(client, workspace_id, [{"type": "text", "content": "B"}])
            assert response.status_code == 200, response.text
        finally:
            resume.set()
        upload = future.result(timeout=15)
    assert upload["deduplicated"] is True
    assert upload["upload_url"] is None
    assert upload["material"]["filename"] == "需求.md"
    assert len(storage.objects) == 1
    assert set(storage.objects) == _storage_keys(job_db, workspace_id)


@pytest.mark.parametrize("change", ["expired", "deleted", "replaced", "uploading"])
def test_ready_reuse_is_revalidated_at_publication(client, storage, job_db, monkeypatch, change):
    from server.app.services.material_ttl import expire_due_materials

    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    service = client.app.state.materials_service
    digest = hashlib.sha256(b"B").hexdigest()
    upload = service.presign(workspace_id, filename="old.txt", size_bytes=1, content_hash=digest)
    material_id = upload["material"]["id"]
    (key,) = _storage_keys(job_db, workspace_id)
    storage.put_object(key, b"B")
    service.complete(workspace_id, material_id)
    runtime_db = client.app.state.job_db
    original_publish = runtime_db.publish_inline_materials

    def mutate_then_publish(*args, **kwargs):
        if change in {"expired", "uploading"}:
            with job_db.write() as conn:
                conn.execute(
                    "update materials set expires_at=now()-interval '1 day' where id=%s",
                    (material_id,),
                )
            assert expire_due_materials(job_db) == 1
        else:
            service.delete(workspace_id, material_id)
        if change in {"replaced", "uploading"}:
            new = service.presign(
                workspace_id, filename="new.txt", size_bytes=1, content_hash=digest
            )
            if change == "replaced":
                (new_key,) = _storage_keys(job_db, workspace_id)
                storage.put_object(new_key, b"B")
                service.complete(workspace_id, new["material"]["id"])
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(runtime_db, "publish_inline_materials", mutate_then_publish)
    response = _create_run(
        client,
        workspace_id,
        [{"type": "text", "content": "A"}, {"type": "text", "content": "B"}],
    )
    assert response.status_code == 409, response.text
    assert all(row["content_hash"] == digest for row in _materials(client, workspace_id))
    assert set(storage.objects) == _storage_keys(job_db, workspace_id)
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []


def test_text_item_filename_falls_back_to_start_node_text_input(client, storage, job_db) -> None:
    """Start node ``text_input.filename`` names materials for items without one."""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    workspace_id = _create_workspace(client)
    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "text"]
    raw["nodes"]["_start"]["text_input"] = {"filename": "创作需求.md", "template": "# 需求\n"}
    WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )

    response = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])

    assert response.status_code == 200, response.text
    (material,) = _materials(client, workspace_id)
    assert material["filename"] == "创作需求.md"
    # An explicit filename still wins over the configured default.
    explicit = _create_run(
        client, workspace_id, [{"type": "text", "content": REQUIREMENT + "!", "filename": "x.txt"}]
    )
    assert explicit.status_code == 200, explicit.text
    assert {m["filename"] for m in _materials(client, workspace_id)} == {"创作需求.md", "x.txt"}
