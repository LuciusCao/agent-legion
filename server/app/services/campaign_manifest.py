"""Campaign manifest normalization (#532 PR-A / #505 server-side port).

The CLI-era ``scripts/submit_campaign.py`` (issue #505) parsed jsonl/csv
manifests client-side; the productized campaign API accepts the same files as
multipart uploads and normalizes them server-side at creation time. The
normalize/load logic below is the CLI's port (behavior contract pinned by the
ported test files, tests/services/test_campaign_manifest_*.py):

- jsonl: one item per line, blank/``#``-comment lines skipped, errors
  located to file:line;
- csv: rows to items via DictReader, blank rows skipped, and — the mixed-type
  header rule — empty cells are DROPPED before normalization (a CSV empty
  cell can only mean "field absent": the POST /runs contracts are
  extra="forbid" with min_length=1 required fields, so an explicit empty
  string column would 422; the default (e.g. ref.params {}) applies only
  when the key is absent, codex #531 P2-2);
- every item: string values stripped, unknown/missing types rejected, the
  per-type required fields checked, ref items get ``params`` defaulted to
  ``{}``.

The service layer serializes the normalized list to canonical jsonl (one
json.dumps per line) for storage (inline in the campaign row or the object
store); ``parse_manifest_text`` accepts that canonical form back. The CSV
``params`` column limitation (a CSV cell is a string; params is only
expressible in jsonl) is the documented CLI contract (runbook §2).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

_VALID_ITEM_TYPES = frozenset({"material", "bundle", "ref"})
_ITEM_REQUIRED_FIELDS = {
    "material": ("material_id",),
    "bundle": ("bundle_id",),
    "ref": ("connection_key", "external_id"),
}


class ManifestError(ValueError):
    """A manifest the operator can fix by hand (bad type / missing field /
    unparsable line / empty manifest). Routes map it to 422."""


def normalize_item(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Normalize one manifest line into the POST /runs item contract.

    ref's absent ``params`` defaults to ``{}`` (the RunItemRef contract
    default); other fields pass through verbatim (RunCreateRequest is
    extra="forbid", so the server's own 422 is the rejection authority —
    duplicating field pruning here would drift). CSV sources additionally
    str-ify (csv is untyped). Ported from scripts/submit_campaign.py
    (issue #505) with UsageError renamed ManifestError.
    """
    if not isinstance(raw, dict):
        raise ManifestError(f"{source}: item 必须是 JSON object，收到 {type(raw).__name__}")
    item_type = raw.get("type")
    if item_type not in _VALID_ITEM_TYPES:
        raise ManifestError(
            f"{source}: 不支持的 item type {item_type!r}（支持 {_VALID_ITEM_TYPES}）"
        )
    item: dict[str, Any] = {}
    for key, value in raw.items():
        item[str(key)] = value.strip() if isinstance(value, str) else value
    missing = [
        field for field in _ITEM_REQUIRED_FIELDS[str(item_type)] if not str(item.get(field) or "")
    ]
    if missing:
        raise ManifestError(f"{source}: {item_type} item 缺少必填字段 {missing}")
    if item_type == "ref" and "params" not in item:
        item["params"] = {}
    return item


def load_items_text(text: str, *, filename: str) -> list[dict[str, Any]]:
    """Parse manifest text (.jsonl / .csv by filename suffix), order-preserving.

    The server-side twin of the CLI's ``load_items(path)``: same acceptance
    rules, same error locations (filename:line / filename:row), reading from
    an in-memory string instead of a file path (the multipart upload's bytes
    decode once in the route, no temp file materialized).
    """
    if filename.lower().endswith(".csv"):
        return _load_csv(text, filename=filename)
    return _load_jsonl(text, filename=filename)


def _load_jsonl(text: str, *, filename: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"{filename}:{line_number}: JSON 解析失败: {exc}") from exc
        items.append(normalize_item(raw, source=f"{filename}:{line_number}"))
    if not items:
        raise ManifestError(f"{filename}: 清单没有可用 item")
    return items


def _load_csv(text: str, *, filename: str) -> list[dict[str, Any]]:
    # Excel-style BOM: the CLI's open(encoding="utf-8-sig") strip moves here
    # (callers hand us already-decoded text); a residual BOM would corrupt the
    # first header cell (\ufefftype instead of type).
    if text.startswith("\ufeff"):
        text = text[1:]
    items: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text))
    for row_number, row in enumerate(reader, start=2):
        if all(value in (None, "") for value in row.values()):
            continue
        # Mixed headers (material/bundle/ref sharing one sheet) attach the
        # other types' empty columns to every row (a material row carries
        # bundle_id=""). An empty cell means "field absent" (the contract
        # default applies); drop empty values — including the None of
        # short-row trailing columns — before normalize_item.
        cleaned = {key: value for key, value in row.items() if value not in (None, "")}
        items.append(normalize_item(cleaned, source=f"{filename}:{row_number}"))
    if not items:
        raise ManifestError(f"{filename}: 清单没有可用 item")
    return items


def serialize_manifest(items: list[dict[str, Any]]) -> str:
    """Canonical jsonl form: one sorted-keys json object per line.

    The stored object (inline row spec or the object-store manifest) is
    always this normalized serialization — the feeder re-parses with
    ``parse_manifest_text`` and gets the exact creation-time list back.
    """
    return "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items)


def parse_manifest_text(text: str, *, filename: str = "manifest.jsonl") -> list[dict[str, Any]]:
    """Parse the canonical jsonl form (round-trips serialize_manifest)."""
    return _load_jsonl(text, filename=filename)
