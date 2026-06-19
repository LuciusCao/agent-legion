import json
from pathlib import Path

import pytest
from fastapi.responses import FileResponse

from scripts.architecture import route_contracts
from scripts.check_architecture import check_repository
from server.app.main import create_app


def write_route(root: Path, annotation: str | None, *, response_model_none: bool = False) -> None:
    return_annotation = f" -> {annotation}" if annotation else ""
    decorator_argument = ", response_model=None" if response_model_none else ""
    path = root / "server/app/routes/example.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from typing import Any\n"
        "from fastapi import APIRouter, Response, responses\n"
        "from fastapi.responses import (\n"
        "    FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse\n"
        ")\n"
        "router = APIRouter()\n"
        f"@router.get('/example'{decorator_argument})\n"
        f"def example(){return_annotation}:\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    budget_path = root / "config/architecture/architecture-budgets.json"
    budget_path.parent.mkdir(parents=True, exist_ok=True)
    budget_path.write_text(json.dumps({"files": {}}), encoding="utf-8")


def write_custom_route(root: Path, imports: str, annotation: str) -> None:
    path = root / "server/app/routes/example.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"from fastapi import APIRouter\n{imports}\n"
        "router = APIRouter()\n"
        "@router.get('/example')\n"
        f"def example() -> {annotation}:\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    budget_path = root / "config/architecture/architecture-budgets.json"
    budget_path.parent.mkdir(parents=True, exist_ok=True)
    budget_path.write_text(json.dumps({"files": {}}), encoding="utf-8")


@pytest.mark.parametrize(
    "annotation",
    [
        "FileResponse",
        "RedirectResponse",
        "StreamingResponse",
        "PlainTextResponse",
        "FileResponse | RedirectResponse",
        "FileResponse | RedirectResponse | PlainTextResponse",
        "responses.FileResponse",
    ],
)
def test_accepts_protocol_response_annotation_without_response_model(tmp_path, annotation):
    write_route(tmp_path, annotation)

    errors = check_repository(tmp_path)

    assert not any("requires named response_model" in error for error in errors)


@pytest.mark.parametrize(
    "annotation",
    [
        "Response",
        "Any",
        "FileResponse | dict[str, str]",
        None,
    ],
)
def test_rejects_non_protocol_response_annotation_without_response_model(tmp_path, annotation):
    write_route(tmp_path, annotation)

    errors = check_repository(tmp_path)

    assert any("requires named response_model" in error for error in errors)


def test_accepts_protocol_annotation_with_explicit_none_response_model(tmp_path):
    write_route(tmp_path, "FileResponse | RedirectResponse", response_model_none=True)

    errors = check_repository(tmp_path)

    assert not any("requires named response_model" in error for error in errors)


def test_app_registers_video_file_protocol_union(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)

    methods = {
        method
        for route in app.routes
        if route.path == "/api/videos/{video_id}/video"
        for method in route.methods
    }
    assert {"GET", "HEAD"} <= methods


@pytest.mark.parametrize(
    "imports,annotation",
    [
        ("from fastapi.responses import FileResponse as DownloadResponse", "DownloadResponse"),
        ("import fastapi.responses as responses", "responses.FileResponse"),
        ("import fastapi.responses", "fastapi.responses.FileResponse"),
        ("from starlette.responses import StreamingResponse as EventResponse", "EventResponse"),
    ],
)
def test_accepts_protocol_response_import_aliases(tmp_path, imports, annotation):
    write_custom_route(tmp_path, imports, annotation)

    errors = check_repository(tmp_path)

    assert not any("requires named response_model" in error for error in errors)


@pytest.mark.parametrize(
    "imports,annotation",
    [
        ("from unrelated import FileResponse", "FileResponse"),
        ("import unrelated", "unrelated.FileResponse"),
        ("", "fastapi.responses.FileResponse"),
    ],
)
def test_rejects_protocol_response_name_from_unrelated_module(tmp_path, imports, annotation):
    write_custom_route(tmp_path, imports, annotation)

    errors = check_repository(tmp_path)

    assert any("requires named response_model" in error for error in errors)


def test_runtime_protocol_response_requires_approved_class_identity():
    class FileResponse:
        pass

    assert not route_contracts.has_protocol_response_type(FileResponse)


def test_runtime_protocol_response_accepts_approved_subclass():
    class DownloadResponse(FileResponse):
        pass

    assert route_contracts.has_protocol_response_type(DownloadResponse)


def test_runtime_protocol_response_resolves_postponed_annotation():
    namespace: dict[str, object] = {}
    exec(
        "from __future__ import annotations\n"
        "from fastapi.responses import FileResponse\n"
        "def endpoint() -> FileResponse:\n"
        "    raise NotImplementedError\n",
        namespace,
    )

    assert route_contracts.has_protocol_response_endpoint(namespace["endpoint"])
