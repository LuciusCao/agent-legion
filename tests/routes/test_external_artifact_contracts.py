"""Focused wire-schema pins for the external artifact contract split."""

import pytest

from server.app.routes.external_artifact_contracts import (
    ExternalArtifactEntry,
    ExternalArtifactListResponse,
)
from server.app.routes.external_artifact_entry_contract import (
    ExternalArtifactEntry as SplitExternalArtifactEntry,
)

pytestmark = pytest.mark.no_db


def test_entry_split_preserves_public_model_and_exact_schema() -> None:
    assert ExternalArtifactEntry is SplitExternalArtifactEntry
    assert ExternalArtifactEntry.model_json_schema() == {
        "description": (
            "One manifest entry: ``storage=object`` rows come from the authoritative\n"
            "job_artifacts manifest (size/content_hash/uploaded_at distinguish the\n"
            "current execution after a rerun, #508); ``local`` rows are legacy\n"
            "job_dir-only names with no manifest metadata.\n\n"
            "#739: object-backed rows additionally carry a presigned GET\n"
            "``download_url`` (S3 answers it directly — big media downloads leave the\n"
            "Host process alone). The URL serves the raw endpoint's representation:\n"
            "its response headers (Content-Type, attachment disposition and, for\n"
            "``.gz`` rows (#338), ``Content-Encoding: gzip``) are signed in, so the\n"
            "two channels answer alike. It addresses the CURRENT bytes under the name\n"
            "(rerun semantics #508, same as raw — verify against ``content_hash``) and\n"
            "``expires_at`` is an upper bound (re-fetch the manifest on 403).\n"
            "``local`` rows and instances without object storage keep both fields null."
        ),
        "properties": {
            "content_encoding": {
                "default": "",
                "description": (
                    'Stored-form marker: "gzip" when the object holds gzip-compressed '
                    "bytes (#338) — both download_url and the raw endpoint answer with "
                    "Content-Encoding: gzip (HTTP clients decode transparently); empty "
                    "otherwise"
                ),
                "title": "Content Encoding",
                "type": "string",
            },
            "content_hash": {"default": "", "title": "Content Hash", "type": "string"},
            "download_url": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "description": (
                    "Presigned object-storage GET URL answering with the raw endpoint's "
                    "headers (storage=object rows); null for local entries and instances "
                    "without object storage — use the raw endpoint then"
                ),
                "title": "Download Url",
            },
            "expires_at": {
                "anyOf": [{"format": "date-time", "type": "string"}, {"type": "null"}],
                "default": None,
                "description": (
                    "Latest moment download_url can work (upper bound: it may 403 "
                    "earlier, e.g. short-lived signing credentials — re-fetch the "
                    "manifest then); null whenever download_url is null"
                ),
                "title": "Expires At",
            },
            "media_type": {
                "default": "application/octet-stream",
                "description": (
                    "Content-Type the raw endpoint serves (whitelist-gated; JSON/text "
                    "and non-whitelisted extensions download as octet-stream)"
                ),
                "title": "Media Type",
                "type": "string",
            },
            "name": {"title": "Name", "type": "string"},
            "node_key": {"default": "", "title": "Node Key", "type": "string"},
            "size_bytes": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "default": None,
                "title": "Size Bytes",
            },
            "storage": {
                "description": '"object" (authoritative manifest row) or "local"',
                "title": "Storage",
                "type": "string",
            },
            "uploaded_at": {
                "anyOf": [{"format": "date-time", "type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Uploaded At",
            },
        },
        "required": ["name", "storage"],
        "title": "ExternalArtifactEntry",
        "type": "object",
    }


def test_list_contract_keeps_entry_reference_and_forbids_extras() -> None:
    schema = ExternalArtifactListResponse.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "job_id",
        "workspace_id",
        "status",
        "object_storage_enabled",
    ]
    assert schema["properties"]["artifacts"] == {
        "items": {"$ref": "#/$defs/ExternalArtifactEntry"},
        "title": "Artifacts",
        "type": "array",
    }
    assert schema["$defs"]["ExternalArtifactEntry"] == ExternalArtifactEntry.model_json_schema()
