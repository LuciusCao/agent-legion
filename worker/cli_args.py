"""Argument parsing for workerctl."""

import argparse
import os
from pathlib import Path

try:
    from agent_worker_cli_payload import configure_payload
except ModuleNotFoundError:  # worker 包内导入；独立 workerctl 使用上方同级模块。
    from worker.cli_payload import configure_payload as configure_payload

# Worker 状态目录的环境变量名与本地默认值（#1106）：workerctl 与
# worker.service 的 --state-dir 默认值共用这一来源。与 Makefile 的
# AGENT_WORKER_STATE_DIR 同名；镜像 worker 阶段经 Dockerfile ENV 设为
# 容器内状态卷路径，使 `workerctl status` 在容器内无参数即可读到控制令牌。
STATE_DIR_ENV = "AGENT_WORKER_STATE_DIR"
LOCAL_STATE_DIR = "data/agent-worker-service"


def default_state_dir() -> Path:
    """--state-dir 默认值：AGENT_WORKER_STATE_DIR（非空时），否则本地默认。

    在 build_parser 调用时求值（而非 import 时），测试可 monkeypatch 环境。
    """
    return Path(os.environ.get(STATE_DIR_ENV) or LOCAL_STATE_DIR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workerctl", description="管理本机 Agent Worker")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--token", help="控制令牌，默认读 state dir 或 AGENT_WORKER_CONTROL_TOKEN")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=default_state_dir(),
        help=f"Worker 状态目录，默认读 {STATE_DIR_ENV}，未设置时为 {LOCAL_STATE_DIR}",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="查看进程、Host 和登记状态")
    commands.add_parser("config", help="查看可编辑配置")
    commands.add_parser("restart", help="重启 Worker 执行进程")
    logs = commands.add_parser("logs", help="查看最近日志")
    logs.add_argument("--limit", type=int, default=100)
    claim = commands.add_parser("claim", help="查看、开启或关闭新任务领取")
    claim.add_argument("action", choices=["status", "enable", "disable"])
    capacity = commands.add_parser("capacity", help="查看或热更新动态容量")
    capacity.add_argument("value", nargs="?", type=int)
    configure = commands.add_parser("configure", help="保存并应用配置（仅更新显式字段）")
    configure.add_argument("--host-url")
    configure.add_argument("--worker-id")
    configure.add_argument("--name")
    configure.add_argument(
        "--disable-runtime",
        action="append",
        choices=["pi", "velites"],
        help="停用某个已安装的 agent runtime（反选；默认全部探测到的都启用）",
    )
    configure.add_argument("--max-concurrency", type=int)
    configure.add_argument("--upload-concurrency", type=int)
    configure.add_argument("--claim-enabled", action=argparse.BooleanOptionalAction, default=None)
    configure.add_argument(
        "--proxy",
        help="出网代理 URL（http(s)/socks5(h)，可含认证）；传空串清除回直连，缺省不动现值",
    )
    configure.add_argument("--model", action="append", default=[])
    configure.add_argument("--label", action="append", default=[])
    configure.add_argument(
        "--register-token-file",
        type=Path,
        help="从文件读取 Host 签发的注册 token，避免密钥出现在进程参数中",
    )
    return parser
