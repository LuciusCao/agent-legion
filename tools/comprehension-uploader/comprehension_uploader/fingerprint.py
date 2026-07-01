from __future__ import annotations

import hashlib
import json
from typing import Any


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _normalize_option(option: Any) -> dict[str, str]:
    if not isinstance(option, dict):
        return {"label": "", "text": ""}
    label = str(option.get("label", "")).strip()
    text = str(option.get("text", "")).strip()
    return {"label": label, "text": text}


def compute_question_fingerprint(stem: str, options: list[Any]) -> str | None:
    """Return a deterministic MD5 fingerprint for a question stem + options.

    The payload is normalized before hashing:
    - stem: leading/trailing whitespace stripped, internal whitespace collapsed.
    - options: only label + text are kept, each stripped, sorted by label.

    Returns None when both stem and all option texts are empty.
    """
    normalized_stem = _collapse_whitespace(stem).strip()
    normalized_options = sorted(
        (_normalize_option(opt) for opt in options),
        key=lambda opt: opt["label"],
    )
    if not normalized_stem and not any(opt["text"] for opt in normalized_options):
        return None
    payload = json.dumps(
        {"stem": normalized_stem, "options": normalized_options},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()
