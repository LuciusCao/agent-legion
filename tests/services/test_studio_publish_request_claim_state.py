"""Pure state predicates for publish-request confirm-claim liveness."""

from datetime import UTC, datetime, timedelta

import pytest

from server.app.services.studio_publish_request_heartbeat import (
    CONFIRMING_STALE_SECONDS,
    is_stale_claim,
)

pytestmark = pytest.mark.no_db


def test_stale_claim_requires_an_old_claim_timestamp() -> None:
    now = datetime.now(UTC)

    assert is_stale_claim({"status": "confirming", "claimed_at": None}) is False
    assert is_stale_claim({"claimed_at": now.isoformat()}) is False
    assert (
        is_stale_claim(
            {"claimed_at": (now - timedelta(seconds=CONFIRMING_STALE_SECONDS + 1)).isoformat()}
        )
        is True
    )
