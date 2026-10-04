"""Worker 本机控制面的 Host 头白名单与变更请求来源校验（#923）。

控制面全部路由（`GET /`、`/assets/*` 与全部 `/api/*`）先过 Host 头白名单：
回环变体（`127.0.0.1` / `localhost` / `[::1]`，任意端口）∪ 实际暴露面地址
（`AGENT_WORKER_UI_EFFECTIVE_BIND`，未设置时取进程 bind）∪ 控制台地址
（`AGENT_WORKER_CONSOLE_URL` 的主机名），其余一律 403。暴露面是通配地址
（`0.0.0.0` / `::`）时无法枚举合法主机名，白名单不启用——此时控制 token
也不内嵌页面（`create_app` 把「Host 校验已启用」作为内嵌前提），API 仍由
bearer token 把守。

变更类请求（非 GET/HEAD/OPTIONS）另做纵深校验：带 `Sec-Fetch-Site` 时只
放行 `same-origin` / `none`，带 `Origin` 时其 host[:port] 必须与 Host 头
一致。CLI（workerctl）不发这两个头，不受影响。
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
# 合法 Host 头：主机名或 IPv4 / 方括号 IPv6，可带端口；含 userinfo、路径、
# 空白等任何其它字符一律视为不合法（解析歧义即拒绝）。
_HOST_HEADER = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+)(:[0-9]{1,5})?$")


def normalize_host(value: str) -> str:
    """主机名归一：小写、去尾点与 IPv6 方括号、IP 字面量取规范形态。"""
    host = value.strip().lower().rstrip(".")
    if len(host) >= 2 and host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    if not host.isascii():
        # 浏览器发送的 Host 是 IDNA ASCII 形态，Unicode 主机名按同一形态入白名单
        try:
            return host.encode("idna").decode("ascii")
        except UnicodeError:
            return host
    return host


def _is_wildcard(host: str) -> bool:
    normalized = normalize_host(host)
    if not normalized:
        return True
    try:
        return ipaddress.ip_address(normalized).is_unspecified
    except ValueError:
        return False


def host_header_name(header: str) -> str | None:
    """从 Host 头取主机名（去端口、归一）；形态不合法返回 None。"""
    match = _HOST_HEADER.match(header.strip())
    return normalize_host(match.group(1)) if match else None


def control_plane_allowed_hosts(
    bind_host: str, effective_host: str | None, console_url: str | None
) -> frozenset[str] | None:
    """控制面 Host 白名单；暴露面为通配地址时返回 None（校验不启用）。"""
    exposure = effective_host if effective_host is not None else bind_host
    if _is_wildcard(exposure):
        logger.warning(
            "Worker 控制面暴露面为通配地址 %s：无法枚举合法 Host，Host 头校验不启用，"
            "控制 token 不内嵌页面",
            exposure,
        )
        return None
    names = set(LOOPBACK_HOSTS) | {normalize_host(exposure)}
    if not _is_wildcard(bind_host):
        names.add(normalize_host(bind_host))
    console_host = urlsplit(console_url).hostname if console_url else None
    if console_host:
        names.add(normalize_host(console_host))
    return frozenset(names)


def request_rejection(
    method: str, headers: Mapping[str, str], allowed_hosts: frozenset[str] | None
) -> str | None:
    """返回拒绝原因；放行返回 None。"""
    host = headers.get("host", "")
    if allowed_hosts is not None and host_header_name(host) not in allowed_hosts:
        return "host not allowed"
    if method.upper() in _SAFE_METHODS:
        return None
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site.strip().lower() not in _ALLOWED_FETCH_SITES:
        return "cross-site request rejected"
    origin = headers.get("origin")
    if origin is not None:
        try:
            origin_netloc = urlsplit(origin.strip()).netloc.lower()
        except ValueError:
            return "origin mismatch"
        if not origin_netloc or origin_netloc != host.strip().lower():
            return "origin mismatch"
    return None


def install_host_guard(app: FastAPI, allowed_hosts: frozenset[str] | None) -> None:
    """在 app 全部路由前挂 Host / 来源校验（中间件覆盖未匹配路由与 404）。"""

    @app.middleware("http")
    async def _host_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        reason = request_rejection(request.method, request.headers, allowed_hosts)
        if reason is not None:
            return JSONResponse({"detail": reason}, status_code=403)
        return await call_next(request)
