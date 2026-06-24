from unittest.mock import MagicMock, patch

import pytest

from server.app.executors.cancellation import CancellationToken
from server.app.workflows.question_content import (
    _base_payload,
    _decode_json_object,
    _effective_cms_config,
    _payload_from_detail,
    assemble_package,
    fetch_question_context,
)


def test_decode_json_object_with_none():
    assert _decode_json_object(None) == {}


def test_decode_json_object_with_empty_string():
    assert _decode_json_object("") == {}


def test_decode_json_object_with_invalid_json():
    assert _decode_json_object("not json") == {}


def test_decode_json_object_with_non_dict_json():
    assert _decode_json_object("[1, 2, 3]") == {}


def test_decode_json_object_with_dict():
    assert _decode_json_object('{"key": "value"}') == {"key": "value"}


def _question_detail_settings_config(api_url: str) -> dict:
    return {
        "resource_providers": {
            "cms.question.detail": {
                "api_url": api_url,
            }
        }
    }


def test_effective_cms_config_without_settings_config():
    job = {"workspace_id": "ws-1", "batch_id": "batch-1"}
    context = {"job_db": MagicMock()}
    context["job_db"].get_workspace.return_value = None
    context["job_db"].get_batch.return_value = None
    config = _effective_cms_config(job, context)
    # Default provider metadata is still filled in even without a configured URL.
    assert config == {"provider": "cms.question.detail"}


def test_effective_cms_config_with_settings_config_only():
    job = {"workspace_id": "ws-1", "batch_id": "batch-1"}
    context = {
        "settings_config": _question_detail_settings_config("https://cms.example.com"),
        "job_db": MagicMock(),
    }
    context["job_db"].get_workspace.return_value = None
    context["job_db"].get_batch.return_value = None
    config = _effective_cms_config(job, context)
    assert config["api_url"] == "https://cms.example.com"


def test_base_payload():
    job = {"source_id": "q1", "title": "Title", "source_type": "question"}
    payload = _base_payload(job)
    assert payload == {
        "question_id": "q1",
        "title": "Title",
        "source_type": "question",
        "normalized": {},
        "cms_payload": None,
    }


def test_payload_from_detail():
    detail = MagicMock()
    detail.question_id = "q1"
    detail.title = "Detail Title"
    detail.normalized = {"stem": "stem"}
    detail.payload = {"raw": "data"}
    job = {"source_id": "q1", "title": "Job Title", "source_type": "question"}
    payload = _payload_from_detail(job, detail)
    assert payload["question_id"] == "q1"
    assert payload["title"] == "Detail Title"
    assert payload["normalized"] == {"stem": "stem"}
    assert payload["cms_payload"] == {"raw": "data"}


def test_payload_from_detail_uses_job_fallback():
    detail = MagicMock()
    detail.question_id = None
    detail.title = None
    detail.normalized = {}
    detail.payload = None
    job = {"source_id": "q1", "title": "Job Title", "source_type": "question"}
    payload = _payload_from_detail(job, detail)
    assert payload["question_id"] == "q1"
    assert payload["title"] == "Job Title"


def test_fetch_question_context_without_cms(tmp_path):
    job = {"source_id": "q1", "title": "Title", "source_type": "question"}
    artifact_dir = tmp_path / "artifacts"
    fetch_question_context(job, artifact_dir, context={})

    out_path = artifact_dir / "question_context.json"
    assert out_path.is_file()
    data = __import__("json").loads(out_path.read_text(encoding="utf-8"))
    assert data["question_id"] == "q1"
    assert data["cms_payload"] is None


def test_fetch_question_context_with_cms(tmp_path):
    job = {"source_id": "q1", "title": "Title", "source_type": "question"}
    artifact_dir = tmp_path / "artifacts"
    detail = MagicMock()
    detail.question_id = "q1"
    detail.title = "CMS Title"
    detail.normalized = {"stem": "stem"}
    detail.payload = {"raw": "data"}

    settings_config = {
        "resource_providers": {
            "cms.question.detail": {
                "api_url": "https://cms.example.com",
            }
        }
    }

    with (
        patch(
            "server.app.workflows.question_content.get_token", return_value="token"
        ) as mock_token,
        patch(
            "server.app.workflows.question_content.fetch_question_detail", return_value=detail
        ) as mock_fetch,
    ):
        fetch_question_context(
            job,
            artifact_dir,
            context={"settings_config": settings_config, "env": "prod"},
        )

    mock_token.assert_called_once()
    assert mock_token.call_args[0][0] == ""
    mock_fetch.assert_called_once_with("q1", "https://cms.example.com", "token")

    out_path = artifact_dir / "question_context.json"
    data = __import__("json").loads(out_path.read_text(encoding="utf-8"))
    assert data["title"] == "CMS Title"
    assert data["cms_payload"] == {"raw": "data"}


def test_assemble_package_with_all_inputs(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    inputs = {
        "question_context.json": {"question_id": "q1"},
        "understanding.json": {"summary": "understanding"},
        "natural_reading.md": "# Reading",
        "misconceptions.json": {"misconceptions": []},
        "solution_steps.json": {"steps": []},
        "faq.json": {"faqs": []},
        "content_graph.json": {"nodes": []},
        "interactive_template.json": {"template": {}},
        "review_result.json": {"approved": True},
    }
    for name, content in inputs.items():
        if name.endswith(".md"):
            (artifact_dir / name).write_text(content, encoding="utf-8")
        else:
            (artifact_dir / name).write_text(
                __import__("json").dumps(content, ensure_ascii=False), encoding="utf-8"
            )

    job = {"source_id": "q1", "source_type": "question", "title": "Title"}
    assemble_package(job, artifact_dir)

    manifest_path = artifact_dir / "manifest.json"
    upload_params_path = artifact_dir / "upload_params.json"
    assert manifest_path.is_file()
    assert upload_params_path.is_file()

    manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["question_id"] == "q1"
    assert all(item["present"] for item in manifest["artifacts"].values())

    upload_params = __import__("json").loads(upload_params_path.read_text(encoding="utf-8"))
    assert upload_params["artifacts"]["natural_reading.md"] == "# Reading"
    assert upload_params["artifacts"]["question_context.json"] == {"question_id": "q1"}


def test_assemble_package_with_missing_inputs(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "question_context.json").write_text(
        __import__("json").dumps({"question_id": "q1"}), encoding="utf-8"
    )

    job = {"source_id": "q1", "source_type": "question", "title": "Title"}
    assemble_package(job, artifact_dir)

    manifest = __import__("json").loads(
        (artifact_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["artifacts"]["question_context.json"]["present"] is True
    assert manifest["artifacts"]["understanding.json"]["present"] is False

    upload_params = __import__("json").loads(
        (artifact_dir / "upload_params.json").read_text(encoding="utf-8")
    )
    assert upload_params["artifacts"]["understanding.json"] is None


def test_assemble_package_respects_cancellation(tmp_path):
    from server.app.executors.cancellation import CancelledError

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    token = CancellationToken()
    token.cancel()
    context = {"cancellation": token}
    job = {"source_id": "q1", "source_type": "question", "title": "Title"}
    with pytest.raises(CancelledError):
        assemble_package(job, artifact_dir, context=context)
