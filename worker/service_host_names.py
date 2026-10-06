"""Worker 控制面的主机名归一与控制台 origin（#923）。

`normalize_host` 是 Host 白名单两侧（配置值与请求 Host 头）的唯一归一入口；
`is_loopback_name` 是 token 内嵌收紧的回环判定；
`console_origin` 给出 `AGENT_WORKER_CONSOLE_URL` 的 origin——反向代理改写上游
Host 时，浏览器变更请求的 `Origin` 是该对外地址而非上游 Host。从
`worker/service_host_guard.py` 拆出（体积预算）。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_host(value: str) -> str:
    """主机名归一：小写、去尾点与 IPv6 方括号、IP 字面量取规范形态。"""
    host = value.strip().lower().rstrip(".")
    if len(host) >= 2 and host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    # 浏览器发送的 Host 是 IDNA ASCII 形态，Unicode 主机名按同一形态入白名单
    try:
        return host if host.isascii() else host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def is_loopback_name(value: str) -> bool:
    """主机名是否回环（`localhost` 或任一回环 IP，含 127/8 全段）。

    与 `worker/service_bind.py::embed_control_token` 的回环语义一致（#976）。
    """
    host = normalize_host(value)
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def console_origin(console_url: str | None) -> str | None:
    """控制台地址的 origin（scheme://host[:port]，IDNA 归一）；无法解析返回 None。"""
    if not console_url:
        return None
    try:
        parts = urlsplit(console_url.strip())
        hostname, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not parts.scheme or not hostname:
        return None
    host = normalize_host(hostname)
    if ":" in host:
        host = f"[{host}]"
    scheme = parts.scheme.lower()
    # 浏览器 Origin 省略协议默认端口（#979）：显式写 :80 / :443 的配置同样省略，否则与之比对不上
    if _DEFAULT_PORTS.get(scheme) == port:
        port = None
    return f"{scheme}://{host}" + (f":{port}" if port is not None else "")
