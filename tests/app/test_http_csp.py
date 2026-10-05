"""Document Content-Security-Policy on served HTML (#752)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.http_csp import build_spa_csp, object_store_connect_sources
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


@pytest.mark.no_db
def test_policy_keeps_what_the_shipped_frontend_loads() -> None:
    policy = _directives(build_spa_csp())
    # srcdoc preview panels inherit this policy and are inline-script bundles.
    assert "'unsafe-inline'" in policy["script-src"]
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
    assert smuggled["script-src"] == ["'self'", "'unsafe-inline'"]


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
