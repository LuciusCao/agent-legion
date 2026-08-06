#!/usr/bin/env python3
"""Structural diff of Node Pi vs velites ``events.jsonl`` stdout archives.

Compares event-type sequences and per-event field structure (keys, usage /
stopReason / provider / model / content shapes). LLM text content is NOT
compared verbatim; only structure must match. Timing fields (timestamp,
duration, pid, ...) are ignored. Pi's streaming delta events
(``message_update`` / ``tool_execution_update``) are stripped before
comparison because velites deliberately does not emit them — that absence
is the expected difference, not an error.

Exit code: 0 when the streams are structurally equivalent, 1 on substantive
differences, 2 on usage errors (argparse).

velites schema v2 note: ``turn_end`` carries only ``turnIndex`` and
``agent_end`` no longer carries ``messages`` (Pi re-serializes both); the
per-event field diff therefore reports Pi's extra payloads as informational
pi-only fields, not as missing velites data.

Usage:
    uv run python scripts/velites_diff_events.py PI_EVENTS.jsonl VELITES_EVENTS.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Pi-only streaming delta event types: expected to be absent from velites.
DELTA_EVENT_TYPES = frozenset({"message_update", "tool_execution_update"})

# Keys whose values (or presence) are timing/runtime specific and never
# meaningful for the structural comparison.
TIMING_KEYS = frozenset(
    {"timestamp", "duration", "durationMs", "duration_ms", "elapsed", "elapsedMs", "pid"}
)

# Fields Agent Legion consumers depend on (token_usage.py, shared/pi_model_error.py).
ASSISTANT_REQUIRED = [
    "message.usage.input",
    "message.usage.output",
    "message.usage.cacheRead",
    "message.stopReason",
    "message.provider",
    "message.model",
    "message.content",
]

# Required top-level fields per non-message event type.
EVENT_REQUIRED = {
    "tool_execution_start": ["toolCallId", "toolName"],
    "tool_execution_end": ["toolCallId", "toolName", "isError", "result.content"],
}

# message_start carries only the assistant skeleton (role/provider/model
# known, content empty); usage / stopReason only exist on final snapshots.
MESSAGE_START_REQUIRED = ["message.provider", "message.model", "message.content"]


def load(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def shape(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: shape(v) for k, v in sorted(obj.items()) if k not in TIMING_KEYS}
    if isinstance(obj, list):
        if not obj:
            return ["<empty>"]
        return [shape(obj[0])]
    return type(obj).__name__


def flat(shape_obj: Any, prefix: str = "") -> set[str]:
    out: set[str] = set()
    if isinstance(shape_obj, dict):
        for k, v in shape_obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out.add(p)
            out |= flat(v, p)
    elif isinstance(shape_obj, list):
        out |= flat(shape_obj[0], prefix + "[]")
    else:
        out.add(f"{prefix}<{shape_obj}>")
    return out


def key_seq(events: list[dict[str, Any]]) -> list[str]:
    return [str(e.get("type")) for e in events]


def compress(seq: list[str]) -> list[str]:
    out: list[str] = []
    for t in seq:
        if not out or out[-1] != t:
            out.append(t)
    return out


def pick(
    events: list[dict[str, Any]], etype: str, role: str | None = None
) -> dict[str, Any] | None:
    for e in events:
        if e.get("type") != etype:
            continue
        if role is not None:
            m = e.get("message")
            if not isinstance(m, dict) or m.get("role") != role:
                continue
        return e
    return None


def diff_fields(
    label: str,
    pi_ev: dict[str, Any] | None,
    velites_ev: dict[str, Any] | None,
    required: list[str],
) -> bool:
    if pi_ev is None and velites_ev is None:
        return True
    if pi_ev is None:
        print(f"[{label}] velites-only event variant (informational)")
        return True
    if velites_ev is None:
        print(f"[{label}] MISSING in velites")
        return False
    fp, fv = flat(shape(pi_ev)), flat(shape(velites_ev))
    only_pi, only_velites = sorted(fp - fv), sorted(fv - fp)
    missing_req = [p for p in required if p not in fv]
    ok = not missing_req
    print(f"[{label}] {'OK' if ok else 'REQUIRED-MISSING'}")
    if only_pi:
        print("  pi-only:", only_pi)
    if only_velites:
        print("  velites-only:", only_velites)
    if missing_req:
        print("  !! required fields missing in velites:", missing_req)
    return ok


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Structural diff of Node Pi vs velites events.jsonl streams. "
            "Ignores timing fields and Pi-only delta events. "
            "Exit 0 = structurally equivalent, 1 = substantive differences."
        ),
    )
    parser.add_argument("pi_events", type=Path, help="events.jsonl from a Node Pi run")
    parser.add_argument("velites_events", type=Path, help="events.jsonl from a velites run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pi = [e for e in load(args.pi_events) if e.get("type") not in DELTA_EVENT_TYPES]
    velites = load(args.velites_events)

    print("== event counts (pi deltas stripped) ==")
    print("pi:", len(pi), dict(Counter(key_seq(pi))))
    print("velites:", len(velites), dict(Counter(key_seq(velites))))

    print("\n== compressed event-type sequence ==")
    cp, cv = compress(key_seq(pi)), compress(key_seq(velites))
    print("pi:", cp)
    print("velites:", cv)
    seq_ok = cp == cv
    print(f"sequence match={seq_ok}")

    print("\n== per-event structural field diff ==")
    ok = True
    event_types = (set(key_seq(pi)) | set(key_seq(velites))) - DELTA_EVENT_TYPES
    for etype in sorted(event_types):
        if etype in ("message_start", "message_end"):
            required = MESSAGE_START_REQUIRED if etype == "message_start" else ASSISTANT_REQUIRED
            ok &= diff_fields(
                f"{etype}/assistant",
                pick(pi, etype, "assistant"),
                pick(velites, etype, "assistant"),
                required,
            )
            ok &= diff_fields(
                f"{etype}/user",
                pick(pi, etype, "user"),
                pick(velites, etype, "user"),
                [],
            )
        else:
            ok &= diff_fields(
                etype,
                pick(pi, etype),
                pick(velites, etype),
                EVENT_REQUIRED.get(etype, []),
            )

    print("\n== usage / stopReason sanity ==")
    for name, evs in (("pi", pi), ("velites", velites)):
        e = pick(evs, "message_end", "assistant")
        if e:
            m = e["message"]
            u = m.get("usage") or {}
            print(
                f"{name}: stopReason={m.get('stopReason')} "
                f"provider={m.get('provider')} model={m.get('model')} "
                f"usage.in={u.get('input')} out={u.get('output')} "
                f"cacheRead={u.get('cacheRead')} err={m.get('errorMessage')!r}"
            )

    verdict = seq_ok and ok
    print("\nRESULT:", "PASS" if verdict else "DIFF-FOUND")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
