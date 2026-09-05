"""worker.yaml `proxy:` 字段的校验（#444）。

独立于 service_env（进程环境操作）以守住各自的行数预算；允许 userinfo
（带认证的代理常见）与 socks5/socks5h（DNS 经代理），拒绝查询参数与锚点。
"""

from __future__ import annotations

import urllib.parse

_SCHEMES = {"http", "https", "socks5", "socks5h"}


def validate_proxy(value: object) -> str:
    """校验并归一化 proxy 字段；空串 = 不代理（默认直连）。"""
    if value is None:
        return ""
    url = str(value).strip()
    if url:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in _SCHEMES or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError(
                "proxy 必须是 http/https/socks5(h) URL（可含认证信息，不能带查询参数或锚点）"
            )
    return url


def redact_proxy_url(url: str) -> str:
    """脱敏代理 URL：认证信息（userinfo）不落日志/控制台。"""
    if "@" in url:
        scheme, sep, rest = url.partition("://")
        if sep:
            _, at, host = rest.rpartition("@")
            if at:
                return f"{scheme}://{host}"
    return url
