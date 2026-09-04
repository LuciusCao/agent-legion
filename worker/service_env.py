"""worker 出网代理的进程环境治理（#444）。

service 入口剥离启动 shell 继承的代理 env（默认直连——本机代理进程的
订阅刷新/配置重载会整批掐断在途 LLM 长流，数百路并发下放大为分钟级
失败窗口）；确需代理出口的部署在 worker.yaml 显式配置 ``proxy:`` 字段。
剥离与注入共用同一个变量集合，语义对称。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# 小写/大写成对列出：reqwest 认小写，requests/部分库认大写。
PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


def strip_proxy_env() -> None:
    """service 入口剥离继承的代理 env；需要代理出口时在 worker.yaml 配置 proxy。"""
    stripped = [name for name in PROXY_ENV_VARS if name in os.environ]
    for name in stripped:
        del os.environ[name]
    if stripped:
        logger.info(
            "已剥离继承的代理环境变量（默认直连；如需代理请在配置中设置 proxy）: %s",
            ", ".join(stripped),
        )


def proxy_env_overrides(proxy: object) -> dict[str, str]:
    """配置声明的代理 → 全部代理 env 变量；空值返回空 dict（不注入）。"""
    url = str(proxy or "").strip()
    if not url:
        return {}
    logger.info("按配置注入出网代理（executor 与 agent 子进程经代理出网）: %s", url)
    return dict.fromkeys(PROXY_ENV_VARS, url)
