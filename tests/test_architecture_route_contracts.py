from pathlib import Path

import pytest
from fastapi.responses import FileResponse

from scripts.architecture import route_contracts
from scripts.check_architecture import check_repository
from server.app.main import create_app
from tests.architecture_budget_helpers import write_neutral_budget_governance


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
    write_neutral_budget_governance(root)


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
    write_neutral_budget_governance(root)


def write_route_source(root: Path, source: str) -> None:
    path = root / "server/app/routes/example.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    write_neutral_budget_governance(root)


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


def test_helper_local_import_does_not_authorize_module_route(tmp_path):
    write_route_source(
        tmp_path,
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "def helper():\n"
        "    from fastapi.responses import FileResponse\n"
        "@router.get('/example')\n"
        "def example() -> FileResponse:\n"
        "    raise NotImplementedError\n",
    )

    assert any("requires named response_model" in error for error in check_repository(tmp_path))


def test_helper_local_import_does_not_authorize_factory_route(tmp_path):
    write_route_source(
        tmp_path,
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "def helper():\n"
        "    from fastapi.responses import FileResponse\n"
        "def create_router():\n"
        "    @router.get('/example')\n"
        "    def example() -> FileResponse:\n"
        "        raise NotImplementedError\n",
    )

    assert any("requires named response_model" in error for error in check_repository(tmp_path))


def test_pre_definition_rebinding_revokes_protocol_import(tmp_path):
    write_route_source(
        tmp_path,
        "from fastapi import APIRouter\n"
        "from fastapi.responses import FileResponse\n"
        "FileResponse = dict\n"
        "router = APIRouter()\n"
        "@router.get('/example')\n"
        "def example() -> FileResponse:\n"
        "    raise NotImplementedError\n",
    )

    assert any("requires named response_model" in error for error in check_repository(tmp_path))


def test_module_import_authorizes_nested_route(tmp_path):
    write_route_source(
        tmp_path,
        "from fastapi import APIRouter\n"
        "from fastapi.responses import FileResponse as DownloadResponse\n"
        "router = APIRouter()\n"
        "def create_router():\n"
        "    @router.get('/example')\n"
        "    def example() -> DownloadResponse:\n"
        "        raise NotImplementedError\n",
    )

    assert not any("requires named response_model" in error for error in check_repository(tmp_path))


def test_enclosing_factory_import_authorizes_nested_route(tmp_path):
    write_route_source(
        tmp_path,
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "def create_router():\n"
        "    from fastapi.responses import FileResponse\n"
        "    @router.get('/example')\n"
        "    def example() -> FileResponse:\n"
        "        raise NotImplementedError\n",
    )

    assert not any("requires named response_model" in error for error in check_repository(tmp_path))


def test_normal_annotation_ignores_post_definition_rebinding(tmp_path):
    write_route_source(
        tmp_path,
        "from fastapi import APIRouter\n"
        "from fastapi.responses import FileResponse\n"
        "router = APIRouter()\n"
        "@router.get('/example')\n"
        "def example() -> FileResponse:\n"
        "    raise NotImplementedError\n"
        "FileResponse = dict\n",
    )

    assert not any("requires named response_model" in error for error in check_repository(tmp_path))


def test_postponed_annotation_honors_post_definition_rebinding(tmp_path):
    write_route_source(
        tmp_path,
        "from __future__ import annotations\n"
        "from fastapi import APIRouter\n"
        "from fastapi.responses import FileResponse\n"
        "router = APIRouter()\n"
        "@router.get('/example')\n"
        "def example() -> FileResponse:\n"
        "    raise NotImplementedError\n"
        "FileResponse = dict\n",
    )

    assert any("requires named response_model" in error for error in check_repository(tmp_path))


@pytest.mark.parametrize(
    "mutation",
    [
        "responses.FileResponse = dict",
        "responses.FileResponse: object = dict",
        "responses.FileResponse += dict",
        "del responses.FileResponse",
    ],
)
def test_module_alias_attribute_mutation_revokes_protocol_origin(tmp_path, mutation):
    write_route_source(
        tmp_path,
        "from fastapi import APIRouter\n"
        "import fastapi.responses as responses\n"
        f"{mutation}\n"
        "router = APIRouter()\n"
        "@router.get('/example')\n"
        "def example() -> responses.FileResponse:\n"
        "    raise NotImplementedError\n",
    )

    assert any("requires named response_model" in error for error in check_repository(tmp_path))
