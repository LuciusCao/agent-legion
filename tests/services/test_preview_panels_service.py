"""Preview panel service (issue #328): validation, draft/publish lifecycle, context."""

from __future__ import annotations

import pytest

from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.preview_panels import (
    MAX_BUNDLE_BYTES,
    PreviewPanelService,
    bundle_hash,
    get_preview_context,
)
from server.app.storage_paths import resolve_job_dir

VALID_HTML = "<!doctype html><html><body><h1>panel</h1></body></html>"
UPDATED_HTML = "<!doctype html><html><body><h1>panel v2</h1></body></html>"


@pytest.fixture
def service(job_db):
    return PreviewPanelService(job_db)


@pytest.fixture
def workspace_id(job_db):
    return job_db.create_workspace(default_workflow_key="wf", name="preview-panels")["id"]


def test_save_draft_creates_version_one(service, workspace_id) -> None:
    row = service.save_draft(workspace_id, VALID_HTML, "user:u1", "first pass")

    assert row["version"] == 1
    assert row["status"] == "draft"
    assert row["created_by"] == "user:u1"
    assert row["change_note"] == "first pass"
    assert row["html"] == VALID_HTML
    assert row["html_hash"] == bundle_hash(VALID_HTML)
    # Draft only: nothing renders until a human publishes.
    assert service.get_published(workspace_id) is None
    state = service.get_state(workspace_id)
    assert state["published"] is None
    assert state["draft"]["version"] == 1


def test_save_draft_rejects_invalid_bundles(service, workspace_id) -> None:
    with pytest.raises(InvalidOperationError, match="must not be empty"):
        service.save_draft(workspace_id, "   \n ", "user:u1")
    with pytest.raises(InvalidOperationError, match="full HTML document"):
        service.save_draft(workspace_id, "just text, no markup", "user:u1")
    oversized = VALID_HTML + "<!--" + "x" * MAX_BUNDLE_BYTES + "-->"
    with pytest.raises(InvalidOperationError, match="size limit"):
        service.save_draft(workspace_id, oversized, "user:u1")
    assert service.get_state(workspace_id) == {"published": None, "draft": None}


def test_save_draft_overwrites_existing_draft(service, workspace_id) -> None:
    service.save_draft(workspace_id, VALID_HTML, "user:u1")
    row = service.save_draft(workspace_id, UPDATED_HTML, "studio-agent:u2")

    assert row["version"] == 1
    assert row["html"] == UPDATED_HTML
    assert row["created_by"] == "studio-agent:u2"


def test_publish_promotes_draft_and_archives_previous(service, workspace_id) -> None:
    service.save_draft(workspace_id, VALID_HTML, "studio-agent:u1")
    published = service.publish(workspace_id)

    assert published["status"] == "published"
    assert published["published_at"] is not None
    state = service.get_state(workspace_id)
    assert state["published"]["html"] == VALID_HTML
    assert state["draft"] is None

    service.save_draft(workspace_id, UPDATED_HTML, "studio-agent:u1")
    republished = service.publish(workspace_id)
    assert republished["version"] == 2
    assert republished["html"] == UPDATED_HTML
    assert service.get_published(workspace_id)["version"] == 2


def test_publish_without_draft_raises_not_found(service, workspace_id) -> None:
    with pytest.raises(NotFoundError):
        service.publish(workspace_id)


def test_archive_all_resets_to_fallback(service, workspace_id) -> None:
    service.save_draft(workspace_id, VALID_HTML, "user:u1")
    service.publish(workspace_id)
    service.save_draft(workspace_id, UPDATED_HTML, "user:u1")

    archived = service.archive_all(workspace_id)

    assert archived == 2
    assert service.get_state(workspace_id) == {"published": None, "draft": None}
    # A fresh draft starts a new version line afterwards.
    row = service.save_draft(workspace_id, VALID_HTML, "user:u1")
    assert row["version"] == 3


def test_panels_are_isolated_between_workspaces(service, job_db, workspace_id) -> None:
    other = job_db.create_workspace(default_workflow_key="wf", name="preview-other")["id"]
    service.save_draft(workspace_id, VALID_HTML, "user:u1")
    service.publish(workspace_id)

    assert service.get_state(other) == {"published": None, "draft": None}


def test_get_preview_context_unknown_workspace_404(job_db, settings) -> None:
    with pytest.raises(NotFoundError):
        get_preview_context(job_db, settings, "no-such-ws")


def test_get_preview_context_empty_workspace(job_db, settings, workspace_id) -> None:
    context = get_preview_context(job_db, settings, workspace_id)

    assert context["workspace_id"] == workspace_id
    assert context["recent_jobs"] == []
    assert context["selected_job"] is None
    assert context["samples"] == {}


def test_get_preview_context_lists_and_samples_artifacts(job_db, settings, workspace_id) -> None:
    job = job_db.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="src-1",
        run_id="run-1",
        title="job one",
        node_keys=[],
        workspace_id=workspace_id,
    )
    job_dir = resolve_job_dir(job, settings.jobs_dir)
    (job_dir / "questions.json").write_text('{"questions": []}', encoding="utf-8")
    (job_dir / "notes.md").write_text("# notes\n" + "y" * 5000, encoding="utf-8")

    context = get_preview_context(job_db, settings, workspace_id)

    assert [j["id"] for j in context["recent_jobs"]] == [job["id"]]
    assert context["recent_jobs"][0]["artifacts"] == ["notes.md", "questions.json"]
    assert context["selected_job"]["id"] == job["id"]
    assert context["samples"]["questions.json"] == '{"questions": []}'
    # Long content is truncated at the sample budget and reported.
    assert len(context["samples"]["notes.md"]) == context["sample_max_chars"]
    assert context["truncated"] == ["notes.md"]


def test_get_preview_context_selects_requested_job(job_db, settings, workspace_id) -> None:
    first = job_db.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="src-1",
        run_id="run-1",
        title="first",
        node_keys=[],
        workspace_id=workspace_id,
    )
    second = job_db.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="src-2",
        run_id="run-2",
        title="second",
        node_keys=[],
        workspace_id=workspace_id,
    )
    job_dir = resolve_job_dir(first, settings.jobs_dir)
    (job_dir / "a.json").write_text("{}", encoding="utf-8")

    context = get_preview_context(job_db, settings, workspace_id, job_id=str(first["id"]))

    assert context["selected_job"]["id"] == first["id"]
    assert list(context["samples"]) == ["a.json"]
    # Recent list contains both jobs (created_at ties make order unreliable).
    assert {j["id"] for j in context["recent_jobs"]} == {first["id"], second["id"]}


def test_get_preview_context_rejects_job_from_another_workspace(
    job_db, settings, workspace_id
) -> None:
    other = job_db.create_workspace(default_workflow_key="wf", name="preview-ctx-other")["id"]
    foreign = job_db.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="src-f",
        run_id="run-f",
        title="foreign",
        node_keys=[],
        workspace_id=other,
    )

    with pytest.raises(NotFoundError):
        get_preview_context(job_db, settings, workspace_id, job_id=str(foreign["id"]))
