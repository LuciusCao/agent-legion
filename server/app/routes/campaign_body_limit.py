"""ASGI-level request-body byte ceiling for the campaign JSON routes (#532
PR-A, PR #541 round-3 P1).

FastAPI reads and deserializes the WHOLE JSON body (several in-memory
copies: raw bytes → parsed tree → RunItem model list) before any service
check runs, so a single-item body carrying a huge params/ID string or an
unbounded job_ids list passes the item-count ceiling and balloons memory
ahead of the manifest_max_bytes 413. The multipart channel is already
bounded at read time (``manifest.read(limit)``); this middleware gives the
JSON channel the same bound at the ASGI receive boundary — the body never
fully enters the process.

Precedent: the worker artifact upload's Content-Length gate
(routes/artifacts.py) and the broker's spool cap (agent_broker/result_spool.
py). Shape choice: a pure-ASGI ``http`` middleware wraps ``receive`` with a
counter — unlike a BaseHTTPMiddleware call-``request.body()`` variant, it
does NOT read the body itself, so FastAPI's own JSON parsing still consumes
the (bounded) stream once and streaming stays streaming; unknown/multiple
content-lengths are rejected closed, and chunked bodies with no declared
length are counted chunk-by-chunk as they arrive.

APIRouter cannot carry middleware (no add_middleware), so this mounts at
the app level and narrows itself by path prefix — every campaign route
lives under /api/workspaces/{id}/campaigns; the GET reads in that subtree
carry no body and pass through the counting no-op. Mounted in main.py's
create_app, right where the router tree is included.

The limit is manifest_max_bytes + headroom: a legal manifest is AT MOST
manifest_max_bytes of canonical jsonl, and the JSON-body form of the same
manifest is more verbose than its canonical serialization (field order,
spacing, the surrounding request shape), so byte-for-byte equality would
over-reject legal edge manifests. The headroom also covers the rerun
target's job_ids bodies, which share these routes.
"""

from __future__ import annotations

from typing import Any

from starlette.datastructures import Headers

# The router subtree the limit applies to (path prefix, root_path-free).
CAMPAIGN_PATH_PREFIX = "/api/workspaces/"

# Headroom multiplier over manifest_max_bytes for the JSON-body form: the
# canonical jsonl serialization the ceiling measures is the most compact
# form; a hand-written JSON body of the same manifest is realistically at
# most ~2x (field order, whitespace, envelope keys), and 2x also covers the
# ~4 MB of a full 100k-id job_ids list against a 50 MB ceiling.
_HEADROOM_FACTOR = 2


def campaign_body_limit_max_bytes(settings: Any) -> int:
    """The campaigns JSON-body ceiling: manifest_max_bytes + headroom."""
    return int(settings.executor_runtime.campaigns.manifest_max_bytes) * _HEADROOM_FACTOR


def _is_campaign_route(path: str) -> bool:
    """Match the campaigns subtree: /api/workspaces/{ws}/campaigns[...].

    The ``/campaigns`` segment must be a WHOLE path segment (``campaignsx``
    is a different route); a bare ``/api/workspaces/campaigns`` is the
    workspaces listing (workspace id missing), not a campaign route.
    """
    if not path.startswith(CAMPAIGN_PATH_PREFIX):
        return False
    rest = path[len(CAMPAIGN_PATH_PREFIX) :]
    workspace, _, tail = rest.partition("/")
    if not workspace:
        return False
    if tail == "campaigns":
        return True
    return tail.startswith("campaigns/")


class CampaignBodyLimitMiddleware:
    """Bound the request body of the campaign create/preview JSON routes.

    Mounted at the app level (APIRouter has no middleware surface) and
    self-scoped by path prefix; multipart uploads keep their own tighter
    read bound (manifest_max_bytes + 1). The ceiling resolves per-request
    from the settings object (not snapshotted at mount time) so
    instance-settings-managed changes apply without a rebuild.
    """

    def __init__(self, app: Any, settings: Any) -> None:
        self.app = app
        self.settings = settings

    @property
    def max_bytes(self) -> int:
        return campaign_body_limit_max_bytes(self.settings)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not _is_campaign_route(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        max_bytes = self.max_bytes
        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared is not None:
            values = [value.strip() for value in declared.split(",")]
            if len(values) > 1 or not all(value.isdigit() for value in values):
                # Ambiguous/duplicated content-length: fail closed (the
                # request-smuggling family, not a legitimate client shape).
                await _reject(send)
                return
            if int(values[0]) > max_bytes:
                await _reject(send)
                return
        received = 0

        async def _bounded_receive() -> dict:
            nonlocal received
            message: dict = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    # Chunked bodies with no declared length: raise into the
                    # app's own body read, then convert to the 413 response
                    # below — the body never finishes entering the process.
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, _bounded_receive, send)
        except _BodyTooLarge:
            await _reject(send)


class _BodyTooLarge(Exception):
    """Internal signal: the streamed body passed the byte ceiling."""


async def _reject(send: Any) -> None:
    # The raw ASGI response shape Starlette's JSONResponse would emit; the
    # detail text is mirrored in both the body and the header-encoding-free
    # form so clients see the same 413 shape the routes' HTTPException uses.
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b'{"detail":"Campaign request body exceeds the manifest byte limit"}',
        }
    )
