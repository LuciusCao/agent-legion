from __future__ import annotations

from pathlib import Path

MAX_COPIED_LOG_BYTES = 20 * 1024 * 1024


def copy_pi_logs(run_dir: Path, log_path: Path, max_bytes: int = MAX_COPIED_LOG_BYTES) -> None:
    """Copy the Pi runner's event and stderr logs to the context log_path.

    The combined output is capped at ``max_bytes`` to keep per-run log files
    from growing without bound when the Pi event stream is very large.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[bytes] = []
    for name in ("events.jsonl", "stderr.log"):
        src = run_dir / name
        if src.is_file():
            parts.append(src.read_bytes())

    if not parts:
        return

    combined = b"\n".join(parts)
    if len(combined) > max_bytes:
        header = f"... (log truncated to last {max_bytes} bytes)\n".encode()
        combined = header + combined[-max_bytes:]

    log_path.write_bytes(combined)
