"""Default Studio MCP callback base derived from the serving address (#915).

Before #915 an unconfigured registry ``api_base`` fell back to the prod port
``http://127.0.0.1:8000`` on every instance, so a dev/worktree backend on any
other port handed its agents an MCP URL that never reached it — kimi-code then
dropped the server and the session ran with zero platform tools.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from server.app.studio_chat import serving_address
from server.app.studio_chat.registry import (
    DEFAULT_API_BASE,
    GLOBAL_SETTINGS_KEY,
    StudioAgentRegistryStore,
)
from server.app.studio_chat.serving_address import (
    ServingAddressMiddleware,
    derived_api_base,
    observe_server_address,
)

pytestmark = pytest.mark.no_db


class _FakeKV:
    def __init__(self, document: dict[str, Any] | None) -> None:
        self._document = document

    def get_global_settings_document(self, key: str) -> dict[str, Any] | None:
        assert key == GLOBAL_SETTINGS_KEY
        return json.loads(json.dumps(self._document)) if self._document is not None else None


@pytest.fixture(autouse=True)
def _fresh_observation():
    serving_address.reset_serving_address_for_tests()
    yield
    serving_address.reset_serving_address_for_tests()


def _store(document: dict[str, Any] | None) -> StudioAgentRegistryStore:
    return StudioAgentRegistryStore(_FakeKV(document))  # type: ignore[arg-type]


def test_unconfigured_api_base_follows_the_serving_port() -> None:
    observe_server_address(("127.0.0.1", 8032))
    assert _store(None).get()["api_base"] == "http://127.0.0.1:8032"
    assert _store({"agents": []}).get()["api_base"] == "http://127.0.0.1:8032"


def test_explicit_api_base_always_wins() -> None:
    observe_server_address(("127.0.0.1", 8032))
    stored = {"api_base": "http://10.0.0.2:9000", "agents": []}
    assert _store(stored).get()["api_base"] == "http://10.0.0.2:9000"


def test_prod_default_port_result_is_unchanged() -> None:
    observe_server_address(("127.0.0.1", 8000))
    assert _store(None).get()["api_base"] == DEFAULT_API_BASE


def test_nothing_observed_keeps_the_legacy_constant() -> None:
    assert derived_api_base() is None
    assert _store(None).get()["api_base"] == DEFAULT_API_BASE


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::"])
def test_wildcard_address_maps_to_loopback(wildcard: str) -> None:
    observe_server_address((wildcard, 8040))
    assert derived_api_base() == "http://127.0.0.1:8040"


def test_loopback_observation_wins_over_lan_address() -> None:
    observe_server_address(("192.168.1.20", 8050))
    assert derived_api_base() == "http://192.168.1.20:8050"
    observe_server_address(("127.0.0.1", 8050))
    assert derived_api_base() == "http://127.0.0.1:8050"
    observe_server_address(("192.168.1.30", 8050))
    assert derived_api_base() == "http://127.0.0.1:8050"


def test_ipv6_host_is_bracketed() -> None:
    observe_server_address(("::1", 8060))
    assert derived_api_base() == "http://[::1]:8060"


@pytest.mark.parametrize(
    "server", [("testserver", 80), ("evil.example", 443), None, ("127.0.0.1", 0), ("1.2.3.4",)]
)
def test_non_ip_or_malformed_observations_are_ignored(server) -> None:
    observe_server_address(server)
    assert derived_api_base() is None


def test_middleware_records_scope_server_never_the_host_header() -> None:
    seen: list[str] = []

    async def app(scope, receive, send) -> None:
        seen.append(scope["path"])

    middleware = ServingAddressMiddleware(app)
    scope = {
        "type": "http",
        "path": "/api/health",
        "server": ("127.0.0.1", 8070),
        "headers": [(b"host", b"attacker.example:443")],
    }
    asyncio.run(middleware(scope, None, None))  # type: ignore[arg-type]
    assert seen == ["/api/health"]
    assert derived_api_base() == "http://127.0.0.1:8070"
