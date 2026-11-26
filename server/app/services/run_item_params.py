"""Bounded non-secret params on material/bundle run items (#1565).

Params are terminal business input, not node configuration: validation knows
only JSON shape, resource limits and the non-secret boundary. Canonical JSON
is used for size accounting and idempotency comparison; the submitted mapping
itself is never normalized or interpreted. Release provenance and expansion
planning are deliberately outside this module.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from server.app.db.rowmap import parse_object
from server.app.jobs.job_id_encoding import encode_resource_job_id
from server.app.services.job_errors import InvalidOperationError
from server.app.services.run_item_client_token import (
    item_client_token,
    split_scoped_source_id,
)

RUN_ITEM_PARAMS_MAX_BYTES = 16 * 1024
RUN_ITEM_PARAMS_MAX_DEPTH = 4
RUN_ITEM_PARAMS_MAX_KEYS = 64
RUN_ITEM_PARAMS_MAX_KEY_CHARS = 64
RUN_ITEM_PARAMS_MAX_STRING_BYTES = 2 * 1024
RUN_ITEM_PARAMS_MAX_ARRAY_ITEMS = 64
_PARAMS_ITEM_TYPES = ("material", "bundle")
_SECRET_PARAM_KEYS = frozenset(
    {
        "secret",
        "secretref",
        "secretset",
        "secretkey",
        "apisecret",
        "password",
        "passwd",
        "pwd",
        "token",
        "authtoken",
        "accesstoken",
        "refreshtoken",
        "sessiontoken",
        "apikey",
        "accesskey",
        "clientsecret",
        "credential",
        "credentials",
        "privatekey",
    }
)


def input_with_params(item: dict[str, Any], item_type: str, entity_id: str) -> dict[str, Any]:
    """Terminal input with params only when the caller explicitly supplied them."""
    document: dict[str, Any] = {"type": item_type, f"{item_type}_id": entity_id}
    if "params" in item:
        document["params"] = item["params"]
    return document


def canonical_item_params(params: Any) -> str:
    """Canonical JSON used only for size accounting and idempotency comparison."""
    return json.dumps(
        params,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_item_params(params: Any) -> dict[str, Any]:
    """Return an unchanged, bounded non-secret JSON object for material/bundle items."""
    if not isinstance(params, dict):
        raise InvalidOperationError("material/bundle item params must be an object")
    key_count = 0
    stack: list[tuple[Any, int, str]] = [(params, 1, "params")]
    while stack:
        value, depth, path = stack.pop()
        if depth > RUN_ITEM_PARAMS_MAX_DEPTH:
            raise InvalidOperationError(
                f"item params exceed maximum depth {RUN_ITEM_PARAMS_MAX_DEPTH} at {path}"
            )
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or not key:
                    raise InvalidOperationError("item params keys must be non-empty strings")
                if len(key) > RUN_ITEM_PARAMS_MAX_KEY_CHARS:
                    raise InvalidOperationError(
                        f"item params keys must be at most {RUN_ITEM_PARAMS_MAX_KEY_CHARS} characters"
                    )
                normalized_key = "".join(char for char in key.casefold() if char.isalnum())
                if normalized_key in _SECRET_PARAM_KEYS:
                    raise InvalidOperationError(
                        "item params must be non-secret; credential fields and vault markers "
                        f"are not allowed ({key!r})"
                    )
                try:
                    key.encode("utf-8")
                except UnicodeError as exc:
                    raise InvalidOperationError("item params keys must be valid UTF-8") from exc
                key_count += 1
                if key_count > RUN_ITEM_PARAMS_MAX_KEYS:
                    raise InvalidOperationError(
                        f"item params exceed maximum total key count {RUN_ITEM_PARAMS_MAX_KEYS}"
                    )
                stack.append((child, depth + 1, f"{path}.{key}"))
        elif isinstance(value, list):
            if len(value) > RUN_ITEM_PARAMS_MAX_ARRAY_ITEMS:
                raise InvalidOperationError(
                    "item params arrays must contain at most "
                    f"{RUN_ITEM_PARAMS_MAX_ARRAY_ITEMS} entries ({path})"
                )
            stack.extend(
                (child, depth + 1, f"{path}[{index}]") for index, child in enumerate(value)
            )
        elif isinstance(value, str):
            try:
                size = len(value.encode("utf-8"))
            except UnicodeError as exc:
                raise InvalidOperationError("item params strings must be valid UTF-8") from exc
            if size > RUN_ITEM_PARAMS_MAX_STRING_BYTES:
                raise InvalidOperationError(
                    "item params strings must be at most "
                    f"{RUN_ITEM_PARAMS_MAX_STRING_BYTES} UTF-8 bytes ({path})"
                )
        elif value is None or isinstance(value, bool | int):
            continue
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise InvalidOperationError("item params numbers must be finite JSON numbers")
        else:
            raise InvalidOperationError(
                f"item params values must be JSON values ({path}: {type(value).__name__})"
            )
    try:
        size = len(canonical_item_params(params).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidOperationError("item params must be serializable as standard JSON") from exc
    if size > RUN_ITEM_PARAMS_MAX_BYTES:
        raise InvalidOperationError(
            f"item params exceed {RUN_ITEM_PARAMS_MAX_BYTES} canonical JSON UTF-8 bytes ({size})"
        )
    return params


def validate_run_item_params(items: list[Any]) -> None:
    """Fail fast on invalid params and same-token param drift within one submission."""
    fingerprints: dict[tuple[str, str, str], str] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("type") not in _PARAMS_ITEM_TYPES:
            continue
        item_type = str(item["type"])
        params = validate_item_params(item.get("params", {}))
        token = item_client_token(item)
        if not token:
            continue
        entity_id = str(item.get(f"{item_type}_id") or "").strip()
        identity = (item_type, entity_id, token)
        fingerprint = canonical_item_params(params)
        if identity in fingerprints and fingerprints[identity] != fingerprint:
            raise InvalidOperationError(
                f"client_token {token!r} is reused with different params for "
                f"{item_type} {entity_id!r}"
            )
        fingerprints[identity] = fingerprint


def check_token_params(
    job_db: Any,
    workspace_id: str,
    candidates: list[dict[str, Any]],
    resource_pins_hash: str = "",
    existing_keys: set[tuple[str, str]] | None = None,
) -> None:
    """Reject a token retry whose frozen params differ, without logging param values."""
    tokenized = []
    for candidate in candidates:
        source_type = str(candidate.get("entity_type") or "")
        source_id = str(candidate.get("entity_id") or "")
        entity_id, token = split_scoped_source_id(source_type, source_id)
        if token is not None:
            tokenized.append((candidate, source_type, source_id, entity_id, token))
    if not tokenized:
        return
    if existing_keys is None:
        existing_keys = job_db.filter_existing_dedup_keys(
            workspace_id,
            ((source_type, source_id) for _, source_type, source_id, _, _ in tokenized),
            resource_pins_hash,
        )
    tokenized = [entry for entry in tokenized if (entry[1], entry[2]) in existing_keys]
    if not tokenized:
        return

    ids_to_fetch: list[str] = []
    for _, _, source_id, _, _ in tokenized:
        ids_to_fetch.append(
            encode_resource_job_id(workspace_id, workspace_id, source_id, resource_pins_hash)
        )
        if not resource_pins_hash:
            ids_to_fetch.append(f"{workspace_id}_{workspace_id}_{source_id.replace('/', '_')}")
    rows = job_db.fetch_jobs_by_ids(list(dict.fromkeys(ids_to_fetch)))
    by_id = {str(row["id"]): row for row in rows}
    for candidate, source_type, source_id, entity_id, token in tokenized:
        job_ids = [
            encode_resource_job_id(workspace_id, workspace_id, source_id, resource_pins_hash)
        ]
        if not resource_pins_hash:
            job_ids.append(f"{workspace_id}_{workspace_id}_{source_id.replace('/', '_')}")
        for job_id in job_ids:
            row = by_id.get(job_id)
            if row is None or str(row.get("workspace_id")) != workspace_id:
                continue
            if (str(row.get("source_type")), str(row.get("source_id"))) != (
                source_type,
                source_id,
            ):
                continue
            stored_input = parse_object(row.get("input_json"))
            candidate_input = candidate.get("input")
            submitted_params = (
                candidate_input.get("params", {}) if isinstance(candidate_input, Mapping) else {}
            )
            if canonical_item_params(stored_input.get("params", {})) != canonical_item_params(
                submitted_params
            ):
                raise InvalidOperationError(
                    f"client_token {token!r} was already used with different params for "
                    f"{source_type} {entity_id!r}"
                )
