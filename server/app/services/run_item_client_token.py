"""Item-level ``client_token`` idempotency key (#813).

A run item's identity is content-addressed: the candidate ``entity_id``
(material id, bundle id, ``connection_key:external_id``) derives the dedup
key, the job id and — through the verbatim items — the run digest. A text
item normalizes to a content-addressed material, so identical text always
lands on one job. ``client_token`` lets a caller ask for several
independent jobs over the same content: the token joins the candidate
identity as ``{entity_id}~{token}``, so

- same content + same token → same job id (idempotent retry);
- same content + different tokens → independent jobs;
- no token → the entity id is untouched, so job ids and run digests of
  token-less submissions stay byte-identical to the pre-#813 derivation.

The run digest needs no extra handling: it hashes the submitted items
verbatim (the route dumps ``exclude_unset``), so a token is in the digest
exactly when it was submitted.

Only ``material`` / ``bundle`` / ``text`` items accept a token. A ``ref``
item already carries a caller-controlled namespace (``external_id``), and
its free-form id could otherwise collide with the ``~`` separator; the
contract model forbids the field (422) and the resolver rejects it (400)
for direct service callers. The token charset excludes ``~`` and ``/``
(job ids appear in URL paths and storage dirs), and material/bundle ids are
server-generated hex, so ``{entity_id}~{token}`` parses unambiguously.
"""

from __future__ import annotations

import re
from typing import Any

from server.app.services.job_errors import InvalidOperationError

# Job ids double as storage dir names (≤255 bytes): workspace (≤64) + "_" +
# workflow key (≤64) + "_" + 32-hex material/bundle id + "~" + token stays
# well inside the limit at 64 token chars.
CLIENT_TOKEN_MAX_CHARS = 64
CLIENT_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
CLIENT_TOKEN_SEPARATOR = "~"
CLIENT_TOKEN_ITEM_TYPES = ("material", "bundle", "text")

_TOKEN_RE = re.compile(CLIENT_TOKEN_PATTERN)


def item_client_token(item: dict[str, Any]) -> str:
    """The item's validated token; ``""`` when the item carries none.

    Mirrors the contract model so direct service callers get the same
    verdict as the HTTP route (400 here, 422 at the contract layer).
    """
    if "client_token" not in item or item["client_token"] is None:
        return ""
    if item.get("type") not in CLIENT_TOKEN_ITEM_TYPES:
        raise InvalidOperationError(
            f"client_token is not supported on {item.get('type')!r} items"
            " (ref items are already namespaced by connection_key:external_id)"
        )
    token = item["client_token"]
    if (
        not isinstance(token, str)
        or not token
        or len(token) > CLIENT_TOKEN_MAX_CHARS
        or not _TOKEN_RE.fullmatch(token)
    ):
        raise InvalidOperationError(
            f"client_token must be 1-{CLIENT_TOKEN_MAX_CHARS} characters of"
            " [A-Za-z0-9._-] starting with a letter or digit"
        )
    return token


def scoped_entity_id(entity_id: str, item: dict[str, Any]) -> str:
    """``entity_id`` scoped by the item's token (unchanged without one)."""
    token = item_client_token(item)
    return f"{entity_id}{CLIENT_TOKEN_SEPARATOR}{token}" if token else entity_id


def drop_null_client_tokens(items: list[Any]) -> list[Any]:
    """Items with an explicit ``"client_token": null`` stripped to the omitted form.

    The route dumps ``exclude_unset``, so a generated client sending ``null``
    would otherwise keep the key in the verbatim-hashed run digest: same job
    identity as the omitted field but a different run id, which breaks the
    #501 duplicate-heal path across null/omitted retries (PR #902 review).
    """
    return [
        {key: value for key, value in item.items() if key != "client_token"}
        if isinstance(item, dict) and "client_token" in item and item["client_token"] is None
        else item
        for item in items
    ]
