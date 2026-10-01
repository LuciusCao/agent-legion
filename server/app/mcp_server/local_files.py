"""Byte-preserving staging for MCP authoring (#767/#768).

Only the dedicated per-workspace scratch directory is readable. Authorize
through the existing backend before touching it; the backend still validates
the complete payload and owns every authoritative write. Descriptor-relative
opens reject symlinks in every untrusted component, including the workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anyio

from server.app.mcp_server.tool_client import ToolClient

MAX_BYTES = 16 * 1024 * 1024


def compact_response(response: str) -> str:
    """Local-path saves return receipts, not another copy of the large source."""
    try:
        value = json.loads(response)
    except ValueError:
        return response
    if not isinstance(value, dict):
        return response
    records = [value, *(value.get("files") or [])]
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("code", "content"):
            content = record.get(key)
            if isinstance(content, str):
                data = record.pop(key).encode("utf-8")
                record[f"{key}_bytes"] = len(data)
                record[f"{key}_sha256"] = hashlib.sha256(data).hexdigest()
    return json.dumps(value, ensure_ascii=False)


def staging_root(workspace_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", workspace_id):
        raise ValueError("Invalid workspace id for local staging")
    return Path.cwd().resolve() / "data" / "studio-mcp-files" / workspace_id


@contextmanager
def _parent(workspace_id: str, path: str, *, create: bool = False):
    root = staging_root(workspace_id)
    target = Path(path)
    if target.is_absolute():
        target = target.relative_to(root)
    if not target.parts or ".." in target.parts:
        raise ValueError("Local path must stay inside the workspace staging directory")
    # data/ and studio-mcp-files/ are also opened without following links.
    fd = os.open(Path.cwd().resolve(), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in ("data", "studio-mcp-files", workspace_id, *target.parts[:-1]):
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=fd)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, target.name, root / target
    finally:
        os.close(fd)


def read_text(workspace_id: str, path: str) -> str:
    with _parent(workspace_id, path) as (parent, name, _):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Local source must be a regular file without hard links")
            data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Local source exceeds 16 MiB")
    return data.decode("utf-8")  # bytes decode preserves CRLF and all escapes


async def authorize(client: ToolClient, workspace_id: str) -> None:
    response = await client.call(
        "GET", f"/workspaces/{quote(workspace_id, safe='')}/workflow/active"
    )
    try:
        value = json.loads(response)
    except ValueError:
        raise ValueError(response) from None
    if not isinstance(value, dict) or value.get("state") not in ("active", "empty"):
        raise ValueError("Workspace authorization did not return an active workflow state")


def _export(workspace_id: str, output_path: str, response: str) -> str:
    json.loads(response)  # Never export an HTTP/network error as source data.
    data = response.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("Export exceeds 16 MiB")
    with _parent(workspace_id, output_path, create=True) as (parent, name, path):
        # Export never overwrites edits or follows an existing symlink.
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(data)
        except OSError:
            os.unlink(name, dir_fd=parent)
            raise
    return json.dumps(
        {"output_path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    )


async def export_response(workspace_id: str, output_path: str | None, response: str) -> str:
    if output_path is None or response.startswith(("HTTP ", "request failed:")):
        return response
    return await anyio.to_thread.run_sync(_export, workspace_id, output_path, response)


def _load_files(
    workspace_id: str, files: list[dict[str, str]] | None, files_path: str | None
) -> list[dict[str, str]]:
    if (files is None) == (files_path is None):
        raise ValueError("Supply exactly one of files or files_path")
    value: Any = files
    if files_path is not None:
        value = json.loads(read_text(workspace_id, files_path))
        if isinstance(value, dict):
            value = value.get("files")  # accepts an unmodified get_* export
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError("Expected 1–100 files")
    result = []
    total = 0
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or item.get("truncated"):
            raise ValueError("Cannot save a malformed or truncated file")
        path = item.get("path")
        if not isinstance(path, str) or not path or path in seen:
            raise ValueError("File paths must be nonempty and unique")
        seen.add(path)
        if ("content" in item) == ("file_path" in item):
            raise ValueError("Each file needs exactly one of content or file_path")
        content = item.get("content")
        if "file_path" in item:
            if not isinstance(item["file_path"], str):
                raise ValueError("file_path must be a string")
            content = read_text(workspace_id, item["file_path"])
        if not isinstance(content, str):
            raise ValueError("File content must be text")
        total += len(content.encode("utf-8"))
        if total > MAX_BYTES:
            raise ValueError("Local file batch exceeds 16 MiB")
        result.append({"path": path, "content": content})
    return result


async def prepare_files(
    client: ToolClient,
    workspace_id: str,
    files: list[dict[str, str]] | None,
    files_path: str | None,
) -> tuple[list[dict[str, str]], bool]:
    local = files_path is not None or any("file_path" in f for f in files or [])
    if not local:
        if files is None:
            raise ValueError("Supply files or files_path")
        return files, False
    await authorize(client, workspace_id)
    return await anyio.to_thread.run_sync(_load_files, workspace_id, files, files_path), True


async def load_code(
    client: ToolClient, workspace_id: str, code: str | None, code_path: str | None
) -> str:
    if (code is None) == (code_path is None):
        raise ValueError("Supply exactly one of code or code_path")
    if code is not None:
        return code
    await authorize(client, workspace_id)
    return await anyio.to_thread.run_sync(read_text, workspace_id, str(code_path))
