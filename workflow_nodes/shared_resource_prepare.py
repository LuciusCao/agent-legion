"""Prepare pinned shared resources as one Agent-consumable JSON artifact."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from workspace_libs.node_sdk import NodeContext, entrypoint

OUTPUT_NAME = "shared_resources.json"
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_TEXT_CONTENT_TYPES = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/csv",
    "application/markdown",
}


def _is_text(content_type: str) -> bool:
    normalized = content_type.partition(";")[0].strip().lower()
    return (
        normalized.startswith("text/")
        or normalized in _TEXT_CONTENT_TYPES
        or normalized.endswith("+json")
        or normalized.endswith("+xml")
    )


def _declared_files(resources: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any], list[Any]]]:
    declared: list[tuple[str, Mapping[str, Any], list[Any]]] = []
    total_bytes = 0
    for resource_id in sorted(resources):
        resource = resources[resource_id]
        if not isinstance(resource, Mapping) or not resource_id:
            raise ValueError("Shared resource metadata is invalid")
        files = resource.get("files")
        if not isinstance(files, list | tuple):
            raise ValueError(f"Shared resource has no file list: {resource_id}")
        for file in files:
            if not isinstance(file, Mapping):
                raise ValueError(f"Shared resource file metadata is invalid: {resource_id}")
            size = file.get("size_bytes")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError(f"Shared resource file has an invalid size: {file.get('path')}")
            if size > MAX_FILE_BYTES:
                raise ValueError(
                    f"Shared resource file exceeds the {MAX_FILE_BYTES}-byte preparation limit: "
                    f"{file.get('path')}"
                )
            total_bytes += size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError(
                    f"Shared resources exceed the {MAX_TOTAL_BYTES}-byte preparation limit"
                )
        declared.append((str(resource_id), resource, list(files)))
    return declared


def _read_file(file: Mapping[str, Any]) -> bytes:
    path = Path(str(file.get("local_path") or ""))
    with path.open("rb") as handle:
        payload = handle.read(MAX_FILE_BYTES + 1)
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError(
            f"Shared resource file exceeds the {MAX_FILE_BYTES}-byte preparation limit: "
            f"{file.get('path')}"
        )
    if len(payload) != file["size_bytes"]:
        raise ValueError(f"Shared resource file size changed: {file.get('path')}")
    if hashlib.sha256(payload).hexdigest() != file.get("sha256"):
        raise ValueError(f"Shared resource file hash changed: {file.get('path')}")
    return payload


def _file_document(file: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    content_type = str(file.get("content_type") or "")
    if _is_text(content_type):
        try:
            content = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Shared resource text file is not valid UTF-8: {file.get('path')}"
            ) from exc
        encoding = "utf-8"
    else:
        content = base64.b64encode(payload).decode("ascii")
        encoding = "base64"
    return {
        "path": str(file["path"]),
        "content_type": content_type,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "encoding": encoding,
        "content": content,
    }


@entrypoint
def run(ctx: NodeContext) -> None:
    ctx.checkpoint()
    resources = ctx.shared_resources
    declared = _declared_files(resources)
    actual_total = 0
    documents = []
    for resource_id, resource, files in declared:
        file_documents = []
        for file in sorted(files, key=lambda item: str(item.get("path") or "")):
            payload = _read_file(file)
            actual_total += len(payload)
            if actual_total > MAX_TOTAL_BYTES:
                raise ValueError(
                    f"Shared resources exceed the {MAX_TOTAL_BYTES}-byte preparation limit"
                )
            file_documents.append(_file_document(file, payload))
        documents.append(
            {
                "resource_id": resource_id,
                "version": resource.get("version"),
                "manifest_hash": str(resource.get("manifest_hash") or ""),
                "files": file_documents,
            }
        )
    text = json.dumps(
        {"format": "shared-resources-v1", "resources": documents},
        ensure_ascii=False,
        indent=2,
    )
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"Prepared shared resources exceed the {MAX_DOCUMENT_BYTES}-byte document limit"
        )
    ctx.artifacts.write_text(OUTPUT_NAME, text)
