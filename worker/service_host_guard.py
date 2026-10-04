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
一致或等于控制台地址的 origin（反向代理改写上游 Host 的形态）。CLI（workerctl）
不发这两个头，不受影响。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from worker.service_host_names import console_origin, normalize_host

logger = logging.getLogger(__name__)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
# 合法 Host 头：主机名或 IPv4 / 方括号 IPv6，可带端口；含 userinfo、路径、
# 空白等任何其它字符一律视为不合法（解析歧义即拒绝）。
_HOST_HEADER = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+)(:[0-9]{1,5})?$")


def _is_wildcard(host: str) -> bool:
    # normalize_host 已把 IP 字面量折成规范形态（0:0::0 → ::）
    return normalize_host(host) in ("", "0.0.0.0", "::")


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
    method: str,
    headers: Mapping[str, str],
    allowed_hosts: frozenset[str] | None,
    trusted_origin: str | None = None,
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
        same_origin = bool(origin_netloc) and origin_netloc == host.strip().lower()
        # 反向代理改写上游 Host 时，浏览器 Origin 是配置的控制台地址
        if not same_origin and origin.strip().lower().rstrip("/") != trusted_origin:
            return "origin mismatch"
    return None


def guard_options(bind_host: str, effective_host: str | None, console_url: str) -> dict[str, Any]:
    """service.main 传给 create_app 的 Host 白名单与可信控制台 origin。"""
    return {
        "allowed_hosts": control_plane_allowed_hosts(bind_host, effective_host, console_url),
        "trusted_origin": console_origin(console_url),
    }


def install_host_guard(
    app: FastAPI,
    allowed_hosts: frozenset[str] | None,
    trusted_origin: str | None,
    embed_token: bool,
) -> bool:
    """在 app 全部路由前挂 Host / 来源校验（中间件覆盖未匹配路由与 404）。

    返回收紧后的 token 内嵌判定：Host 校验已启用（None = 通配暴露面）且白名单
    只含回环名——任一非回环主机名（暴露面或控制台地址）都意味着页面可经
    非本机入口打开，此时不内嵌。
    """

    @app.middleware("http")
    async def _host_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        reason = request_rejection(request.method, request.headers, allowed_hosts, trusted_origin)
        if reason is not None:
            return JSONResponse({"detail": reason}, status_code=403)
        return await call_next(request)

    return embed_token and allowed_hosts is not None and allowed_hosts <= LOOPBACK_HOSTS
