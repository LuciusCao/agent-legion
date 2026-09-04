"""worker.service 的进程环境治理（#444）。

启动入口在此剥离代理 env：worker.service 从启动 shell 继承的
http_proxy/https_proxy/all_proxy 会让 velites（reqwest 默认读代理 env）
把全部 LLM 流量绕经本机代理进程（Clash/mihomo 等），代理自身的配置
重载/订阅刷新会整批掐断在途流。executor 与 agent 子进程全量继承本进程
环境，因此在 main() 入口（任何子进程派生之前）剥离一次即可覆盖全部
下游。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# 小写/大写成对列出：reqwest 认小写，部分库只认大写。
_PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


def strip_proxy_env() -> None:
    """进程入口剥离代理 env；显式设置 WORKER_KEEP_PROXY_ENV=1 可保留。

    保留开关面向远程部署确需代理出口的场景——显式选择优于隐式继承。
    """
    if os.environ.get("WORKER_KEEP_PROXY_ENV") == "1":
        logger.info("WORKER_KEEP_PROXY_ENV=1，保留代理环境变量（显式选择）")
        return
    stripped = [name for name in _PROXY_ENV_VARS if name in os.environ]
    if stripped:
        for name in stripped:
            del os.environ[name]
        logger.info("已剥离代理环境变量（agent 执行直连，不走本机代理）: %s", ", ".join(stripped))
