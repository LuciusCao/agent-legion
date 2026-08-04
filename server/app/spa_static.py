"""Static-file serving helpers with cache headers for the SPA build output."""

from __future__ import annotations

from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

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
