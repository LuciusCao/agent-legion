from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

from server.app.routes.agent_worker_result_logging import (
    log_result_rejection,
    safe_result_rejection_detail,
)

pytestmark = pytest.mark.no_db


def test_safe_detail_keeps_protocol_reason_but_drops_dynamic_text() -> None:
    assert safe_result_rejection_detail("invalid Agent result metadata") == (
        "invalid Agent result metadata"
    )
    assert safe_result_rejection_detail("invalid Agent result metadata: invalid status") == (
        "invalid Agent result metadata: invalid status"
    )
    assert (
        safe_result_rejection_detail(
            "invalid Agent result metadata: result archive is unreadable: token=sk-secret"
        )
        == "invalid Agent result metadata: result archive is unreadable"
    )


def test_unknown_or_unbounded_detail_never_reaches_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sk-live-credential-that-must-not-leak"
    detail = f"invalid Agent result metadata: unrecognized {secret} " + "x" * 100_000

    with caplog.at_level(logging.WARNING):
        log_result_rejection("exec-secret", HTTPException(status_code=400, detail=detail))

    message = caplog.records[-1].getMessage()
    assert secret not in message
    assert len(message) < 200
    assert message.endswith("detail=invalid Agent result metadata")


def test_non_400_rejection_is_not_mislabeled(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        log_result_rejection("exec-1", HTTPException(status_code=409, detail="lease lost"))
    assert caplog.records == []
