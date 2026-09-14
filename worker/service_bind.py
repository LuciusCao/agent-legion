"""Bind-address hardening for the local Worker control service."""

from __future__ import annotations

import ipaddress
import logging

logger = logging.getLogger(__name__)


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def embed_control_token(host: str, effective_host: str | None = None) -> bool:
    """是否把控制 token 内嵌进控制台页面。

    判定输入是**暴露面**而非进程 bind（issue #489）：token 内嵌的风险在于
    「能打开页面的浏览器就拿到 token」，因此看的是页面实际从哪个地址被
    访问——

    - 裸机/dev 形态：进程绑哪个地址，暴露面就是哪个地址（``effective_host``
      为 ``None``，直接按 ``host`` 判定——行为与历史版本完全一致）；
    - Docker 形态：容器内必绑 ``0.0.0.0``（端口映射前提），真实暴露面由
      compose 的宿主侧发布地址决定，经 ``AGENT_WORKER_UI_EFFECTIVE_BIND``
      传入（effective_host）。宿主发布回环（``127.0.0.1`` 等）时页面仅
      本机可达，内嵌不扩大风险面（进程 bind 的非回环只是端口映射前提，
      用 effective 覆盖判定）；发布非回环（如 ``0.0.0.0`` / ``192.0.2.1``）
      时同网段浏览器都能打开页面，token 不内嵌 + warning，需手动输入。

    进程 bind 非回环但暴露面回环时打 info 说明判定链，供运维核对。
    """
    host_loopback = _is_loopback(host)
    effective_loopback = _is_loopback(effective_host) if effective_host is not None else True
    # 暴露面取 effective；effective 未设置时回落进程 bind（None 分支把
    # host 的判定结果代入，保持历史语义逐字节等价）。
    embed = effective_loopback if effective_host is not None else host_loopback

    if not effective_loopback:
        logger.warning(
            "Worker 控制面宿主侧发布地址非回环（进程绑定 %s，发布地址 %s）："
            "页面不再内嵌控制 token，需在页面手动输入",
            host,
            effective_host,
        )
    elif not host_loopback:
        if effective_host is not None:
            logger.info(
                "Worker 控制面进程绑定非回环地址 %s，但宿主侧发布地址为回环 %s："
                "页面仅本机可达，控制 token 照常内嵌",
                host,
                effective_host,
            )
        else:
            # 未传 effective（裸机/dev 形态）却绑定非回环：与历史版本同级的
            # warning——此时按进程 bind 判定为不内嵌，不该比旧版更安静。
            logger.warning(
                "Worker 控制面绑定到非回环地址 %s：页面不再内嵌控制 token，需在页面手动输入",
                host,
            )
    return embed
