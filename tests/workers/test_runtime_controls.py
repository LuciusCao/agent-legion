"""热更 code 并发守卫（worker/runtime_controls.hot_code_concurrency）。

0→>0 热开 code 容量必须要求 velites 二进制可解析，与启动预检同一红线
（EXEC-CODE-003 fail-closed）；service.py 刻意把 max_code_concurrency 排除在
热更字段之外，直接改配置文件不能绕过同一道守卫。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from worker import binary_resolution, runtime_controls

pytestmark = pytest.mark.no_db


@pytest.fixture(autouse=True)
def _isolated_bundled_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把自带二进制目录指向不存在的位置，避免开发机 data/bin 污染测试。"""
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bin")


def test_hot_open_code_capacity_rejected_without_velites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _binary: None)

    assert runtime_controls.hot_code_concurrency(0, 4) == (0, True)


def test_hot_open_code_capacity_allowed_with_velites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")

    assert runtime_controls.hot_code_concurrency(0, 4) == (4, False)


def test_hot_resize_and_close_stay_hot_without_velites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 池已在运行（启动时预检已通过）；缩容/扩容/关闭不重新要求 velites。
    monkeypatch.setattr(shutil, "which", lambda _binary: None)

    assert runtime_controls.hot_code_concurrency(2, 4) == (4, False)
    assert runtime_controls.hot_code_concurrency(2, 0) == (0, False)
