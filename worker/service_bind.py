"""Bind-address hardening for the local Worker control service."""

from __future__ import annotations

import ipaddress
import logging

logger = logging.getLogger(__name__)


def embed_control_token(host: str) -> bool:
    """仅回环绑定才把控制 token 内嵌进页面；非回环绑定打 warning 并要求手动输入。"""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        logger.warning(
            "Worker 控制面绑定到非回环地址 %s：页面不再内嵌控制 token，需在页面手动输入",
            host,
        )
    return loopback
