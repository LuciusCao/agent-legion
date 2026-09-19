"""Worker 控制台自报地址：主控制台按 Worker 逐个显示「控制台」入口的数据源。

Worker Service 知道自己的控制面绑定地址（``--host/--port``），executor 子进程
不知道；service 启动时把推导出的地址写进环境变量 ``AGENT_WORKER_CONSOLE_URL``
（supervisor 让子进程继承 ``os.environ``），executor 注册时把它注入
``labels`` 的保留键 ``console_url``——零协议/schema 变更，旧版 Host 与旧版
Worker 互不影响（缺键 = 不显示入口）。

部署侧显式设置 ``AGENT_WORKER_CONSOLE_URL`` 时以它为准（反向代理、docker
端口映射到非回环地址等场景）；显式空串 = 不上报。
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

CONSOLE_URL_ENV = "AGENT_WORKER_CONSOLE_URL"
CONSOLE_URL_LABEL = "console_url"


def derive_console_url(host: str, port: int) -> str:
    """从控制面绑定地址推导浏览器可用的控制台地址。

    通配绑定（0.0.0.0 / ::）对浏览器无意义，回落 127.0.0.1（与 docker 默认
    端口发布 127.0.0.1:8787 一致）；IPv6 字面量补方括号；主机名原样保留。"""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return f"http://{host}:{port}"
    if address.is_unspecified:
        return f"http://127.0.0.1:{port}"
    if address.version == 6:
        return f"http://[{host}]:{port}"
    return f"http://{host}:{port}"


def resolve_console_url(host: str, port: int, environ: Mapping[str, str]) -> str:
    """环境显式给值（含空串 = 不上报）优先于绑定地址推导。"""
    override = environ.get(CONSOLE_URL_ENV)
    if override is not None:
        return override.strip()
    return derive_console_url(host, port)


def with_console_label(labels: Mapping[str, Any] | None, console_url: str) -> dict[str, Any]:
    """注册 labels 里注入保留键 ``console_url``。

    用户在 worker.yaml 自定义的 labels 原样保留；地址为空（显式禁用）时不
    注入也不删除用户自己写的同名键。"""
    merged = dict(labels or {})
    if console_url:
        merged[CONSOLE_URL_LABEL] = console_url
    return merged


def registration_config(config: Mapping[str, Any], environ: Mapping[str, str]) -> dict[str, Any]:
    """注册用的配置副本：labels 带上 service 经环境传来的控制台地址。"""
    prepared = dict(config)
    prepared["labels"] = with_console_label(
        config.get("labels"), environ.get(CONSOLE_URL_ENV, "").strip()
    )
    return prepared
