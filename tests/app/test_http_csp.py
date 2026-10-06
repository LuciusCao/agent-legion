"""Document Content-Security-Policy on served HTML (#752, nonce #989)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from server.app.configuration.csp import load_csp_settings
from server.app.configuration.env_overrides import _bool_parser
from server.app.http_csp import (
    ContentSecurityPolicyMiddleware,
    build_spa_csp,
    object_store_connect_sources,
)
from server.app.http_csp_nonce import CSP_NONCE_PLACEHOLDER, issue_csp_nonce
from server.app.settings import load_settings
from server.app.storage.s3_settings import S3Settings
from tests.helpers import setup_spa_app


def _directives(policy: str) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for directive in policy.split(";"):
        name, *sources = directive.split()
        parsed[name] = sources
    return parsed


def test_spa_documents_carry_csp_and_other_responses_do_not(tmp_path, monkeypatch):
    from server.app import main

    root_dir, data_dir = setup_spa_app(tmp_path, monkeypatch)
    frontend_dist = root_dir / "frontend" / "dist"
    (frontend_dist / "assets").mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<div>spa-index</div>", encoding="utf-8")
    (frontend_dist / "assets" / "main.js").write_text("console.log(1)", encoding="utf-8")

    app = main.create_app(data_dir=data_dir, start_worker=False)
    with TestClient(app) as client:
        for path in ("/", "/some/client/route"):
            response = client.get(path)
            assert response.status_code == 200
            policy = _directives(response.headers["content-security-policy"])
            assert policy["default-src"] == ["'self'"]
            assert policy["object-src"] == ["'none'"]
            assert policy["frame-ancestors"] == ["'self'"]
            assert "'self'" in policy["connect-src"]
            # A dist without vite's nonce placeholder gets no nonce at all.
            assert policy["script-src"] == ["'self'"]
        assert "content-security-policy" not in client.get("/assets/main.js").headers
        assert "content-security-policy" not in client.get("/api/health").headers
        # FastAPI's built-in API docs pages keep their own CDN-loaded UI.
        for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.headers["content-type"].startswith("text/html")
            assert "content-security-policy" not in response.headers, path
        # Only those exact endpoints: other paths under the prefixes are the
        # SPA catch-all and keep the SPA policy.
        for path in ("/docs/foo", "/redoc/x", "/docs/"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.text == "<div>spa-index</div>", path
            assert "default-src 'self'" in response.headers["content-security-policy"], path


def test_index_nonce_matches_header_and_rotates_per_response(tmp_path, monkeypatch):
    """#989: the placeholder vite writes is swapped for the header's nonce."""
    from server.app import main

    root_dir, data_dir = setup_spa_app(tmp_path, monkeypatch)
    frontend_dist = root_dir / "frontend" / "dist"
    (frontend_dist / "assets").mkdir(parents=True)
    (frontend_dist / "index.html").write_text(
        f'<meta property="csp-nonce" nonce="{CSP_NONCE_PLACEHOLDER}">'
        f'<script type="module" src="/assets/i.js" nonce="{CSP_NONCE_PLACEHOLDER}"></script>',
        encoding="utf-8",
    )

    app = main.create_app(data_dir=data_dir, start_worker=False)
    seen: set[str] = set()
    with TestClient(app) as client:
        for path in ("/", "/jobs/x", "/index.html", "/../index.html"):
            response = client.get(path)
            assert response.status_code == 200, path
            script_src = _directives(response.headers["content-security-policy"])["script-src"]
            assert script_src[0] == "'self'" and len(script_src) == 2, path
            nonce = re.fullmatch(r"'nonce-([A-Za-z0-9_-]{20,})'", script_src[1]).group(1)
            assert CSP_NONCE_PLACEHOLDER not in response.text
            assert response.text.count(f'nonce="{nonce}"') == 2, path
            # Per-response body: no validator, so no 304 can pair a cached
            # body's old nonce with a fresh header.
            assert "etag" not in response.headers and "last-modified" not in response.headers
            assert response.headers["cache-control"] == "no-cache"
            seen.add(nonce)
    assert len(seen) == 4


def _probe_app(script_unsafe_inline: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/page", response_class=HTMLResponse)
    def page(request: Request) -> str:
        return issue_csp_nonce(request.scope) + "|" + issue_csp_nonce(request.scope)

    @app.get("/plain", response_class=HTMLResponse)
    def plain() -> str:
        return "<p>no nonce</p>"

    app.add_middleware(ContentSecurityPolicyMiddleware, script_unsafe_inline=script_unsafe_inline)
    return app


@pytest.mark.no_db
def test_middleware_puts_the_issued_nonce_in_the_header() -> None:
    with TestClient(_probe_app(script_unsafe_inline=False)) as client:
        response = client.get("/page")
        first, second = response.text.split("|")
        # One nonce per response, however often the endpoint asks.
        assert first == second
        policy = response.headers["content-security-policy"]
        assert _directives(policy)["script-src"] == ["'self'", f"'nonce-{first}'"]
        assert "'unsafe-inline'" not in _directives(policy)["script-src"]
        assert _directives(client.get("/plain").headers["content-security-policy"])[
            "script-src"
        ] == ["'self'"]


@pytest.mark.no_db
def test_instance_switch_rolls_script_src_back_to_unsafe_inline() -> None:
    """Compat mode leaves the nonce out: its presence disables 'unsafe-inline'."""
    with TestClient(_probe_app(script_unsafe_inline=True)) as client:
        response = client.get("/page")
        script_src = _directives(response.headers["content-security-policy"])["script-src"]
        assert script_src == ["'self'", "'unsafe-inline'"]


def test_csp_switch_env_drives_settings(tmp_path, monkeypatch):
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_LEGION_CSP_SCRIPT_UNSAFE_INLINE", raising=False)
    assert (
        load_settings(data_dir=tmp_path / "data", config_path=config_path).csp.script_unsafe_inline
        is False
    )
    monkeypatch.setenv("AGENT_LEGION_CSP_SCRIPT_UNSAFE_INLINE", "1")
    assert (
        load_settings(data_dir=tmp_path / "data", config_path=config_path).csp.script_unsafe_inline
        is True
    )


@pytest.mark.no_db
def test_docker_host_stack_passes_the_csp_switch_through() -> None:
    """compose's deploy/.env only feeds interpolation: the Host container sees
    the switch only if compose.host.yaml forwards it, and the unset default
    must still parse as a boolean (an empty string fails the settings load)."""
    repo = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load((repo / "deploy/compose.host.yaml").read_text(encoding="utf-8"))
    value = doc["services"]["host"]["environment"]["AGENT_LEGION_CSP_SCRIPT_UNSAFE_INLINE"]
    match = re.fullmatch(r"\$\{AGENT_LEGION_CSP_SCRIPT_UNSAFE_INLINE:-(\w+)\}", value)
    assert match is not None, value
    assert load_csp_settings(
        {"server": {"csp": {"script_unsafe_inline": _bool_parser(match.group(1))}}}
    ) == load_csp_settings({})


@pytest.mark.no_db
def test_csp_settings_reject_non_boolean_switch() -> None:
    with pytest.raises(ValueError, match="script_unsafe_inline must be a boolean"):
        load_csp_settings({"server": {"csp": {"script_unsafe_inline": "yes"}}})


@pytest.mark.no_db
def test_nonce_placeholder_matches_vite_config_and_panel_host() -> None:
    """The literal is shared by three files; drift would silently drop the nonce."""
    repo = Path(__file__).resolve().parents[2]
    for relative in ("frontend/vite.config.ts", "frontend/src/features/previewPanel/panelCsp.ts"):
        assert f"'{CSP_NONCE_PLACEHOLDER}'" in (repo / relative).read_text(encoding="utf-8"), (
            relative
        )


@pytest.mark.no_db
def test_policy_keeps_what_the_shipped_frontend_loads() -> None:
    policy = _directives(build_spa_csp(script_nonce="abc"))
    # srcdoc preview panels inherit this policy: their inline scripts run on
    # the stamped nonce, never on 'unsafe-inline' (#989).
    assert policy["script-src"] == ["'self'", "'nonce-abc'"]
    assert "'unsafe-inline'" in policy["style-src"]
    assert "https://fonts.googleapis.com" in policy["style-src"]
    assert {"data:", "https://fonts.gstatic.com"} <= set(policy["font-src"])
    assert {"data:", "blob:", "https:"} <= set(policy["img-src"])


@pytest.mark.no_db
def test_connect_src_lists_request_host_sockets_only_for_plain_hosts() -> None:
    plain = _directives(build_spa_csp(host="legion.example:8443"))
    assert "wss://legion.example:8443" in plain["connect-src"]
    smuggled = _directives(build_spa_csp(host="evil; script-src *"))
    assert smuggled["connect-src"] == ["'self'"]
    assert smuggled["script-src"] == ["'self'"]


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (None, ()),
        (S3Settings(bucket="b"), ("https://*.amazonaws.com",)),
        (S3Settings(bucket="b", region="cn-north-1"), ("https://*.amazonaws.com.cn",)),
        (S3Settings(bucket="b", endpoint_url="http://seaweed:8333"), ("http://seaweed:8333",)),
        (
            S3Settings(
                bucket="b",
                endpoint_url="http://seaweed:8333",
                public_endpoint_url="https://s3.example.com/prefix",
            ),
            ("https://s3.example.com",),
        ),
        (S3Settings(bucket="b", endpoint_url="not a url"), ()),
    ],
)
def test_object_store_presign_origin(settings, expected) -> None:
    assert object_store_connect_sources(settings) == expected
