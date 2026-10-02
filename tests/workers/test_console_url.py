"""Worker 控制台自报地址（worker/console_url.py）：推导、env 覆盖、labels 注入。"""

from __future__ import annotations

import pytest

from worker.console_url import (
    CONSOLE_URL_ENV,
    CONSOLE_URL_LABEL,
    derive_console_url,
    registration_config,
    resolve_console_url,
    with_console_label,
)

pytestmark = pytest.mark.no_db


def test_derive_console_url_from_bind_address() -> None:
    assert derive_console_url("127.0.0.1", 8789) == "http://127.0.0.1:8789"
    assert derive_console_url("10.0.0.8", 8787) == "http://10.0.0.8:8787"
    assert derive_console_url("localhost", 8787) == "http://localhost:8787"
    # 通配绑定对浏览器无意义：回落 loopback（与 docker 默认端口发布一致）。
    assert derive_console_url("0.0.0.0", 8787) == "http://127.0.0.1:8787"
    assert derive_console_url("::", 8787) == "http://[::1]:8787"
    assert derive_console_url("::1", 8787) == "http://[::1]:8787"


def test_resolve_console_url_prefers_explicit_env() -> None:
    assert resolve_console_url("0.0.0.0", 8787, {}) == "http://127.0.0.1:8787"
    assert (
        resolve_console_url("0.0.0.0", 8787, {CONSOLE_URL_ENV: " https://worker.example/console "})
        == "https://worker.example/console"
    )
    # 显式空串 = 不上报。
    assert resolve_console_url("127.0.0.1", 8789, {CONSOLE_URL_ENV: ""}) == ""


def test_with_console_label_keeps_operator_labels() -> None:
    labels = {"site": "office", "gpu": "none"}

    merged = with_console_label(labels, "http://127.0.0.1:8789")

    assert merged == {"site": "office", "gpu": "none", CONSOLE_URL_LABEL: "http://127.0.0.1:8789"}
    assert labels == {"site": "office", "gpu": "none"}, "input mapping must not be mutated"


def test_with_console_label_without_address_leaves_labels_alone() -> None:
    # 显式禁用时既不注入，也不删用户自己写的同名键。
    assert with_console_label(None, "") == {}
    assert with_console_label({CONSOLE_URL_LABEL: "http://manual:1"}, "") == {
        CONSOLE_URL_LABEL: "http://manual:1"
    }


def test_registration_config_injects_label_from_env_without_touching_runtime_config() -> None:
    config = {"worker_id": "w1", "labels": {"site": "office"}, "max_concurrency": 2}

    prepared = registration_config(config, {CONSOLE_URL_ENV: "http://127.0.0.1:8799"})

    assert prepared["labels"] == {"site": "office", CONSOLE_URL_LABEL: "http://127.0.0.1:8799"}
    assert prepared["worker_id"] == "w1" and prepared["max_concurrency"] == 2
    assert config["labels"] == {"site": "office"}
    # env 缺失 / 空串：labels 原样（旧部署行为不变）。
    assert registration_config(config, {})["labels"] == {"site": "office"}
    assert registration_config({"worker_id": "w1"}, {})["labels"] == {}


@pytest.mark.parametrize("count", [31, 32])
@pytest.mark.parametrize("existing_console", [False, True])
@pytest.mark.parametrize("url_length", [256, 257])
def test_optional_console_label_never_breaks_valid_registration(
    count, existing_console, url_length
):
    from server.app.agent_control.declarations import normalize_labels as host_labels
    from worker.worker_declarations import normalize_labels as worker_labels

    labels = {f"tag_{i}": "custom" for i in range(count - int(existing_console))}
    if existing_console:
        labels[CONSOLE_URL_LABEL] = "existing-scheduler-label"
    original = dict(labels)
    url = "https://worker.example/".ljust(url_length, "x")
    result = registration_config({"labels": labels}, {CONSOLE_URL_ENV: url})["labels"]
    assert host_labels(result) == worker_labels(result) == result
    assert labels == original
    from server.app.agent_broker.claim_scan import labels_satisfy

    assert labels_satisfy(result, original), "console metadata must preserve scheduling eligibility"
    if url_length <= 256 and not existing_console and count < 32:
        assert result == {**original, CONSOLE_URL_LABEL: url}
    else:
        assert result == original
