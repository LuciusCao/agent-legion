from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


def _not_found(path: str) -> None:  # noqa: ARG001
    raise HTTPException(status_code=404, detail="Not Found")


def mount_spa(app: FastAPI, frontend_dist: Path) -> None:
    """Mount static assets and the SPA catch-all, plus an API 404 guard."""
    app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )(_not_found)

    frontend_assets = frontend_dist / "assets"
    frontend_index = frontend_dist / "index.html"
    if frontend_index.exists() and frontend_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_assets), name="assets")

        @app.get("/{path:path}", response_model=None)
        def spa(path: str):
            if not path:
                return FileResponse(frontend_index)
            requested = (frontend_dist / path).resolve()
            if requested.is_relative_to(frontend_dist.resolve()) and requested.is_file():
                return FileResponse(requested)
            return FileResponse(frontend_index)

    else:

        @app.get("/", response_class=HTMLResponse)
        def frontend_missing() -> str:
            return (
                "<main style='font-family: system-ui; padding: 24px'>"
                "<h1>Agent Legion API</h1>"
                "<p>Run the TypeScript frontend with <code>cd frontend && npm run dev</code>.</p>"
                "</main>"
            )
