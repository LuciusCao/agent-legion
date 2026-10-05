"""SeaweedFS ``-volume.max`` 必须是显式正值（#746 / #819）。

``-volume.max=0`` 不是「不限」，而是走 SeaweedFS 的自动推导上限：volume
数随 bucket/collection 单调累积，打满后新 bucket 全部写不进去。compose
的 ``weed server`` 启动命令须带显式上限（经 env 可调、默认值为正），
防止回退到 0。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]


def test_compose_weed_server_uses_explicit_positive_volume_max() -> None:
    compose = (ROOT / "deploy" / "compose.host.yaml").read_text(encoding="utf-8")
    commands = [
        line
        for line in compose.splitlines()
        if "weed server" in line and not line.lstrip().startswith("#")
    ]
    assert commands, "compose.host.yaml 缺少 weed server 启动命令"
    for line in commands:
        match = re.search(r"-volume\.max=\$\{AGENT_LEGION_SEAWEEDFS_VOLUME_MAX:-(\d+)\}", line)
        assert match, f"-volume.max 须为显式可配上限: {line.strip()}"
        assert int(match.group(1)) > 0, "默认上限不得为 0（0 = 自动推导）"
