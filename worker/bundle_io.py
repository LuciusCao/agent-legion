"""Shared bundle/artifact IO for Worker execution preparation.

The tar-bundle extraction lives here; the input-artifact download channel
(``download_input_artifacts`` / ``sha256_file``) split to
``worker.artifact.inputs`` for the file-size budget and is re-exported so
existing import paths keep working.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, cast

from worker.artifact.inputs import download_input_artifacts, sha256_file

__all__ = [
    "MAX_BUNDLE_MEMBERS",
    "MAX_BUNDLE_UNPACKED_BYTES",
    "BundleLimitExceeded",
    "download_input_artifacts",
    "safe_extract",
    "safe_extract_tree",
    "sha256_file",
]

# #967 解压炸弹面：bundle 来自 Host（skill 仓库 git archive 导出 + manifest，
# code 车道是 node.py + workspace_libs），下载侧只有压缩体积、没有解压后
# 总量的约束——高压缩比归档（全零大文件 / 海量空成员）可在 extractall 时
# 撑爆磁盘，或在成员表上撑爆内存。两条上限都远高于正常 bundle（skill 与
# libs 是源码级目录，常见几十到几千个成员、MB 级），只拦病态归档：成员数
# 2 万（成员表内存 ≈ 每成员 KB 级）、解压总量 1 GiB（按常规文件成员头里
# 声明的 size 累加——tarfile 按 size 写出字节，头部声明即实际落盘量）。
MAX_BUNDLE_MEMBERS = 20_000
MAX_BUNDLE_UNPACKED_BYTES = 1024 * 1024 * 1024


class BundleLimitExceeded(ValueError):
    """#967：bundle 成员数或解压总量超上限，拒绝解压。

    继承 ValueError（与 unsafe member 拒绝同族）：执行准备的遏制边界
    （worker/execution/run.py、code_runner 经同一边界）把它转为一次显式
    failed 结果上报，错误文本随 error_message 交给 Host。"""


def safe_extract_tree(archive: Path, destination: Path) -> None:
    """Extract a tar.gz bundle, rejecting absolute/parent/link members and
    bundles over the member-count / unpacked-size ceilings (#967).

    成员头逐个流式读取（不先 getmembers 整表入内存），任何一条上限越线
    立即拒绝——在 extractall 写出第一个字节之前。"""
    with tarfile.open(archive, "r:gz") as tar:
        members: list[tarfile.TarInfo] = []
        unpacked_bytes = 0
        for member in tar:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.islnk() or member.issym():
                raise ValueError(f"unsafe Agent bundle member: {member.name!r}")
            members.append(member)
            if len(members) > MAX_BUNDLE_MEMBERS:
                raise BundleLimitExceeded(
                    f"Agent bundle has more than {MAX_BUNDLE_MEMBERS} members; refusing to extract"
                )
            if member.isfile():
                unpacked_bytes += member.size
            if unpacked_bytes > MAX_BUNDLE_UNPACKED_BYTES:
                raise BundleLimitExceeded(
                    f"Agent bundle unpacks to more than {MAX_BUNDLE_UNPACKED_BYTES} bytes;"
                    f" refusing to extract"
                )
        tar.extractall(destination, members=members, filter="data")


def safe_extract(archive: Path, destination: Path) -> dict[str, Any]:
    """Agent bundles carry a manifest.json; code bundles do not."""
    safe_extract_tree(archive, destination)
    return cast(dict[str, Any], json.loads((destination / "manifest.json").read_text()))
