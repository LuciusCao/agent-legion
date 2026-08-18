"""Schema v44: legacy ``cms_hmac`` connections retype to ``hmac_token``."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def migrate_hmac_connection_type(conn: Any) -> None:
    """Retype v34-created ``cms_hmac`` connections to the platform built-in.

    Schema v34 (frozen) wrote CMS token_gen credentials with connection type
    ``cms_hmac``, whose adapter lived in the stripped workspace pack; the
    built-in that replaces it registers as ``hmac_token`` (issue #97). Only
    the type string changes: the stored config/secret refs stay untouched
    because the built-in reads the same keys (app_id / nonce / token_url +
    vault secret) and speaks the same wire protocol. Idempotent on replay.
    """
    conn.execute("update external_connections set type='hmac_token' where type='cms_hmac'")
    logger.info("hmac connection type migration: retyped cms_hmac rows to hmac_token")
