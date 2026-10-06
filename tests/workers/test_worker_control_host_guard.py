"""Worker 本机控制面 Host 头白名单与变更请求来源校验（#923）。

钉住两层：模块级判定（白名单构造、Host 头解析、来源校验）与经 create_app
的端到端行为（页面、静态资产、全部 /api/* 都过白名单；内嵌 token 以
Host 校验启用为前提）。共享件不跨文件 import（见
tests/app/test_pytest_postgres_boundaries.py 的守卫）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from worker.service import create_app
from worker.service_bind import embed_control_token
from worker.service_host_guard import (
    LOOPBACK_HOSTS,
    control_plane_allowed_hosts,
    host_header_name,
    request_rejection,
)
from worker.service_host_names import console_origin, is_loopback_name
from worker.supervisor import WorkerConfigStore

pytestmark = pytest.mark.no_db

_PLACEHOLDER = '<script>window.__WORKER_CONTROL_TOKEN__ = "__WORKER_CONTROL_TOKEN__";</script>'


class _FakeSupervisor:
    def __init__(self, store: WorkerConfigStore) -> None:
        self.store = store
        self.restarts = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def restart(self) -> None:
        self.restarts += 1

    def status(self) -> dict[str, object]:
        return {"service": "running"}


def _app(tmp_path: Path, **kwargs: object) -> tuple[WorkerConfigStore, _FakeSupervisor, object]:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text(_PLACEHOLDER, encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state")
    supervisor = _FakeSupervisor(store)
    return store, supervisor, create_app(supervisor, ui, **kwargs)  # type: ignore[arg-type]


# ---- 模块级判定 ----


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1:8787", "127.0.0.1"),
        ("localhost", "localhost"),
        ("LOCALHOST.:8787", "localhost"),
        ("[::1]:8787", "::1"),
        ("[0:0:0:0:0:0:0:1]", "::1"),
        ("worker.example:8787", "worker.example"),
        ("", None),
        ("attacker@127.0.0.1", None),
        ("127.0.0.1/x", None),
        ("127.0.0.1 :8787", None),
    ],
)
def test_host_header_name_parsing(header: str, expected: str | None) -> None:
    assert host_header_name(header) == expected


def test_allowed_hosts_loopback_exposure_is_loopback_set() -> None:
    assert control_plane_allowed_hosts("127.0.0.1", None, None) == LOOPBACK_HOSTS
    # Docker 默认形态：容器内通配 bind，发布面回环
    assert control_plane_allowed_hosts("0.0.0.0", "127.0.0.1", None) == LOOPBACK_HOSTS
    assert control_plane_allowed_hosts("0.0.0.0", "[::1]", None) == LOOPBACK_HOSTS


def test_allowed_hosts_include_explicit_exposure_and_console_url() -> None:
    hosts = control_plane_allowed_hosts("192.0.2.5", None, "http://worker.example:8787")
    assert hosts == LOOPBACK_HOSTS | {"192.0.2.5", "worker.example"}
    hosts = control_plane_allowed_hosts("0.0.0.0", "[2001:db8::1]", None)
    assert hosts == LOOPBACK_HOSTS | {"2001:db8::1"}


@pytest.mark.parametrize(
    ("bind", "effective"),
    [("0.0.0.0", None), ("::", None), ("0.0.0.0", "0.0.0.0"), ("0.0.0.0", "[::]"), ("0.0.0.0", "")],
)
def test_allowed_hosts_wildcard_exposure_disables_guard(bind: str, effective: str | None) -> None:
    assert control_plane_allowed_hosts(bind, effective, None) is None


def test_non_loopback_host_header_rejected() -> None:
    assert request_rejection("GET", {"host": "attacker.example"}, LOOPBACK_HOSTS)
    assert request_rejection("GET", {}, LOOPBACK_HOSTS)
    assert request_rejection("GET", {"host": "127.0.0.1:8787"}, LOOPBACK_HOSTS) is None


def test_cross_site_mutation_rejected_even_without_host_guard() -> None:
    base = {"host": "192.0.2.5:8787"}
    assert request_rejection("PUT", {**base, "sec-fetch-site": "cross-site"}, None)
    assert request_rejection("POST", {**base, "sec-fetch-site": "same-site"}, None)
    assert request_rejection("POST", {**base, "origin": "http://attacker.example"}, None)
    assert request_rejection("POST", {**base, "origin": "null"}, None)
    # 同源浏览器请求与不带来源头的 CLI 请求放行
    same = {**base, "sec-fetch-site": "same-origin", "origin": "http://192.0.2.5:8787"}
    assert request_rejection("POST", same, None) is None
    assert request_rejection("POST", base, None) is None
    # 安全方法不做来源校验
    assert request_rejection("GET", {**base, "sec-fetch-site": "cross-site"}, None) is None


# ---- 经 create_app 的端到端行为 ----


@pytest.mark.parametrize("path", ["/", "/assets/app.js", "/api/health", "/api/status", "/nope"])
def test_non_loopback_host_rejected_on_every_route(tmp_path: Path, path: str) -> None:
    store, _sup, app = _app(tmp_path)
    headers = {"Authorization": f"Bearer {store.control_token()}"}
    with TestClient(app, base_url="http://attacker.example:8787") as client:  # type: ignore[arg-type]
        response = client.get(path, headers=headers)
    assert response.status_code == 403
    assert store.control_token() not in response.text


@pytest.mark.parametrize("host", ["127.0.0.1:8787", "localhost", "[::1]:8787", "LocalHost:9000"])
def test_loopback_host_variants_served(tmp_path: Path, host: str) -> None:
    store, _sup, app = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:  # type: ignore[arg-type]
        body = client.get("/", headers={"host": host}).text
        health = client.get("/api/health", headers={"host": host})
    assert f'= "{store.control_token()}"' in body
    assert health.status_code == 200


def test_configured_exposure_host_served(tmp_path: Path) -> None:
    allowed = control_plane_allowed_hosts("192.0.2.5", None, None)
    store, _sup, app = _app(tmp_path, embed_token=False, allowed_hosts=allowed)
    with TestClient(app, base_url="http://192.0.2.5:8787") as client:  # type: ignore[arg-type]
        response = client.get("/")
    assert response.status_code == 200
    assert store.control_token() not in response.text


def test_token_not_embedded_without_host_guard(tmp_path: Path) -> None:
    store, _sup, app = _app(tmp_path, embed_token=True, allowed_hosts=None)
    with TestClient(app, base_url="http://192.0.2.5:8787") as client:  # type: ignore[arg-type]
        body = client.get("/").text
    assert store.control_token() not in body
    assert '= "__WORKER_CONTROL_TOKEN__"' in body


def test_cross_site_mutation_rejected_with_valid_token(tmp_path: Path) -> None:
    store, supervisor, app = _app(tmp_path)
    auth = {"Authorization": f"Bearer {store.control_token()}"}
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:  # type: ignore[arg-type]
        cross = client.post("/api/restart", headers={**auth, "Sec-Fetch-Site": "cross-site"})
        foreign = client.post("/api/restart", headers={**auth, "Origin": "http://attacker.example"})
        assert supervisor.restarts == 0
        same = client.post(
            "/api/restart",
            headers={**auth, "Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1:8787"},
        )
        cli = client.post("/api/restart", headers=auth)
    assert cross.status_code == 403
    assert foreign.status_code == 403
    assert same.status_code == 200
    assert cli.status_code == 200
    assert supervisor.restarts == 2


def test_token_not_embedded_when_allowlist_has_non_loopback_host(tmp_path: Path) -> None:
    """白名单含非回环主机名（如控制台地址）时页面不内嵌 token。"""
    allowed = control_plane_allowed_hosts("127.0.0.1", None, "https://worker.example")
    store, _sup, app = _app(tmp_path, embed_token=True, allowed_hosts=allowed)
    with TestClient(app, base_url="http://127.0.0.1") as client:  # type: ignore[arg-type]
        loopback_body = client.get("/").text
        proxied_body = client.get("/", headers={"host": "worker.example"}).text
    assert store.control_token() not in loopback_body
    assert store.control_token() not in proxied_body


def test_unicode_console_host_matches_idna_host_header() -> None:
    hosts = control_plane_allowed_hosts("127.0.0.1", None, "http://例子.测试:8787")
    assert hosts is not None
    assert host_header_name("xn--fsqu00a.xn--0zwm56d:8787") in hosts


def test_console_origin_accepted_for_mutation_behind_host_rewriting_proxy() -> None:
    trusted = console_origin("https://Worker.Example/")
    assert trusted == "https://worker.example"
    upstream = {"host": "127.0.0.1:8787", "sec-fetch-site": "same-origin"}
    ok = {**upstream, "origin": "https://worker.example"}
    assert request_rejection("POST", ok, LOOPBACK_HOSTS, trusted) is None
    other = {**upstream, "origin": "https://attacker.example"}
    assert request_rejection("POST", other, LOOPBACK_HOSTS, trusted)
    assert console_origin("http://[::1]:8787") == "http://[::1]:8787"
    assert console_origin("") is None


@pytest.mark.parametrize(
    ("console_url", "expected"),
    [
        ("https://worker.example:443", "https://worker.example"),
        ("http://worker.example:80/", "http://worker.example"),
        ("HTTPS://Worker.Example:443/console", "https://worker.example"),
        ("http://[::1]:80", "http://[::1]"),
        # 非默认端口（含协议错配的默认端口）照常保留
        ("https://worker.example:80", "https://worker.example:80"),
        ("http://worker.example:443", "http://worker.example:443"),
        ("https://worker.example:8443", "https://worker.example:8443"),
    ],
)
def test_console_origin_omits_scheme_default_port(console_url: str, expected: str) -> None:
    """浏览器 Origin 省略默认端口；显式写 :443 / :80 的控制台地址同样归一（#979）。"""
    assert console_origin(console_url) == expected


def test_explicit_default_port_console_origin_accepted_behind_proxy() -> None:
    trusted = console_origin("https://worker.example:443")
    headers = {
        "host": "127.0.0.1:8787",
        "sec-fetch-site": "same-origin",
        "origin": "https://worker.example",
    }
    assert request_rejection("POST", headers, LOOPBACK_HOSTS, trusted) is None


@pytest.mark.parametrize(
    ("bind", "effective"),
    [("127.0.0.2", None), ("0.0.0.0", "127.0.0.2"), ("127.1.2.3", None), ("::1", None)],
)
def test_token_embedded_for_non_default_loopback_alias(
    tmp_path: Path, bind: str, effective: str | None
) -> None:
    """127/8 回环别名暴露面与 embed_control_token 判定一致，照常内嵌（#976）。"""
    assert embed_control_token(bind, effective)
    allowed = control_plane_allowed_hosts(bind, effective, None)
    store, _sup, app = _app(tmp_path, embed_token=True, allowed_hosts=allowed)
    exposure = effective or bind
    host = f"[{exposure}]" if ":" in exposure else exposure
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:  # type: ignore[arg-type]
        body = client.get("/", headers={"host": f"{host}:8787"}).text
    assert f'= "{store.control_token()}"' in body


@pytest.mark.parametrize(
    ("bind", "effective", "console_url"),
    [
        ("127.0.0.2", None, "https://worker.example"),
        ("0.0.0.0", "192.0.2.5", None),
        ("192.0.2.5", None, None),
        ("127.0.0.2", None, "http://128.0.0.1:8787"),
        ("127.0.0.2", None, "http://localhost.example:8787"),
    ],
)
def test_token_not_embedded_when_any_allowlist_member_is_not_loopback(
    tmp_path: Path, bind: str, effective: str | None, console_url: str | None
) -> None:
    """回环语义只放宽到真正回环：白名单任一非回环成员仍 fail-closed（#976）。"""
    allowed = control_plane_allowed_hosts(bind, effective, console_url)
    store, _sup, app = _app(tmp_path, embed_token=True, allowed_hosts=allowed)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:  # type: ignore[arg-type]
        body = client.get("/").text
    assert store.control_token() not in body


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("127.0.0.2", True),
        ("[::1]", True),
        ("LOCALHOST.", True),
        ("128.0.0.1", False),
        ("localhost.example", False),
        ("worker.example", False),
        ("0.0.0.0", False),
    ],
)
def test_is_loopback_name(name: str, expected: bool) -> None:
    assert is_loopback_name(name) is expected
