"""Live filter for the Pi ``--mode json`` stdout stream.

Pi emits one JSON event per line; the ``message_update`` token deltas make
up over 99% of the volume (hundreds of MB per execution) and are discarded
by ``compress_pi_events`` at upload time anyway. Filtering them as the
stream arrives keeps events.jsonl near 1% of its former size, so post-run
scans, archiving, and uploads stay cheap regardless of run length.

The filter is a denylist, not the renderer's allowlist: only known
high-volume delta types are dropped, everything else — including type-less
assistant error messages and non-JSON stderr text — passes through. The
upload-time compression still applies the render allowlist, so archived
content is unchanged either way.
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import BinaryIO

# High-volume streaming delta events; the final state is always captured in
# the corresponding *_end event, so dropping the deltas loses nothing.
DROP_EVENT_TYPES = frozenset({"message_update", "tool_execution_update"})

# Fast path: Pi's serializer always writes the type first, so delta spam is
# dropped without a JSON parse. Lines that do not match fall through to the
# parse below — a reordered key only costs a parse, never a wrong decision.
_DELTA_PREFIX = b'{"type":"message_update"'


def pump_filtered_events(src: BinaryIO | None, dst: BinaryIO) -> None:
    """Copy Pi stdout lines from ``src`` to ``dst``, dropping delta spam.

    Runs in a daemon thread for the life of the Pi process; returns at EOF
    (process exit) and tolerates the destination closing early during
    Worker shutdown.
    """
    if src is None:
        return
    try:
        for raw in src:
            if raw.startswith(_DELTA_PREFIX):
                continue
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                dst.write(raw)  # stderr text, crash traces, partial lines
                continue
            if isinstance(event, dict) and event.get("type") in DROP_EVENT_TYPES:
                continue
            dst.write(raw)
    except (OSError, ValueError):
        # Destination closed underneath us during shutdown; the process is
        # gone either way and the events file is complete enough to upload.
        pass


def spawn_event_pump(proc: subprocess.Popen[bytes], dst: BinaryIO, name: str) -> threading.Thread:
    """Start the daemon thread pumping ``proc.stdout`` through the delta filter."""
    pump = threading.Thread(
        target=pump_filtered_events, args=(proc.stdout, dst), name=name, daemon=True
    )
    pump.start()
    return pump
