from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

# Static-file serving helpers with cache headers for the SPA build output.
# Fingerprinted build assets (e.g. /assets/index-BdvET8O9.js) never change
# under the same URL, so they can be cached forever.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
# index.html and other non-fingerprinted files must revalidate on every load
# (ETag keeps this cheap) so that new deploys take effect immediately.
REVALIDATE_CACHE_CONTROL = "no-cache"


class FingerprintedStaticFiles(StaticFiles):
    """StaticFiles that marks fingerprinted build assets as immutable."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = IMMUTABLE_CACHE_CONTROL
        return response


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
        app.mount("/assets", FingerprintedStaticFiles(directory=frontend_assets), name="assets")

        @app.get("/{path:path}", response_model=None)
        def spa(path: str):
            headers = {"Cache-Control": REVALIDATE_CACHE_CONTROL}
            if not path:
                return FileResponse(frontend_index, headers=headers)
            requested = (frontend_dist / path).resolve()
            if requested.is_relative_to(frontend_dist.resolve()) and requested.is_file():
                return FileResponse(requested, headers=headers)
            return FileResponse(frontend_index, headers=headers)

    else:

        @app.get("/", response_class=HTMLResponse)
        def frontend_missing() -> str:
            return (
                "<main style='font-family: system-ui; padding: 24px'>"
                "<h1>Agent Legion API</h1>"
                "<p>Run the TypeScript frontend with <code>cd frontend && npm run dev</code>.</p>"
                "</main>"
            )
